from __future__ import annotations

import base64
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput

_PROBE_SCRIPT = """
import base64, glob, json, os, sys
payload = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
matches = []
for spec in payload.get("jobs", []):
    for pattern in spec.get("patterns", []):
        try:
            for path in glob.glob(os.path.expanduser(pattern), recursive=True):
                if not os.path.isfile(path):
                    continue
                lower = path.lower()
                if not (lower.endswith('.json') or lower.endswith('.jsonl')):
                    continue
                matches.append(path)
        except Exception:
            pass
print(json.dumps({"matches": len(sorted(set(matches)))}))
"""

_REMOTE_PARSE_TIME_HELPER = """
def _normalize_iso_datetime_text(text):
    text = text.strip()
    text = text.replace(',', '.', 1)
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    match = re.search(r'([+-]\\d{2})$', text)
    if match:
        text = text + ':00'
    match = re.search(r'([+-]\\d{2}):(\\d{2})$', text)
    if match:
        text = text[:match.start()] + match.group(1) + match.group(2)
    return text

def parse_iso_datetime(text):
    normalized = _normalize_iso_datetime_text(text)
    formats = (
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M%z',
        '%Y-%m-%d %H:%M:%S.%f%z',
        '%Y-%m-%d %H:%M:%S%z',
        '%Y-%m-%d %H:%M%z',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None
"""

