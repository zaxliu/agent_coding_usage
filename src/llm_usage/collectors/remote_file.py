from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import selectors
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput


class SshAuthenticationError(Exception):
    """Raised when SSH authentication fails and no password fallback is available."""

    def __init__(self, source_name: str, message: str = "") -> None:
        self.source_name = source_name
        super().__init__(message or f"SSH authentication failed for {source_name}")


_CHUNKED_STDOUT_PREFIX = "LLMUSAGE_CHUNKED_V1"
_DEFAULT_STDOUT_CHUNK_SIZE = 32 * 1024
_DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES = 48 * 1024
_PACKAGE_NAME = "llm-usage-horizon"

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
import base64, glob, hashlib, json, os, re, sys
from datetime import datetime, timezone

payload = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
jobs = payload.get("jobs", [])
start_ts = float(payload.get("start_ts", 0))
end_ts = float(payload.get("end_ts", 0))
max_files = int(payload.get("max_files", 0) or 0)
max_total_bytes = int(payload.get("max_total_bytes", 0) or 0)
stdout_page_budget_bytes = int(payload.get("stdout_page_budget_bytes") or 0)
remote_cursor = payload.get("cursor")

def log(message):
    print(message, file=sys.stderr)

if remote_cursor is not None:
    log("info: remote collect cursor present")

def coerce_cursor_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default

cj = 0
cp = 0
cf = 0
cl = 0
if remote_cursor is not None and isinstance(remote_cursor, dict):
    cj = max(0, coerce_cursor_int(remote_cursor.get("job_index"), 0))
    cp = max(0, coerce_cursor_int(remote_cursor.get("pattern_index"), 0))
    cf = max(0, coerce_cursor_int(remote_cursor.get("file_index"), 0))
    cl = max(0, coerce_cursor_int(remote_cursor.get("line_index"), 0))

def file_is_before_resume_cursor(job_index, pattern_index, file_index):
    if remote_cursor is None:
        return False
    return (job_index, pattern_index, file_index) < (cj, cp, cf)

def jsonl_resume_line_index(job_index, pattern_index, file_index):
    if remote_cursor is None:
        return 0
    if (job_index, pattern_index, file_index) == (cj, cp, cf):
        return cl
    return 0

def _emit_chunked_payload(payload_obj):
    raw = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    blob = base64.b64encode(raw).decode("ascii")
    chunk_size = __CHUNKED_CHUNK_SIZE__
    chunks = [blob[i : i + chunk_size] for i in range(0, len(blob), chunk_size)] or [""]
    prefix = __CHUNKED_STDOUT_PREFIX__
    print(prefix + " BEGIN total_chunks=" + str(len(chunks)) + " total_bytes=" + str(len(raw)) + " sha256=" + digest)
    for index, chunk in enumerate(chunks):
        print(prefix + " CHUNK index=" + str(index) + " data=" + chunk)
    print(prefix + " END")

def _chunked_wire_bytes(payload_obj):
    raw = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    blob = base64.b64encode(raw).decode("ascii")
    chunk_size = __CHUNKED_CHUNK_SIZE__
    chunks = [blob[i : i + chunk_size] for i in range(0, len(blob), chunk_size)] or [""]
    prefix = __CHUNKED_STDOUT_PREFIX__
    total = len((prefix + " BEGIN total_chunks=" + str(len(chunks)) + " total_bytes=" + str(len(raw)) + " sha256=" + digest + "\\n").encode("utf-8"))
    for index, chunk in enumerate(chunks):
        total += len((prefix + " CHUNK index=" + str(index) + " data=" + chunk + "\\n").encode("utf-8"))
    total += len((prefix + " END\\n").encode("utf-8"))
    return total

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

def extract_copilot_vscode_events_from_jsonl_text(text, fallback_time, source_ref, start_line_index=0):
    state = {}
    saw_delta = False
    out = []
    idx = 0
    for raw_line in text.splitlines():
        idx += 1
        if (idx - 1) < start_line_index:
            continue
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

def extract_cline_vscode_usage(node):
    metrics = node.get("metrics")
    if not isinstance(metrics, dict):
        return 0, 0, 0
    tokens = metrics.get("tokens")
    if not isinstance(tokens, dict):
        return 0, 0, 0
    cache_tokens = coerce_int(tokens.get("cached") or tokens.get("cacheRead") or tokens.get("cache_read"))
    prompt_tokens = coerce_int(tokens.get("prompt") or tokens.get("input") or tokens.get("inputTokens"))
    output_tokens = coerce_int(tokens.get("completion") or tokens.get("output") or tokens.get("outputTokens"))
    input_tokens = prompt_tokens
    if prompt_tokens > 0 and cache_tokens > 0:
        input_tokens = max(0, prompt_tokens - cache_tokens)
    return input_tokens, cache_tokens, output_tokens