_COLLECT_SCRIPT = (
    """
import base64, glob, json, os, re, sys
from datetime import datetime, timezone

payload = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
jobs = payload.get("jobs", [])
start_ts = float(payload.get("start_ts", 0))
end_ts = float(payload.get("end_ts", 0))
max_files = int(payload.get("max_files", 0) or 0)
max_total_bytes = int(payload.get("max_total_bytes", 0) or 0)

def log(message):
    print(message, file=sys.stderr)

log("info: remote script started jobs=" + str(len(jobs)))

def coerce_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0

__REMOTE_PARSE_TIME_HELPER__

def parse_time(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10000000000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 10000000000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return parse_iso_datetime(text)
    return None

def walk_json_nodes(obj, parent_key=None):
    if isinstance(obj, dict):
        yield obj, parent_key
        for key, value in obj.items():
            for item in walk_json_nodes(value, key):
                yield item
    elif isinstance(obj, list):
        for item in obj:
            for node in walk_json_nodes(item, parent_key):
                yield node

def extract_model(node):
    for key in ("model", "model_name", "modelName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"

def extract_time(node):
    for key in ("timestamp", "created_at", "createdAt", "time", "date"):
        parsed = parse_time(node.get(key))
        if parsed is not None:
            return parsed
    return None

def extract_usage(node):
    usage = node.get("usage") if isinstance(node.get("usage"), dict) else node
    input_tokens = coerce_int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("inputTokenCount"))
    output_tokens = coerce_int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("outputTokenCount"))
    cache_tokens = coerce_int(
        usage.get("cache_tokens") or usage.get("cached_tokens") or usage.get("cached_input_tokens")
    )
    if cache_tokens == 0:
        cache_tokens = coerce_int(usage.get("cache_read_input_tokens")) + coerce_int(
            usage.get("cache_creation_input_tokens")
        )
    return input_tokens, cache_tokens, output_tokens

def extract_codex_token_count_usage(node):
    if node.get("type") != "event_msg":
        return None
    payload = node.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    last_token_usage = info.get("last_token_usage")
    if not isinstance(last_token_usage, dict):
        return None
    cache_tokens = coerce_int(last_token_usage.get("cached_input_tokens"))
    input_tokens = max(0, coerce_int(last_token_usage.get("input_tokens")) - cache_tokens)
    output_tokens = coerce_int(last_token_usage.get("output_tokens"))
    return input_tokens, cache_tokens, output_tokens

def extract_codex_turn_model(node):
    if node.get("type") != "turn_context":
        return None
    payload = node.get("payload")
    if not isinstance(payload, dict):
        return None
    model = extract_model(payload)
    if model != "unknown":
        return model
    collaboration_mode = payload.get("collaboration_mode")
    if not isinstance(collaboration_mode, dict):
        return None
    settings = collaboration_mode.get("settings")
    if not isinstance(settings, dict):
        return None
    nested_model = extract_model(settings)
    return nested_model if nested_model != "unknown" else None

def build_session_fingerprint(path, tool_name):
    if tool_name == "codex":
        matches = re.findall(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            os.path.splitext(os.path.basename(path))[0],
        )
        if matches:
            return "codex:" + matches[-1].lower()
        stem = os.path.splitext(os.path.basename(path))[0]
        return "codex_file:" + stem
    if tool_name == "copilot_cli":
        session_dir = os.path.basename(os.path.dirname(path)).strip()
        if session_dir:
            return "copilot_cli:" + session_dir
        stem = os.path.splitext(os.path.basename(path))[0]
        return "copilot_cli_file:" + stem
    return None

def extract_copilot_cli_events(node, fallback_time, session_fingerprint, source_ref):
    if node.get("type") != "session.shutdown":
        return []
    data = node.get("data")
    if not isinstance(data, dict):
        return []
    model_metrics = data.get("modelMetrics")
    if not isinstance(model_metrics, dict):
        return []
    event_time = extract_time(node) or parse_time(data.get("sessionStartTime")) or fallback_time
    out = []
    prefix = session_fingerprint or "copilot_cli"
    for model_name, metrics in model_metrics.items():
        if not isinstance(model_name, str) or not model_name.strip() or not isinstance(metrics, dict):
            continue
        usage = metrics.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = coerce_int(usage.get("inputTokens"))
        output_tokens = coerce_int(usage.get("outputTokens"))
        cache_tokens = coerce_int(usage.get("cacheReadTokens")) + coerce_int(usage.get("cacheWriteTokens"))
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            continue
        out.append((event_time, model_name.strip(), input_tokens, cache_tokens, output_tokens, prefix + ":" + model_name.strip(), source_ref))
    return out

def normalize_copilot_model(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if text.startswith("copilot/"):
        text = text[len("copilot/"):]
    return text

def extract_copilot_vscode_usage(result):
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None
    input_tokens = 0
    output_tokens = 0
    cache_tokens = 0
    if usage is not None:
        input_tokens = coerce_int(usage.get("promptTokens") or usage.get("inputTokens") or usage.get("prompt_tokens"))
        output_tokens = coerce_int(usage.get("completionTokens") or usage.get("outputTokens") or usage.get("output_tokens"))
        cache_tokens = coerce_int(usage.get("cachedInputTokens") or usage.get("cacheReadTokens") or usage.get("cached_input_tokens"))
    if input_tokens == 0 and output_tokens == 0:
        input_tokens = coerce_int(result.get("promptTokens"))
        output_tokens = coerce_int(result.get("outputTokens") or result.get("completionTokens"))
    if input_tokens == 0 and output_tokens == 0 and metadata is not None:
        input_tokens = coerce_int(metadata.get("promptTokens") or metadata.get("inputTokens"))
        output_tokens = coerce_int(metadata.get("outputTokens") or metadata.get("completionTokens"))
        if cache_tokens == 0:
            cache_tokens = coerce_int(metadata.get("cachedInputTokens") or metadata.get("cacheReadTokens"))
    return input_tokens, cache_tokens, output_tokens

def estimate_tokens_from_text(text):
    content = text.strip() if isinstance(text, str) else ""
    if not content:
        return 0
    ascii_chars = sum(1 for ch in content if ord(ch) < 128)
    non_ascii_chars = len(content) - ascii_chars
    return max(1, int((ascii_chars * 0.25) + (non_ascii_chars * 0.6) + 0.999999))

def collect_copilot_text_parts(value):
    parts = []
    if isinstance(value, str):
        if value:
            parts.append(value)
        return parts
    if isinstance(value, list):
        for item in value:
            parts.extend(collect_copilot_text_parts(item))
        return parts
    if not isinstance(value, dict):
        return parts
    direct_text = value.get("text")
    if isinstance(direct_text, str) and direct_text:
        parts.append(direct_text)
    direct_value = value.get("value")
    if isinstance(direct_value, str) and direct_value:
        parts.append(direct_value)
    content = value.get("content")
    if isinstance(content, dict):
        content_value = content.get("value")
        if isinstance(content_value, str) and content_value:
            parts.append(content_value)
    if isinstance(value.get("parts"), list):
        for item in value.get("parts"):
            parts.extend(collect_copilot_text_parts(item))
    if isinstance(value.get("response"), list):
        for item in value.get("response"):
            parts.extend(collect_copilot_text_parts(item))
    return parts

def extract_copilot_vscode_model(session, request):
    selected_model = request.get("selectedModel")
    for value in (
        request.get("modelId"),
        request.get("model"),
        selected_model.get("identifier") if isinstance(selected_model, dict) else None,
    ):
        normalized = normalize_copilot_model(value)
        if normalized and normalized != "auto":
            return normalized
    agent = request.get("agent")
    if isinstance(agent, dict):
        normalized = normalize_copilot_model(agent.get("modelId"))
        if normalized and normalized != "auto":
            return normalized
    result = request.get("result")
    if isinstance(result, dict):
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in ("modelId", "model", "id"):
                normalized = normalize_copilot_model(metadata.get(key))
                if normalized and normalized != "auto":
                    return normalized
        details = result.get("details")
        if isinstance(details, str) and details.strip():
            return details.split("•", 1)[0].strip()
    input_state = session.get("inputState")
    if isinstance(input_state, dict):
        selected_model = input_state.get("selectedModel")
        if isinstance(selected_model, dict):
            metadata = selected_model.get("metadata")
            if isinstance(metadata, dict):
                for key in ("version", "name", "id"):
                    normalized = normalize_copilot_model(metadata.get(key))
                    if normalized:
                        return normalized
            normalized = normalize_copilot_model(selected_model.get("identifier"))
            if normalized:
                return normalized
    return "unknown"

def build_copilot_vscode_event(session, request, fallback_time, source_ref):
    session_id = session.get("sessionId")
    request_id = request.get("requestId")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    result = request.get("result")
    if not isinstance(result, dict):
        return None
    input_tokens, cache_tokens, output_tokens = extract_copilot_vscode_usage(result)
    if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
        input_text = "\\n".join(collect_copilot_text_parts(request.get("message")))
        output_text = "\\n".join(collect_copilot_text_parts(request.get("response")))
        if not output_text:
            output_text = "\\n".join(collect_copilot_text_parts(result))
        input_tokens = estimate_tokens_from_text(input_text)
        output_tokens = estimate_tokens_from_text(output_text)
        if input_tokens == 0 and output_tokens == 0:
            return None
    event_time = parse_time(request.get("timestamp")) or extract_time(result) or fallback_time
    return (
        event_time,
        extract_copilot_vscode_model(session, request),
        input_tokens,
        cache_tokens,
        output_tokens,
        "copilot_vscode:" + session_id.strip() + ":" + request_id.strip(),
        source_ref,
    )

def extract_copilot_vscode_events(node, fallback_time, source_ref):
    session = node.get("v") if node.get("kind") == 0 and isinstance(node.get("v"), dict) else node
    if not isinstance(session, dict):
        return []
    session_id = session.get("sessionId")
    requests = session.get("requests")
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(requests, list):
        return []
    out = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        item = build_copilot_vscode_event(session, request, fallback_time, source_ref)
        if item is not None:
            out.append(item)
    return out

def apply_copilot_delta(state, delta):
    kind = delta.get("kind")
    path = delta.get("k")
    value = delta.get("v")
    if kind == 0:
        if isinstance(value, (dict, list)):
            return value
        return state
    if not isinstance(path, list) or not path:
        return state
    root = state if isinstance(state, (dict, list)) else {}
    current = root
    parts = [str(part) for part in path]
    for idx, part in enumerate(parts[:-1]):
        next_part = parts[idx + 1]
        wants_list = next_part.isdigit()
        if isinstance(current, list):
            if not part.isdigit():
                return root
            part_index = int(part)
            while len(current) <= part_index:
                current.append([] if wants_list else {})
            if not isinstance(current[part_index], (dict, list)):
                current[part_index] = [] if wants_list else {}
            current = current[part_index]
            continue
        if not isinstance(current, dict):
            return root
        child = current.get(part)
        if not isinstance(child, (dict, list)):
            child = [] if wants_list else {}
            current[part] = child
        current = child
    last = parts[-1]
    if kind == 1:
        if isinstance(current, list):
            if not last.isdigit():
                return root
            last_index = int(last)
            while len(current) <= last_index:
                current.append(None)
            current[last_index] = value
            return root
        if isinstance(current, dict):
            current[last] = value
        return root
    if kind == 2:
        if isinstance(current, list):
            if not last.isdigit():
                return root
            last_index = int(last)
            while len(current) <= last_index:
                current.append([])
            if not isinstance(current[last_index], list):
                current[last_index] = []
            target = current[last_index]
        elif isinstance(current, dict):
            if not isinstance(current.get(last), list):
                current[last] = []
            target = current[last]
        else:
            return root
        if isinstance(value, list):
            target.extend(value)
        else:
            target.append(value)
    return root

def extract_copilot_vscode_events_from_jsonl_text(text, fallback_time, source_ref):
    state = {}
    saw_delta = False
    out = []
    idx = 0
    for raw_line in text.splitlines():
        idx += 1
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("kind"), int):
            saw_delta = True
            state = apply_copilot_delta(state, obj)
            continue
        for item in extract_copilot_vscode_events(obj, fallback_time, source_ref + ":" + str(idx)):
            out.append(item)
    if saw_delta and isinstance(state, dict):
        return extract_copilot_vscode_events(state, fallback_time, source_ref)
    return out

def append_event(out, event_time, model, input_tokens, cache_tokens, output_tokens, session_fingerprint, source_ref):
    if event_time is None:
        return
    ts = event_time.timestamp()
    if ts < start_ts or ts > end_ts:
        return
    out.append(
        {
            "tool": active_tool,
            "model": model,
            "event_time": event_time.isoformat(),
            "input_tokens": input_tokens,
            "cache_tokens": cache_tokens,
            "output_tokens": output_tokens,
            "session_fingerprint": session_fingerprint,
            "source_ref": source_ref,
        }
    )

events = []
warnings = []
seen = set()
processed_files = 0
total_bytes = 0
for spec in jobs:
    active_tool = spec.get("tool", "unknown")
    patterns = spec.get("patterns", [])
    for pattern in patterns:
        try:
            log("info: expanding pattern tool=" + active_tool + " pattern=" + pattern)
            for path in glob.glob(os.path.expanduser(pattern), recursive=True):
                dedupe_key = active_tool + "\\0" + path
                if dedupe_key in seen or not os.path.isfile(path):
                    continue
                lower = path.lower()
                if not (lower.endswith('.json') or lower.endswith('.jsonl')):
                    continue
                try:
                    stat = os.stat(path)
                except OSError as exc:
                    warnings.append(active_tool + ": failed stating " + path + ": " + str(exc))
                    continue
                if stat.st_mtime < start_ts:
                    continue
                if max_files > 0 and processed_files >= max_files:
                    warnings.append("stopped after reaching max_files=" + str(max_files))
                    print(json.dumps({"events": events, "warnings": warnings}))
                    raise SystemExit(0)
                file_size = int(stat.st_size)
                if max_total_bytes > 0 and total_bytes + file_size > max_total_bytes:
                    warnings.append("stopped after reaching max_total_bytes=" + str(max_total_bytes))
                    print(json.dumps({"events": events, "warnings": warnings}))
                    raise SystemExit(0)
                seen.add(dedupe_key)
                processed_files += 1
                total_bytes += file_size
                if processed_files == 1 or processed_files % 20 == 0:
                    log(
                        "info: processing file "
                        + str(processed_files)
                        + " tool="
                        + active_tool
                        + " size="
                        + str(file_size)
                        + " path="
                        + path
                    )
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        text = fh.read()
                    fallback_time = datetime.fromtimestamp(int(os.path.getmtime(path)), tz=timezone.utc)
                    session_fingerprint = build_session_fingerprint(path, active_tool)
                    codex_model_hint = None
                    if lower.endswith('.jsonl'):
                        if active_tool == "copilot_vscode":
                            for item in extract_copilot_vscode_events_from_jsonl_text(text, fallback_time, path):
                                append_event(events, *item)
                            continue
                        idx = 0
                        for raw_line in text.splitlines():
                            idx += 1
                            line = raw_line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except ValueError:
                                continue
                            if active_tool == "copilot_cli":
                                for item in extract_copilot_cli_events(
                                    obj,
                                    fallback_time,
                                    session_fingerprint,
                                    path + ":" + str(idx),
                                ):
                                    append_event(events, *item)
                                continue
                            if active_tool == "copilot_vscode":
                                for item in extract_copilot_vscode_events(
                                    obj,
                                    fallback_time,
                                    path + ":" + str(idx),
                                ):
                                    append_event(events, *item)
                                continue
                            if active_tool == "codex":
                                turn_model = extract_codex_turn_model(obj)
                                if turn_model:
                                    codex_model_hint = turn_model
                                usage = extract_codex_token_count_usage(obj)
                                if usage is None:
                                    continue
                                input_tokens, cache_tokens, output_tokens = usage
                                if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                    continue
                                event_time = extract_time(obj) or fallback_time
                                model = extract_model(obj)
                                if model == "unknown" and codex_model_hint:
                                    model = codex_model_hint
                                append_event(
                                    events,
                                    event_time,
                                    model,
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path + ":" + str(idx),
                                )
                                continue
                            local_seen = set()
                            for candidate, parent_key in walk_json_nodes(obj):
                                if parent_key == "usage":
                                    continue
                                input_tokens, cache_tokens, output_tokens = extract_usage(candidate)
                                if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                    continue
                                event_time = extract_time(candidate) or fallback_time
                                dedupe_key = (
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    event_time.isoformat(),
                                )
                                if dedupe_key in local_seen:
                                    continue
                                local_seen.add(dedupe_key)
                                append_event(
                                    events,
                                    event_time,
                                    extract_model(candidate),
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path + ":" + str(idx),
                                )
                    elif lower.endswith('.json'):
                        try:
                            obj = json.loads(text)
                        except ValueError:
                            warnings.append(active_tool + ": failed decoding " + path)
                            continue
                        if active_tool == "codex":
                            for candidate, _parent_key in walk_json_nodes(obj):
                                turn_model = extract_codex_turn_model(candidate)
                                if turn_model:
                                    codex_model_hint = turn_model
                            for candidate, _parent_key in walk_json_nodes(obj):
                                usage = extract_codex_token_count_usage(candidate)
                                if usage is None:
                                    continue
                                input_tokens, cache_tokens, output_tokens = usage
                                if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                    continue
                                event_time = extract_time(candidate) or fallback_time
                                model = extract_model(candidate)
                                if model == "unknown" and codex_model_hint:
                                    model = codex_model_hint
                                append_event(
                                    events,
                                    event_time,
                                    model,
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path,
                                )
                        elif active_tool == "copilot_cli":
                            for item in extract_copilot_cli_events(obj, fallback_time, session_fingerprint, path):
                                append_event(events, *item)
                        elif active_tool == "copilot_vscode":
                            for item in extract_copilot_vscode_events(obj, fallback_time, path):
                                append_event(events, *item)
                        else:
                            local_seen = set()
                            for candidate, parent_key in walk_json_nodes(obj):
                                if parent_key == "usage":
                                    continue
                                input_tokens, cache_tokens, output_tokens = extract_usage(candidate)
                                if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                    continue
                                event_time = extract_time(candidate) or fallback_time
                                dedupe_key = (
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    event_time.isoformat(),
                                )
                                if dedupe_key in local_seen:
                                    continue
                                local_seen.add(dedupe_key)
                                append_event(
                                    events,
                                    event_time,
                                    extract_model(candidate),
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path,
                                )
                except Exception as exc:
                    warnings.append(active_tool + ": failed reading " + path + ": " + str(exc))
        except Exception:
            pass
print(json.dumps({"events": events, "warnings": warnings}))
"""
).replace("__REMOTE_PARSE_TIME_HELPER__", _REMOTE_PARSE_TIME_HELPER)


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    port: int

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass(frozen=True)
class RemoteCollectJob:
    tool: str
    patterns: list[str]