def extract_cline_vscode_model(node):
    model_info = node.get("modelInfo")
    if isinstance(model_info, dict):
        model_id = model_info.get("modelId")
        if isinstance(model_id, str) and model_id.strip():
            return model_id.strip()
    return "unknown"

def extract_cline_task_id(path):
    parent = os.path.basename(os.path.dirname(path)).strip()
    if parent:
        return parent
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem or "unknown"

def extract_cline_vscode_events(node, fallback_time, source_ref):
    if not isinstance(node, list):
        return []
    task_id = extract_cline_task_id(source_ref)
    out = []
    event_index = 0
    for item in node:
        if not isinstance(item, dict) or str(item.get("role") or "").strip() != "assistant":
            continue
        input_tokens, cache_tokens, output_tokens = extract_cline_vscode_usage(item)
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            continue
        event_index += 1
        event_time = parse_time(item.get("ts")) or fallback_time
        event_ts_ms = int(event_time.timestamp() * 1000)
        out.append(
            (
                event_time,
                extract_cline_vscode_model(item),
                input_tokens,
                cache_tokens,
                output_tokens,
                "cline_vscode:" + task_id + ":" + str(event_index) + ":" + str(event_ts_ms),
                source_ref + ":" + str(event_index),
            )
        )
    return out

def build_resume_cursor(job_index, pattern_index, file_index, source_ref, path_is_jsonl):
    # line_index: for .jsonl path:line refs, 0-based physical line index of the resume point (next line to read).
    line_index = 0
    if path_is_jsonl:
        ref = str(source_ref)
        if ":" in ref:
            tail = ref.rsplit(":", 1)[-1]
            if tail.isdigit():
                line_index = max(0, int(tail) - 1)
    return {"job_index": job_index, "pattern_index": pattern_index, "file_index": file_index, "line_index": line_index}

def append_event(out, cursors, event_time, model, input_tokens, cache_tokens, output_tokens, session_fingerprint, source_ref, resume_cursor):
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
    cursors.append(resume_cursor)

events = []
event_resume_cursors = []
warnings = []
seen = set()
processed_files = 0
total_bytes = 0
for job_index, spec in enumerate(jobs):
    active_tool = spec.get("tool", "unknown")
    patterns = spec.get("patterns", [])
    for pattern_index, pattern in enumerate(patterns):
        try:
            log("info: expanding pattern tool=" + active_tool + " pattern=" + pattern)
            for file_index, path in enumerate(sorted(glob.glob(os.path.expanduser(pattern), recursive=True))):
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
                if file_is_before_resume_cursor(job_index, pattern_index, file_index):
                    continue
                if max_files > 0 and processed_files >= max_files:
                    warnings.append("stopped after reaching max_files=" + str(max_files))
                    _emit_chunked_payload({"events": events, "warnings": warnings, "next_cursor": None})
                    raise SystemExit(0)
                file_size = int(stat.st_size)
                if max_total_bytes > 0 and total_bytes + file_size > max_total_bytes:
                    warnings.append("stopped after reaching max_total_bytes=" + str(max_total_bytes))
                    _emit_chunked_payload({"events": events, "warnings": warnings, "next_cursor": None})
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
                            start_li = jsonl_resume_line_index(job_index, pattern_index, file_index)
                            for item in extract_copilot_vscode_events_from_jsonl_text(
                                text, fallback_time, path, start_li
                            ):
                                rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], True)
                                append_event(events, event_resume_cursors, *item, rc)
                            continue
                        start_li = jsonl_resume_line_index(job_index, pattern_index, file_index)
                        idx = 0
                        for raw_line in text.splitlines():
                            idx += 1
                            if (idx - 1) < start_li:
                                continue
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
                                    rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], True)
                                    append_event(events, event_resume_cursors, *item, rc)
                                continue
                            if active_tool == "copilot_vscode":
                                for item in extract_copilot_vscode_events(
                                    obj,
                                    fallback_time,
                                    path + ":" + str(idx),
                                ):
                                    rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], True)
                                    append_event(events, event_resume_cursors, *item, rc)
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
                                rc = build_resume_cursor(job_index, pattern_index, file_index, path + ":" + str(idx), True)
                                append_event(
                                    events,
                                    event_resume_cursors,
                                    event_time,
                                    model,
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path + ":" + str(idx),
                                    rc,
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
                                rc = build_resume_cursor(job_index, pattern_index, file_index, path + ":" + str(idx), True)
                                append_event(
                                    events,
                                    event_resume_cursors,
                                    event_time,
                                    extract_model(candidate),
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path + ":" + str(idx),
                                    rc,
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
                                rc = build_resume_cursor(job_index, pattern_index, file_index, path, False)
                                append_event(
                                    events,
                                    event_resume_cursors,
                                    event_time,
                                    model,
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path,
                                    rc,
                                )
                        elif active_tool == "copilot_cli":
                            for item in extract_copilot_cli_events(obj, fallback_time, session_fingerprint, path):
                                rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], False)
                                append_event(events, event_resume_cursors, *item, rc)
                        elif active_tool == "copilot_vscode":
                            for item in extract_copilot_vscode_events(obj, fallback_time, path):
                                rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], False)
                                append_event(events, event_resume_cursors, *item, rc)
                        elif active_tool == "cline_vscode":
                            for item in extract_cline_vscode_events(obj, fallback_time, path):
                                rc = build_resume_cursor(job_index, pattern_index, file_index, item[6], False)
                                append_event(events, event_resume_cursors, *item, rc)
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
                                rc = build_resume_cursor(job_index, pattern_index, file_index, path, False)
                                append_event(
                                    events,
                                    event_resume_cursors,
                                    event_time,
                                    extract_model(candidate),
                                    input_tokens,
                                    cache_tokens,
                                    output_tokens,
                                    session_fingerprint,
                                    path,
                                    rc,
                                )
                except Exception as exc:
                    warnings.append(active_tool + ": failed reading " + path + ": " + str(exc))
        except Exception:
            pass