class RemoteFileCollector(BaseCollector):
    _STDOUT_PROGRESS_STEP_BYTES = 256 * 1024

    def __init__(
        self,
        name: str,
        target: SshTarget,
        source_name: str,
        source_host_hash: str,
        patterns: Optional[list[str]] = None,
        max_files: int = 400,
        max_total_bytes: int = 64 * 1024 * 1024,
        timeout_sec: int = 120,
        runner=None,
        popen_factory=None,
        jobs: Optional[list[RemoteCollectJob]] = None,
        use_sshpass: bool = False,
        ssh_password: Optional[str] = None,
    ) -> None:
        self.name = name
        self.target = target
        if jobs is not None:
            self.jobs = [job for job in jobs if job.patterns]
        else:
            self.jobs = [RemoteCollectJob(tool=name, patterns=list(patterns or []))]
        self.source_name = source_name
        self.source_host_hash = source_host_hash
        self.max_files = max(1, max_files)
        self.max_total_bytes = max(1, max_total_bytes)
        self.timeout_sec = max(10, timeout_sec)
        self._runner = runner or subprocess.run
        self._popen_factory = popen_factory or (subprocess.Popen if runner is None else None)
        self.use_sshpass = use_sshpass
        self.ssh_password = ssh_password
        self._use_connection_sharing = True
        self._logged_connection_sharing_fallback = False

    def probe(self) -> tuple[bool, str]:
        self._log_progress("探测：查找远端 Python")
        python_cmd, error = self._discover_python()
        if error:
            return False, error
        if not python_cmd:
            return False, "no remote python interpreter found"
        self._log_progress(f"探测：使用远端解释器 {python_cmd}")
        payload, error = self._run_python_script(python_cmd, _PROBE_SCRIPT)
        if error:
            return False, error
        matches = payload.get("matches")
        if not isinstance(matches, int):
            return False, "remote probe returned invalid payload"
        if matches == 0:
            return False, f"no data files found for {self.name}"
        return True, f"{matches} remote files detected"

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        warnings: list[str] = []
        self._active_start_value = start
        self._active_end_value = end
        self._log_progress("采集：查找远端 Python")
        python_cmd, error = self._discover_python()
        if error:
            return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: {error}"])
        if not python_cmd:
            return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: no remote python interpreter found"])
        self._log_progress(f"采集：使用远端解释器 {python_cmd}")

        payload, error = self._run_python_script(python_cmd, _COLLECT_SCRIPT)
        if error:
            return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: {error}"])

        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            return CollectOutput(
                events=[],
                warnings=[f"{self.source_name}/{self.name}: remote collect returned invalid payload"],
            )

        events: list[UsageEvent] = []
        warnings.extend(
            f"{self.source_name}/{self.name}: {warning}"
            for warning in payload.get("warnings", [])
            if isinstance(warning, str) and warning.strip()
        )
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_time = _parse_datetime_value(item.get("event_time"))
            if event_time is None:
                continue
            if start <= event_time <= end:
                events.append(
                    UsageEvent(
                        tool=str(item.get("tool") or self.name),
                        model=str(item.get("model") or "unknown"),
                        event_time=event_time,
                        input_tokens=_coerce_int(item.get("input_tokens")),
                        cache_tokens=_coerce_int(item.get("cache_tokens")),
                        output_tokens=_coerce_int(item.get("output_tokens")),
                        session_fingerprint=_optional_str(item.get("session_fingerprint")),
                        source_ref=_optional_str(item.get("source_ref")),
                        source_host_hash=self.source_host_hash,
                    )
                )
        if not events:
            warnings.append(f"{self.source_name}/{self.name}: no usage events in selected time range")
        return CollectOutput(events=events, warnings=warnings)

    def _discover_python(self) -> tuple[Optional[str], Optional[str]]:
        for remote_args in _python_discovery_commands():
            try:
                completed = self._run_ssh_with_optional_fallback(remote_args, timeout=15)
            except ValueError as exc:
                return None, str(exc)
            label = " ".join(remote_args[:2])
            if completed is None:
                self._log_progress(f"探测失败：{label} timed out")
                continue
            if completed.returncode != 0:
                stderr_preview = _preview_text(completed.stderr)
                if stderr_preview:
                    self._log_progress(f"探测失败：{label} rc={completed.returncode} stderr={stderr_preview}")
                else:
                    stdout_preview = _preview_text(completed.stdout)
                    self._log_progress(f"探测失败：{label} rc={completed.returncode} stdout={stdout_preview}")
                continue
            python_cmd = _extract_python_command(completed.stdout)
            if python_cmd:
                return python_cmd, None
            preview = _preview_text(completed.stdout)
            if preview:
                self._log_progress(f"探测未命中：{label} stdout={preview}")
        return None, None

    def _run_python_script(self, python_cmd: str, script: str) -> tuple[dict, Optional[str]]:
        command, script_input = self._python_stdin_command(python_cmd, script)
        self._log_progress("执行远端脚本（单次 SSH）")
        completed, error = self._ssh_run_python_command(command, input_text=script_input)
        if error:
            return {}, error
        payload, discarded = _extract_json_payload(completed.stdout)
        if discarded:
            for line in discarded.splitlines():
                text = line.strip()
                if text:
                    self._log_progress(f"remote stdout noise: {text}")
        if payload is None:
            if self._should_fallback_to_uploaded_script(completed.stdout, completed.stderr):
                self._log_progress("检测到远端网关会吞掉 stdin 脚本，回退为上传临时脚本执行")
                return self._run_python_script_via_uploaded_file(python_cmd, script)
            self._log_non_json_debug(completed.stdout, completed.stderr)
            return {}, "remote command returned non-JSON output"
        if not isinstance(payload, dict):
            return {}, "remote command returned invalid JSON payload"
        return payload, None

    def _build_remote_payload(self) -> dict[str, object]:
        return {
            "jobs": [{"tool": job.tool, "patterns": job.patterns} for job in self.jobs],
            "start_ts": self._active_start.timestamp(),
            "end_ts": self._active_end.timestamp(),
            "max_files": self.max_files,
            "max_total_bytes": self.max_total_bytes,
        }

    def _python_stdin_command(self, python_cmd: str, script: str) -> tuple[list[str], str]:
        payload = base64.b64encode(json.dumps(self._build_remote_payload()).encode("utf-8")).decode("ascii")
        bootstrap = (
            "import sys;"
            "PAYLOAD_B64=sys.stdin.readline().rstrip('\\n');"
            "exec(sys.stdin.read(), {'__name__': '__main__', 'PAYLOAD_B64': PAYLOAD_B64})"
        )
        remote_command = f"{_shell_quote(python_cmd)} -c {_shell_quote(bootstrap)}"
        return ["sh", "-lc", remote_command], payload + "\n" + script

    def _run_python_script_via_uploaded_file(self, python_cmd: str, script: str) -> tuple[dict, Optional[str]]:
        remote_base = f"/tmp/llm_usage_{os.getpid()}_{next(tempfile._get_candidate_names())}"
        remote_script = f"{remote_base}.py"
        combined_script = self._build_uploaded_remote_script(script)
        self._log_progress(f"上传远端脚本 -> {remote_script}")
        previous_connection_sharing = self._use_connection_sharing
        self._use_connection_sharing = False
        try:
            error = self._ssh_write_text(remote_script, combined_script)
            if error:
                return {}, error
            command = ["sh", "-lc", f"{_shell_quote(python_cmd)} {_shell_quote(remote_script)}"]
            try:
                completed = self._run_ssh_with_optional_fallback(command, input_text="", timeout=self.timeout_sec)
            except ValueError as exc:
                return {}, str(exc)
            if completed is None:
                return {}, "remote command timed out"
            if completed.returncode != 0:
                return {}, completed.stderr.strip() or completed.stdout.strip() or "remote command failed"
            if completed.stderr.strip():
                for line in completed.stderr.strip().splitlines():
                    self._log_progress(f"remote stderr: {line}")
            payload, discarded = _extract_json_payload(completed.stdout)
            if discarded:
                for line in discarded.splitlines():
                    text = line.strip()
                    if text:
                        self._log_progress(f"remote stdout noise: {text}")
            if payload is None:
                self._log_non_json_debug(completed.stdout, completed.stderr)
                return {}, "remote command returned non-JSON output"
            if not isinstance(payload, dict):
                return {}, "remote command returned invalid JSON payload"
            return payload, None
        finally:
            self._ssh_remove_file(remote_script)
            self._use_connection_sharing = previous_connection_sharing

    def _build_uploaded_remote_script(self, script: str) -> str:
        payload = base64.b64encode(json.dumps(self._build_remote_payload()).encode("utf-8")).decode("ascii")
        return f"PAYLOAD_B64 = {payload!r}\n" + script.lstrip()

    def _ssh_write_text(self, remote_path: str, content: str) -> Optional[str]:
        try:
            completed = self._run_ssh_with_optional_fallback(
                ["sh", "-lc", f"cat > {_shell_quote(remote_path)}"],
                input_text=content,
                timeout=self.timeout_sec,
            )
        except ValueError as exc:
            return str(exc)
        if completed is None:
            return "ssh upload timed out"
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or "ssh upload failed"
        return None

    def _ssh_remove_file(self, remote_path: str) -> None:
        try:
            self._run_ssh_with_optional_fallback(
                ["sh", "-lc", f"rm -f {_shell_quote(remote_path)}"],
                timeout=15,
            )
        except Exception:
            return

    def _ssh_read_text(self, remote_path: str) -> tuple[str, Optional[str]]:
        try:
            completed = self._run_ssh_with_optional_fallback(
                ["sh", "-lc", f"cat {_shell_quote(remote_path)}"],
                timeout=self.timeout_sec,
            )
        except ValueError as exc:
            return "", str(exc)
        if completed is None:
            return "", "ssh download timed out"
        if completed.returncode != 0:
            return "", completed.stderr.strip() or completed.stdout.strip() or "ssh download failed"
        return completed.stdout, None

    def _should_fallback_to_uploaded_script(self, stdout_text: str, stderr_text: str = "") -> bool:
        lowered = "\n".join(part for part in (stdout_text, stderr_text) if part).lower()
        return 'file "<stdin>", line 1' in lowered and (
            "nameerror:" in lowered or "syntaxerror:" in lowered or "traceback" in lowered
        )

    def _ssh_run_python_command(
        self,
        args: list[str],
        *,
        input_text: str,
    ) -> tuple[Optional[subprocess.CompletedProcess[str]], Optional[str]]:
        return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=True)

    def _ssh_run_python_command_once(
        self,
        args: list[str],
        input_text: str,
        allow_retry: bool,
    ) -> tuple[Optional[subprocess.CompletedProcess[str]], Optional[str]]:
        try:
            command, env = self._ssh_command_and_env(args)
        except ValueError as exc:
            return None, str(exc)
        if self._popen_factory is None:
            try:
                completed = self._run_ssh_with_optional_fallback(
                    args,
                    input_text=input_text,
                    timeout=self.timeout_sec,
                )
            except ValueError as exc:
                return None, str(exc)
            if completed is None:
                return None, "remote command timed out"
            if completed.returncode != 0:
                if allow_retry and self._maybe_disable_connection_sharing_from_output(completed):
                    return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=False)
                if self._should_fallback_to_uploaded_script(completed.stdout, completed.stderr):
                    return completed, None
                return None, completed.stderr.strip() or completed.stdout.strip() or "remote command failed"
            if completed.stderr.strip():
                for line in completed.stderr.strip().splitlines():
                    self._log_progress(f"remote stderr: {line}")
            return completed, None

        stdout_handle = tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8")
        try:
            popen_kwargs = dict(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            if env is not None:
                popen_kwargs["env"] = env
            process = self._popen_factory(command, **popen_kwargs)
        except OSError as exc:
            stdout_handle.close()
            Path(stdout_handle.name).unlink(missing_ok=True)
            return None, f"remote command failed to start: {exc}"

        stderr_lines: list[str] = []
        stderr_buffer = b""
        stdout_bytes = 0
        next_progress = self._STDOUT_PROGRESS_STEP_BYTES
        deadline = time.monotonic() + self.timeout_sec
        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if process.stdin is not None:
            stdin_bytes = input_text.encode("utf-8")
            process.stdin.write(stdin_bytes)
            process.stdin.close()
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, self.timeout_sec)
                ready = selector.select(timeout=min(0.2, remaining))
                if not ready:
                    continue
                for key, _mask in ready:
                    stream = key.fileobj
                    data = os.read(stream.fileno(), 65536)
                    if not data:
                        selector.unregister(stream)
                        continue
                    if key.data == "stdout":
                        stdout_handle.buffer.write(data)
                        stdout_bytes += len(data)
                        if stdout_bytes >= next_progress:
                            self._log_progress(f"remote stdout received {stdout_bytes} bytes")
                            next_progress += self._STDOUT_PROGRESS_STEP_BYTES
                        continue
                    stderr_buffer += data
                    while b"\n" in stderr_buffer:
                        raw_line, stderr_buffer = stderr_buffer.split(b"\n", 1)
                        text = raw_line.decode("utf-8", errors="replace").rstrip()
                        if text:
                            stderr_lines.append(text)
                            self._log_progress(f"remote stderr: {text}")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise subprocess.TimeoutExpired(args, self.timeout_sec)
            if stderr_buffer.strip():
                text = stderr_buffer.decode("utf-8", errors="replace").rstrip()
                if text:
                    stderr_lines.append(text)
                    self._log_progress(f"remote stderr: {text}")
        except subprocess.TimeoutExpired:
            selector.close()
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if allow_retry and self._disable_connection_sharing("ssh session timed out while using connection sharing"):
                return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=False)
            return None, "remote command timed out"
        finally:
            selector.close()
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        try:
            if process.returncode != 0:
                stderr_text = "\n".join(stderr_lines)
                if allow_retry and self._maybe_disable_connection_sharing_from_text(stderr_text):
                    return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=False)
                if self._should_fallback_to_uploaded_script("", stderr_text):
                    return subprocess.CompletedProcess(command, process.returncode, "", stderr_text), None
                return None, stderr_text.strip() or "remote command failed"
            stdout_handle.flush()
            if stdout_bytes:
                self._log_progress(f"remote stdout complete {stdout_bytes} bytes")
            stdout_text = Path(stdout_handle.name).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(command, process.returncode, stdout_text, "\n".join(stderr_lines)), None
        finally:
            stdout_handle.close()
            Path(stdout_handle.name).unlink(missing_ok=True)
        return None, "remote command failed"

    def _ssh_command_and_env(self, remote_args: list[str]) -> tuple[list[str], Optional[dict[str, str]]]:
        return _ssh_command_and_env(
            self.target.destination,
            self.target.port,
            remote_args,
            use_connection_sharing=self._use_connection_sharing,
            use_sshpass=self.use_sshpass,
            ssh_password=self.ssh_password,
        )

    def _log_progress(self, message: str) -> None:
        print(f"info: remote[{self.source_name}/{self.name}] {message}")

    def _log_non_json_debug(self, stdout_text: str, stderr_text: str) -> None:
        self._log_progress(
            f"remote stdout debug: {len(stdout_text.encode('utf-8', errors='replace'))} bytes"
        )
        preview = _preview_text(stdout_text)
        if preview:
            self._log_progress(f"remote stdout preview: {preview}")
        if stderr_text.strip():
            self._log_progress(
                f"remote stderr debug: {len(stderr_text.encode('utf-8', errors='replace'))} bytes"
            )
            self._log_progress(f"remote stderr preview: {_preview_text(stderr_text)}")

    def _run_ssh_with_optional_fallback(
        self,
        remote_args: list[str],
        *,
        input_text: Optional[str] = None,
        timeout: int,
    ):
        command, env = self._ssh_command_and_env(remote_args)
        try:
            run_kwargs = dict(
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=timeout,
            )
            if env is not None:
                run_kwargs["env"] = env
            return self._runner(
                command,
                **run_kwargs,
            )
        except FileNotFoundError as exc:
            raise ValueError(_missing_ssh_binary_message(exc, self.use_sshpass))
        except subprocess.TimeoutExpired:
            if self._disable_connection_sharing("ssh timed out while using connection sharing"):
                try:
                    retry_command, retry_env = self._ssh_command_and_env(remote_args)
                    retry_kwargs = dict(
                        check=False,
                        capture_output=True,
                        text=True,
                        input=input_text,
                        timeout=timeout,
                    )
                    if retry_env is not None:
                        retry_kwargs["env"] = retry_env
                    return self._runner(
                        retry_command,
                        **retry_kwargs,
                    )
                except FileNotFoundError as exc:
                    raise ValueError(_missing_ssh_binary_message(exc, self.use_sshpass))
                except subprocess.TimeoutExpired:
                    return None
            return None

    def _maybe_disable_connection_sharing_from_output(self, completed) -> bool:
        return self._maybe_disable_connection_sharing_from_text(
            "\n".join(part for part in (completed.stderr, completed.stdout) if isinstance(part, str) and part.strip())
        )

    def _maybe_disable_connection_sharing_from_text(self, text: str) -> bool:
        lowered = text.lower()
        if any(
            marker in lowered
            for marker in (
                "mux_client_",
                "control socket",
                "master is dead",
                "broken pipe",
                "connection reset",
            )
        ):
            return self._disable_connection_sharing("ssh connection sharing is not supported by the remote gateway")
        return False

    def _disable_connection_sharing(self, reason: str) -> bool:
        if not self._use_connection_sharing:
            return False
        self._use_connection_sharing = False
        if not self._logged_connection_sharing_fallback:
            self._logged_connection_sharing_fallback = True
            self._log_progress(f"检测到 SSH 复用不稳定，切换为普通 SSH 重试: {reason}")
        return True

    @property
    def _active_start(self) -> datetime:
        return getattr(self, "_active_start_value", datetime.fromtimestamp(0, tz=timezone.utc))

    @property
    def _active_end(self) -> datetime:
        return getattr(self, "_active_end_value", datetime.now(timezone.utc))