next_cursor = None
if stdout_page_budget_bytes > 0:

    def _page_wire_bytes(ev, wn, nc):
        # Must match _emit_chunked_payload: same object shape, chunking, and stdout framing.
        return _chunked_wire_bytes({"events": ev, "warnings": wn, "next_cursor": nc})

    def _cursor_at(k):
        if k >= len(events):
            return None
        return event_resume_cursors[k]

    if _page_wire_bytes(events, warnings, None) > stdout_page_budget_bytes:
        # Choose the largest k such that events[:k] plus next_cursor fits within the full chunked stdout budget.
        chosen_k = None
        for k in range(len(events), -1, -1):
            nc = _cursor_at(k) if k < len(events) else None
            if _page_wire_bytes(events[:k], warnings, nc) <= stdout_page_budget_bytes:
                chosen_k = k
                next_cursor = nc if k < len(events) else None
                break
        if chosen_k is None:
            chosen_k = 0
            next_cursor = None
            warnings.append("remote collect: stdout_page_budget_bytes too small to emit a resumable page")
        events = events[:chosen_k]
_emit_chunked_payload({"events": events, "warnings": warnings, "next_cursor": next_cursor})
"""
).replace("__REMOTE_PARSE_TIME_HELPER__", _REMOTE_PARSE_TIME_HELPER).replace(
    "__CHUNKED_CHUNK_SIZE__", str(_DEFAULT_STDOUT_CHUNK_SIZE)
).replace("__CHUNKED_STDOUT_PREFIX__", repr(_CHUNKED_STDOUT_PREFIX))


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    port: int
    jump_host: str = ""
    jump_port: int = 2222

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

        events: list[UsageEvent] = []
        request_cursor: Optional[dict[str, Any]] = None
        while True:
            payload, error = self._run_python_script(
                python_cmd, _COLLECT_SCRIPT, cursor=request_cursor, use_page_payload=True
            )
            if error:
                return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: {error}"])

            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                return CollectOutput(
                    events=[],
                    warnings=[f"{self.source_name}/{self.name}: remote collect returned invalid payload"],
                )

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

            next_cursor = payload.get("next_cursor")
            if next_cursor is None:
                break
            if request_cursor is not None and _remote_cursor_tuple(next_cursor) == _remote_cursor_tuple(request_cursor):
                warnings.append(f"{self.source_name}/{self.name}: remote pagination cursor did not advance")
                return CollectOutput(events=events, warnings=warnings)
            request_cursor = next_cursor

        if not events:
            warnings.append(f"{self.source_name}/{self.name}: no usage events in selected time range")
        return CollectOutput(events=events, warnings=warnings)

    def _discover_python(self) -> tuple[Optional[str], Optional[str]]:
        required_version = _remote_python_minimum_version()
        found_candidate = False
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
                found_candidate = True
                version, error = self._probe_python_version(python_cmd)
                if error:
                    return None, error
                if version is None:
                    if _is_explicit_python3_command(python_cmd):
                        self._log_progress(f"探测：无法确认 {python_cmd} 版本，沿用该解释器")
                        return python_cmd, None
                    self._log_progress(f"探测失败：无法确认 {python_cmd} 的版本")
                    continue
                if version < required_version:
                    self._log_progress(
                        "探测命中但版本不满足："
                        f"{python_cmd} requires >="
                        f"{required_version[0]}.{required_version[1]}, "
                        f"got {version[0]}.{version[1]}"
                    )
                    continue
                return python_cmd, None
            preview = _preview_text(completed.stdout)
            if preview:
                self._log_progress(f"探测未命中：{label} stdout={preview}")
        if found_candidate:
            return None, f"no remote python interpreter found matching >={required_version[0]}.{required_version[1]}"
        return None, None

    def _probe_python_version(self, python_cmd: str) -> tuple[Optional[tuple[int, int]], Optional[str]]:
        try:
            completed = self._run_ssh_with_optional_fallback(
                _python_version_probe_command(python_cmd),
                timeout=15,
            )
        except ValueError as exc:
            return None, str(exc)
        if completed is None:
            self._log_progress(f"探测失败：{python_cmd} version probe timed out")
            return None, None
        if completed.returncode != 0:
            stderr_preview = _preview_text(completed.stderr)
            stdout_preview = _preview_text(completed.stdout)
            if stderr_preview:
                self._log_progress(f"探测失败：{python_cmd} version probe rc={completed.returncode} stderr={stderr_preview}")
            elif stdout_preview:
                self._log_progress(f"探测失败：{python_cmd} version probe rc={completed.returncode} stdout={stdout_preview}")
            return None, None
        version = _extract_python_version(completed.stdout)
        if version is None:
            preview = _preview_text(completed.stdout)
            if preview:
                self._log_progress(f"探测失败：{python_cmd} version probe stdout={preview}")
        return version, None

    def _run_python_script(
        self,
        python_cmd: str,
        script: str,
        cursor: Optional[dict[str, Any]] = None,
        *,
        use_page_payload: bool = False,
    ) -> tuple[dict, Optional[str]]:
        command, script_input = self._python_stdin_command(python_cmd, script, cursor=cursor)
        self._log_progress("执行远端脚本（单次 SSH）")
        completed, error = self._ssh_run_python_command(command, input_text=script_input)
        if error:
            return {}, error
        if use_page_payload:
            payload, discarded, parse_error = _extract_remote_page_payload(completed.stdout)
        else:
            payload, discarded, parse_error = _extract_remote_payload_with_fallbacks(completed.stdout)
        if parse_error:
            if use_page_payload and self._should_fallback_to_uploaded_script(completed.stdout, completed.stderr):
                self._log_progress("检测到远端网关会吞掉 stdin 脚本，回退为上传临时脚本执行")
                return self._run_python_script_via_uploaded_file(
                    python_cmd, script, cursor=cursor, use_page_payload=use_page_payload
                )
            self._log_non_json_debug(completed.stdout, completed.stderr)
            self._log_progress(parse_error)
            return {}, parse_error
        if discarded:
            for line in discarded.splitlines():
                text = line.strip()
                if text:
                    self._log_progress(f"remote stdout noise: {text}")
        if payload is None:
            if self._should_fallback_to_uploaded_script(completed.stdout, completed.stderr):
                self._log_progress("检测到远端网关会吞掉 stdin 脚本，回退为上传临时脚本执行")
                return self._run_python_script_via_uploaded_file(
                    python_cmd, script, cursor=cursor, use_page_payload=use_page_payload
                )
            self._log_non_json_debug(completed.stdout, completed.stderr)
            if use_page_payload:
                return {}, "remote pagination payload: could not extract JSON from remote stdout"
            return {}, "remote command returned non-JSON output"
        if not isinstance(payload, dict):
            return {}, "remote command returned invalid JSON payload"
        return payload, None

    def _build_remote_payload(self, cursor: Optional[dict[str, Any]] = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "jobs": [{"tool": job.tool, "patterns": job.patterns} for job in self.jobs],
            "start_ts": self._active_start.timestamp(),
            "end_ts": self._active_end.timestamp(),
            "max_files": self.max_files,
            "max_total_bytes": self.max_total_bytes,
            "stdout_page_budget_bytes": _DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES,
        }
        if cursor is not None:
            payload["cursor"] = cursor
        return payload

    def _remote_collect_payload_b64(self, cursor: Optional[dict[str, Any]] = None) -> str:
        """Base64 stdin/upload payload for the remote collect script (same contract as stdin and uploaded-file paths)."""
        return base64.b64encode(json.dumps(self._build_remote_payload(cursor)).encode("utf-8")).decode("ascii")

    def _python_stdin_command(
        self, python_cmd: str, script: str, cursor: Optional[dict[str, Any]] = None
    ) -> tuple[list[str], str]:
        payload = self._remote_collect_payload_b64(cursor)
        bootstrap = (
            "import sys;"
            "PAYLOAD_B64=sys.stdin.readline().rstrip('\\n');"
            "exec(sys.stdin.read(), {'__name__': '__main__', 'PAYLOAD_B64': PAYLOAD_B64})"
        )
        remote_command = f"{_shell_quote(python_cmd)} -c {_shell_quote(bootstrap)}"
        return ["sh", "-lc", remote_command], payload + "\n" + script

    def _run_python_script_via_uploaded_file(
        self,
        python_cmd: str,
        script: str,
        cursor: Optional[dict[str, Any]] = None,
        *,
        use_page_payload: bool = False,
    ) -> tuple[dict, Optional[str]]:
        remote_base = f"/tmp/llm_usage_{os.getpid()}_{next(tempfile._get_candidate_names())}"
        remote_script = f"{remote_base}.py"
        combined_script = self._build_uploaded_remote_script(script, cursor=cursor)
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
            if use_page_payload:
                payload, discarded, parse_error = _extract_remote_page_payload(completed.stdout)
            else:
                payload, discarded, parse_error = _extract_remote_payload_with_fallbacks(completed.stdout)
            if parse_error:
                self._log_non_json_debug(completed.stdout, completed.stderr)
                self._log_progress(parse_error)
                return {}, parse_error
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

    def _build_uploaded_remote_script(self, script: str, cursor: Optional[dict[str, Any]] = None) -> str:
        payload = self._remote_collect_payload_b64(cursor)
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
        if self.ssh_password and self.ssh_password.strip():
            try:
                completed = self._run_ssh_with_optional_fallback(args, input_text=input_text, timeout=self.timeout_sec)
            except ValueError as exc:
                return None, str(exc)
            if completed is None:
                return None, "remote command timed out"
            if completed.returncode != 0:
                if self._should_fallback_to_uploaded_script(completed.stdout, completed.stderr):
                    return completed, None
                return None, completed.stderr.strip() or completed.stdout.strip() or "remote command failed"
            if completed.stderr.strip():
                for line in completed.stderr.strip().splitlines():
                    self._log_progress(f"remote stderr: {line}")
            return completed, None
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
        stdin_bytes = input_text.encode("utf-8")
        if os.name == "nt":
            try:
                try:
                    stdout_data, stderr_data = process.communicate(input=stdin_bytes, timeout=self.timeout_sec)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    if allow_retry and self._disable_connection_sharing("ssh session timed out while using connection sharing"):
                        return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=False)
                    return None, "remote command timed out"
                stdout_data = stdout_data or b""
                stderr_data = stderr_data or b""
                stdout_text = stdout_data.decode("utf-8", errors="replace")
                stderr_text = stderr_data.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    for line in stderr_text.strip().splitlines():
                        stderr_lines.append(line)
                        self._log_progress(f"remote stderr: {line}")
                if stdout_data:
                    stdout_bytes = len(stdout_data)
                    if stdout_bytes >= next_progress:
                        self._log_progress(f"remote stdout received {stdout_bytes} bytes")
                    self._log_progress(f"remote stdout complete {stdout_bytes} bytes")
                if process.returncode != 0:
                    if _is_ssh_auth_failure(stderr_text):
                        raise SshAuthenticationError(self.source_name, stderr_text.strip())
                    if allow_retry and self._maybe_disable_connection_sharing_from_text(stderr_text):
                        return self._ssh_run_python_command_once(args, input_text=input_text, allow_retry=False)
                    if self._should_fallback_to_uploaded_script(stdout_text, stderr_text):
                        return subprocess.CompletedProcess(command, process.returncode, stdout_text, stderr_text), None
                    return None, stderr_text.strip() or stdout_text.strip() or "remote command failed"
                return subprocess.CompletedProcess(command, process.returncode, stdout_text, stderr_text), None
            finally:
                stdout_handle.close()
                Path(stdout_handle.name).unlink(missing_ok=True)

        deadline = time.monotonic() + self.timeout_sec
        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if process.stdin is not None:
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
                if _is_ssh_auth_failure(stderr_text):
                    raise SshAuthenticationError(self.source_name, stderr_text.strip())
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
            jump_host=self.target.jump_host,
            jump_port=self.target.jump_port,
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
        if self.ssh_password and self.ssh_password.strip():
            try:
                result = _run_remote_command_with_paramiko(
                    target=self.target,
                    remote_args=remote_args,
                    ssh_password=self.ssh_password,
                    timeout_sec=timeout,
                    input_text=input_text,
                )
            except TimeoutError:
                return None
            except Exception as exc:
                if _is_paramiko_auth_failure(exc):
                    raise SshAuthenticationError(self.source_name, str(exc).strip())
                raise ValueError(str(exc))
            if result.returncode != 0 and _is_ssh_auth_failure(result.stderr or ""):
                raise SshAuthenticationError(self.source_name, (result.stderr or "").strip())
            return result
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
            result = self._runner(
                command,
                **run_kwargs,
            )
            if result.returncode != 0 and _is_ssh_auth_failure(result.stderr or ""):
                raise SshAuthenticationError(self.source_name, (result.stderr or "").strip())
            return result
        except FileNotFoundError as exc:
            raise ValueError(_missing_ssh_binary_message(exc))
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
                    retry_result = self._runner(
                        retry_command,
                        **retry_kwargs,
                    )
                    if retry_result.returncode != 0 and _is_ssh_auth_failure(retry_result.stderr or ""):
                        raise SshAuthenticationError(self.source_name, (retry_result.stderr or "").strip())
                    return retry_result
                except FileNotFoundError as exc:
                    raise ValueError(_missing_ssh_binary_message(exc))
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


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _ssh_base_command(
    destination: str,
    port: int,
    use_connection_sharing: bool = True,
    batch_mode: bool = False,
    jump_host: str = "",
    jump_port: int = 2222,
) -> list[str]:
    if jump_host:
        # 堡垒机模式: ssh user@user@目标IP@跳板机IP -p 跳板机端口
        user, host = destination.split("@", 1)
        destination = f"{user}@{user}@{host}@{jump_host}"
        port = jump_port
        if _is_windows_platform():
            use_connection_sharing = False
    command = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(port),
        destination,
    ]
    if batch_mode:
        command[3:3] = ["-o", "BatchMode=yes"]
    if use_connection_sharing:
        idx = 3 + (2 if batch_mode else 0)
        command[idx:idx] = [
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
    jump_host: str = "",
    jump_port: int = 2222,
) -> tuple[list[str], Optional[dict[str, str]]]:
    remote_command = " ".join(_shell_quote(arg) for arg in remote_args)
    command = _ssh_base_command(
        destination, port, use_connection_sharing=use_connection_sharing, batch_mode=True,
        jump_host=jump_host, jump_port=jump_port,
    ) + [remote_command]
    return command, None


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


def _is_ssh_auth_failure(stderr_text: str) -> bool:
    """Return True if SSH stderr indicates an authentication failure."""
    return "permission denied" in stderr_text.lower()


def _missing_ssh_binary_message(exc: FileNotFoundError) -> str:
    missing = (getattr(exc, "filename", None) or "").strip()
    if not missing:
        text = str(exc)
        if "'ssh'" in text or text.strip() == "ssh":
            missing = "ssh"
    if missing == "ssh":
        return "SSH 命令未找到"
    return "SSH 命令未找到"


def _is_paramiko_auth_failure(exc: BaseException) -> bool:
    return type(exc).__name__ in {"AuthenticationException", "BadAuthenticationType", "PasswordRequiredException"}


def _paramiko_client():
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _connect_paramiko_client(
    target: SshTarget,
    ssh_password: str,
    timeout_sec: int,
):
    timeout = max(3, timeout_sec)
    try:
        client = _paramiko_client()
        if target.jump_host:
            # Go SSH 堡垒机模式: 直连堡垒机，用复合用户名路由到目标机。
            # 堡垒机禁止 direct-tcpip 端口转发，所以不能用两步连接。
            compound_user = f"{target.user}@{target.user}@{target.host}"
            client.connect(
                hostname=target.jump_host,
                port=target.jump_port,
                username=compound_user,
                password=ssh_password,
                allow_agent=False,
                look_for_keys=False,
                timeout=timeout,
                auth_timeout=timeout,
                banner_timeout=timeout,
            )
        else:
            client.connect(
                hostname=target.host,
                port=target.port,
                username=target.user,
                password=ssh_password,
                allow_agent=False,
                look_for_keys=False,
                timeout=timeout,
                auth_timeout=timeout,
                banner_timeout=timeout,
            )
        return client, None
    except Exception:
        client.close()
        raise


def _run_remote_command_with_paramiko(
    *,
    target: SshTarget,
    remote_args: list[str],
    ssh_password: str,
    timeout_sec: int,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    if not ssh_password.strip():
        raise ValueError("SSH 密码不能为空")

    import paramiko

    command = " ".join(_shell_quote(arg) for arg in remote_args)
    client = None
    jump_client = None
    try:
        client, jump_client = _connect_paramiko_client(target, ssh_password, timeout_sec)
        stdin, stdout, stderr = client.exec_command(command, timeout=max(3, timeout_sec))
        channel = stdout.channel
        channel.settimeout(max(3, timeout_sec))
        if input_text is not None:
            stdin.write(input_text)
            stdin.flush()
        try:
            channel.shutdown_write()
        except Exception:
            stdin.close()
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(remote_args, channel.recv_exit_status(), stdout_text, stderr_text)
    except socket.timeout as exc:
        raise TimeoutError("remote command timed out") from exc
    except paramiko.AuthenticationException as exc:
        raise SshAuthenticationError("", str(exc)) from exc
    except paramiko.SSHException as exc:
        raise RuntimeError(str(exc) or "SSH 连接失败") from exc
    except OSError as exc:
        raise RuntimeError(str(exc) or "SSH 连接失败") from exc
    finally:
        for conn in (client, jump_client):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


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


def _extract_python_version(stdout: str) -> Optional[tuple[int, int]]:
    match = re.search(r"(\d+)\.(\d+)", stdout)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


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


def _python_version_probe_command(python_cmd: str) -> list[str]:
    probe = "import sys; print('%s.%s' % (sys.version_info[0], sys.version_info[1]))"
    return ["sh", "-lc", f"{_shell_quote(python_cmd)} -c {_shell_quote(probe)}"]


def _is_python_command(value: str) -> bool:
    if value in {"python3", "python"}:
        return True
    basename = os.path.basename(value)
    return basename in {"python3", "python"}


def _is_explicit_python3_command(value: str) -> bool:
    return value == "python3" or os.path.basename(value) == "python3"


def _remote_python_minimum_version() -> tuple[int, int]:
    from importlib import resources

    raw = (
        resources.files("llm_usage.resources")
        .joinpath("remote_config.json")
        .read_text(encoding="utf-8")
    )
    config = json.loads(raw)
    spec = config.get("remote_python_requires", "")
    lower_bound = re.fullmatch(r">=\s*(\d+)\.(\d+)", spec)
    if not lower_bound:
        raise RuntimeError(
            f"Invalid remote_python_requires in remote_config.json: {spec!r}"
        )
    return int(lower_bound.group(1)), int(lower_bound.group(2))


def _encode_chunked_stdout_payload(
    payload: dict[str, object], *, chunk_size: int = _DEFAULT_STDOUT_CHUNK_SIZE
) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    blob = base64.b64encode(raw).decode("ascii")
    chunks = [blob[index : index + chunk_size] for index in range(0, len(blob), chunk_size)] or [""]
    lines = [
        f"{_CHUNKED_STDOUT_PREFIX} BEGIN total_chunks={len(chunks)} total_bytes={len(raw)} sha256={digest}",
    ]
    for index, chunk in enumerate(chunks):
        lines.append(f"{_CHUNKED_STDOUT_PREFIX} CHUNK index={index} data={chunk}")
    lines.append(f"{_CHUNKED_STDOUT_PREFIX} END")
    return "\n".join(lines)


def _decode_chunked_stdout_payload(stdout: str) -> tuple[Optional[dict[str, object]], str, Optional[str]]:
    if _CHUNKED_STDOUT_PREFIX not in stdout:
        return None, stdout, None
    begin_re = re.compile(
        rf"^{re.escape(_CHUNKED_STDOUT_PREFIX)} BEGIN total_chunks=(\d+) total_bytes=(\d+) sha256=([a-f0-9]{{64}})\s*$"
    )
    chunk_re = re.compile(rf"^{re.escape(_CHUNKED_STDOUT_PREFIX)} CHUNK index=(\d+) data=(.*)$")
    end_re = re.compile(rf"^{re.escape(_CHUNKED_STDOUT_PREFIX)} END\s*$")
    discarded_lines: list[str] = []
    begin: Optional[tuple[int, int, str]] = None
    chunk_data: dict[int, str] = {}
    saw_end = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m_begin = begin_re.match(line)
        if m_begin:
            begin = (int(m_begin.group(1)), int(m_begin.group(2)), m_begin.group(3))
            continue
        m_chunk = chunk_re.match(line)
        if m_chunk:
            chunk_data[int(m_chunk.group(1))] = m_chunk.group(2)
            continue
        if end_re.match(line):
            saw_end = True
            continue
        discarded_lines.append(line)
    if begin is None or not saw_end:
        return None, stdout, "remote chunked stdout invalid framing"
    total_chunks, total_bytes, expected_sha = begin
    if len(chunk_data) != total_chunks or any(index not in chunk_data for index in range(total_chunks)):
        return None, stdout, "remote chunked stdout missing chunks"
    blob = "".join(chunk_data[index] for index in range(total_chunks))
    try:
        raw = base64.b64decode(blob.encode("ascii"), validate=True)
    except (ValueError, binascii.Error):
        return None, stdout, "remote chunked stdout invalid base64"
    if len(raw) != total_bytes:
        return None, stdout, "remote chunked stdout length mismatch"
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        return None, stdout, "remote chunked stdout hash mismatch"
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, stdout, "remote chunked stdout invalid json"
    if not isinstance(parsed, dict):
        return None, stdout, "remote chunked stdout invalid json payload"
    discarded_noise = "\n".join(discarded_lines).strip()
    return parsed, discarded_noise, None


def _extract_json_payload_legacy(stdout: str) -> tuple[Optional[Union[dict, list]], str]:
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


def _extract_remote_payload_with_fallbacks(
    stdout: str,
) -> tuple[Optional[Union[dict, list]], str, Optional[str]]:
    """Parse remote stdout: try chunked protocol first when present, then legacy JSON with noise tolerance.

    Returns ``(payload, discarded_noise, chunked_error)``. On successful chunked decode,
    ``discarded_noise`` holds non-protocol lines (e.g. SSH/bastion banners) around the frame.
    When ``chunked_error`` is set, callers must not treat the failure as generic non-JSON
    (chunked framing was present but invalid).
    """
    if _CHUNKED_STDOUT_PREFIX in stdout:
        parsed, discarded, error = _decode_chunked_stdout_payload(stdout)
        if error is not None:
            return None, discarded, error
        return parsed, discarded, None
    payload, discarded = _extract_json_payload_legacy(stdout)
    return payload, discarded, None


def _remote_cursor_tuple(cursor: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(cursor.get("job_index", 0)),
        int(cursor.get("pattern_index", 0)),
        int(cursor.get("file_index", 0)),
        int(cursor.get("line_index", 0)),
    )


def _is_valid_remote_cursor(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    for key in ("job_index", "pattern_index", "file_index", "line_index"):
        raw = value.get(key)
        if not isinstance(raw, int) or isinstance(raw, bool):
            return False
        if raw < 0:
            return False
    return True


def _extract_remote_page_payload(
    stdout: str,
) -> tuple[Optional[dict[str, object]], str, Optional[str]]:
    """Parse remote stdout and validate one paginated collect page payload."""
    payload, discarded, chunked_error = _extract_remote_payload_with_fallbacks(stdout)
    if chunked_error is not None:
        return None, discarded, chunked_error
    if payload is None:
        return None, discarded, "remote pagination payload: could not extract JSON from remote stdout"
    if not isinstance(payload, dict):
        return None, discarded, "remote pagination payload: JSON root must be an object"
    if "events" not in payload or not isinstance(payload["events"], list):
        return None, discarded, "remote pagination payload invalid: events must be a list"
    if "warnings" not in payload or not isinstance(payload["warnings"], list):
        return None, discarded, "remote pagination payload invalid: warnings must be a list"
    if "next_cursor" not in payload:
        return None, discarded, "remote pagination payload missing next_cursor"
    if not _is_valid_remote_cursor(payload["next_cursor"]):
        return None, discarded, "remote pagination returned invalid cursor"
    return payload, discarded, None


def _preview_text(text: str, limit: int = 400) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."