def _suffix(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".jsonl"):
        return ".jsonl"
    if lower.endswith(".json"):
        return ".json"
    return ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _ssh_base_command(destination: str, port: int, use_connection_sharing: bool = True) -> list[str]:
    command = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(port),
        destination,
    ]
    if use_connection_sharing:
        command[3:3] = [
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=5m",
            "-o",
            "ControlPath=/tmp/llm-usage-ssh-%C",
        ]
    return command


def _ssh_command_and_env(
    destination: str,
    port: int,
    remote_args: list[str],
    *,
    use_connection_sharing: bool = True,
    use_sshpass: bool = False,
    ssh_password: Optional[str] = None,
) -> tuple[list[str], Optional[dict[str, str]]]:
    remote_command = " ".join(_shell_quote(arg) for arg in remote_args)
    command = _ssh_base_command(destination, port, use_connection_sharing=use_connection_sharing) + [remote_command]
    if not use_sshpass:
        return command, None
    password = ssh_password if ssh_password is not None else os.environ.get("SSHPASS", "")
    if not password.strip():
        raise ValueError("SSH 密码模式需要提供密码")
    env = os.environ.copy()
    env["SSHPASS"] = password
    return ["sshpass", "-e"] + command, env


def _coerce_int(value: object) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _missing_ssh_binary_message(exc: FileNotFoundError, use_sshpass: bool) -> str:
    missing = (getattr(exc, "filename", None) or "").strip()
    if not missing:
        text = str(exc)
        if "sshpass" in text:
            missing = "sshpass"
        elif "'ssh'" in text or text.strip() == "ssh":
            missing = "ssh"
    if missing == "sshpass":
        return "sshpass 未找到"
    if missing == "ssh":
        return "SSH 命令未找到"
    if use_sshpass:
        return "SSH 或 sshpass 命令未找到"
    return "SSH 命令未找到"


def _normalize_iso_datetime_text(text: str) -> str:
    stripped = text.strip()
    stripped = stripped.replace(",", ".", 1)
    if stripped.endswith("Z"):
        stripped = stripped[:-1] + "+00:00"
    match = re.search(r"([+-]\d{2})$", stripped)
    if match:
        stripped = stripped + ":00"
    match = re.search(r"([+-]\d{2}):(\d{2})$", stripped)
    if match:
        stripped = stripped[: match.start()] + match.group(1) + match.group(2)
    return stripped


def _parse_datetime_value(raw: object) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10000000000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 10000000000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        normalized = _normalize_iso_datetime_text(text)
        formats = (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M%z",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                dt = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return None


def _extract_python_command(stdout: str) -> Optional[str]:
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if _is_python_command(line):
            return line
    for token in stdout.split():
        if _is_python_command(token):
            return token
    return None


def _python_discovery_commands() -> list[list[str]]:
    discover = (
        "command -v python3 >/dev/null 2>&1 && command -v python3 || "
        "(command -v python >/dev/null 2>&1 && command -v python || true)"
    )
    discover_common_paths = (
        "for candidate in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 "
        "/bin/python3 /usr/bin/python /usr/local/bin/python; do "
        "[ -x \"$candidate\" ] && printf '%s\\n' \"$candidate\" && exit 0; "
        "done; true"
    )
    return [
        ["sh", "-lc", discover],
        ["bash", "-lc", discover],
        ["zsh", "-lc", discover],
        ["sh", "-lc", discover_common_paths],
    ]


def _is_python_command(value: str) -> bool:
    if value in {"python3", "python"}:
        return True
    basename = os.path.basename(value)
    return basename in {"python3", "python"}


def _extract_json_payload(stdout: str) -> tuple[Optional[Union[dict, list]], str]:
    text = stdout.strip()
    if not text:
        return None, ""
    try:
        return json.loads(text), ""
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for start, ch in enumerate(stdout):
        if ch not in "{[":
            continue
        try:
            payload, end = decoder.raw_decode(stdout[start:])
        except ValueError:
            continue
        discarded = (stdout[:start] + stdout[start + end :]).strip()
        return payload, discarded
    return None, stdout


def _preview_text(text: str, limit: int = 400) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."
