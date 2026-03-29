from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from llm_usage.models import UsageEvent


def walk_json_nodes(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk_json_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_json_nodes(item)


def _coerce_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10000000000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        candidate = raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _extract_usage(node: dict[str, Any]) -> tuple[int, int, int]:
    usage = node.get("usage") if isinstance(node.get("usage"), dict) else node

    input_tokens = _coerce_int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("inputTokenCount")
    )

    output_tokens = _coerce_int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("outputTokenCount")
    )

    cache_tokens = _coerce_int(
        usage.get("cache_tokens")
        or usage.get("cached_tokens")
        or usage.get("cached_input_tokens")
    )
    if cache_tokens == 0:
        cache_tokens = _coerce_int(usage.get("cache_read_input_tokens")) + _coerce_int(
            usage.get("cache_creation_input_tokens")
        )

    return input_tokens, cache_tokens, output_tokens


def _extract_codex_token_count_usage(node: dict[str, Any]) -> tuple[int, int, int] | None:
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
    cache_tokens = _coerce_int(last_token_usage.get("cached_input_tokens"))
    # In Codex token_count, input_tokens includes cached input. Align with
    # other tools by storing uncached input in input_tokens.
    input_tokens = max(0, _coerce_int(last_token_usage.get("input_tokens")) - cache_tokens)
    output_tokens = _coerce_int(last_token_usage.get("output_tokens"))
    return input_tokens, cache_tokens, output_tokens


def _extract_codex_turn_model(node: dict[str, Any]) -> str | None:
    if node.get("type") != "turn_context":
        return None
    payload = node.get("payload")
    if not isinstance(payload, dict):
        return None

    model = _extract_model(payload)
    if model != "unknown":
        return model

    collaboration_mode = payload.get("collaboration_mode")
    if not isinstance(collaboration_mode, dict):
        return None
    settings = collaboration_mode.get("settings")
    if not isinstance(settings, dict):
        return None
    nested_model = _extract_model(settings)
    return nested_model if nested_model != "unknown" else None


def _build_session_fingerprint(path: Path, tool: str) -> str | None:
    if tool == "codex":
        matches = re.findall(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            path.stem,
        )
        if matches:
            return f"codex:{matches[-1].lower()}"
        return f"codex_file:{path.stem}"

    if tool == "copilot_cli":
        session_id = path.parent.name.strip()
        if session_id:
            return f"copilot_cli:{session_id}"
        return f"copilot_cli_file:{path.stem}"

    return None


def _extract_model(node: dict[str, Any]) -> str:
    for key in ("model", "model_name", "modelName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _extract_time(node: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "created_at", "createdAt", "time", "date"):
        parsed = _parse_time(node.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_copilot_cli_events(
    node: dict[str, Any],
    fallback_time: datetime,
    source_ref: str,
    session_fingerprint: str | None,
) -> list[UsageEvent]:
    if node.get("type") != "session.shutdown":
        return []
    data = node.get("data")
    if not isinstance(data, dict):
        return []
    model_metrics = data.get("modelMetrics")
    if not isinstance(model_metrics, dict):
        return []

    event_time = _extract_time(node) or _parse_time(data.get("sessionStartTime")) or fallback_time
    session_prefix = session_fingerprint or "copilot_cli"
    out: list[UsageEvent] = []
    for model_name, metrics in model_metrics.items():
        if not isinstance(model_name, str) or not model_name.strip() or not isinstance(metrics, dict):
            continue
        usage = metrics.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = _coerce_int(usage.get("inputTokens"))
        output_tokens = _coerce_int(usage.get("outputTokens"))
        cache_tokens = _coerce_int(usage.get("cacheReadTokens")) + _coerce_int(
            usage.get("cacheWriteTokens")
        )
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            continue
        out.append(
            UsageEvent(
                tool="copilot_cli",
                model=model_name.strip(),
                event_time=event_time,
                input_tokens=input_tokens,
                cache_tokens=cache_tokens,
                output_tokens=output_tokens,
                session_fingerprint=f"{session_prefix}:{model_name.strip()}",
                source_ref=source_ref,
            )
        )
    return out


def _normalize_copilot_model(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if text.startswith("copilot/"):
        text = text[len("copilot/") :]
    return text


def _extract_copilot_vscode_usage(result: dict[str, Any]) -> tuple[int, int, int]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None

    input_tokens = 0
    output_tokens = 0
    cache_tokens = 0

    if usage is not None:
        input_tokens = _coerce_int(
            usage.get("promptTokens")
            or usage.get("inputTokens")
            or usage.get("prompt_tokens")
        )
        output_tokens = _coerce_int(
            usage.get("completionTokens")
            or usage.get("outputTokens")
            or usage.get("output_tokens")
        )
        cache_tokens = _coerce_int(
            usage.get("cachedInputTokens")
            or usage.get("cacheReadTokens")
            or usage.get("cached_input_tokens")
        )

    if input_tokens == 0 and output_tokens == 0:
        input_tokens = _coerce_int(result.get("promptTokens"))
        output_tokens = _coerce_int(
            result.get("outputTokens") or result.get("completionTokens")
        )

    if input_tokens == 0 and output_tokens == 0 and metadata is not None:
        input_tokens = _coerce_int(
            metadata.get("promptTokens") or metadata.get("inputTokens")
        )
        output_tokens = _coerce_int(
            metadata.get("outputTokens") or metadata.get("completionTokens")
        )
        if cache_tokens == 0:
            cache_tokens = _coerce_int(
                metadata.get("cachedInputTokens") or metadata.get("cacheReadTokens")
            )

    return input_tokens, cache_tokens, output_tokens


def _estimate_tokens_from_text(text: str) -> int:
    content = text.strip()
    if not content:
        return 0
    ascii_chars = sum(1 for ch in content if ord(ch) < 128)
    non_ascii_chars = len(content) - ascii_chars
    return max(1, int((ascii_chars * 0.25) + (non_ascii_chars * 0.6) + 0.999999))


def _collect_copilot_text_parts(value: Any) -> list[str]:
    parts: list[str] = []
    if isinstance(value, str):
        if value:
            parts.append(value)
        return parts
    if isinstance(value, list):
        for item in value:
            parts.extend(_collect_copilot_text_parts(item))
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
        for item in value["parts"]:
            parts.extend(_collect_copilot_text_parts(item))
    if isinstance(value.get("response"), list):
        for item in value["response"]:
            parts.extend(_collect_copilot_text_parts(item))
    return parts


def _extract_copilot_vscode_model(session: dict[str, Any], request: dict[str, Any]) -> str:
    for value in (
        request.get("modelId"),
        request.get("model"),
        request.get("selectedModel", {}).get("identifier")
        if isinstance(request.get("selectedModel"), dict)
        else None,
    ):
        normalized = _normalize_copilot_model(value)
        if normalized and normalized != "auto":
            return normalized

    agent = request.get("agent")
    if isinstance(agent, dict):
        normalized = _normalize_copilot_model(agent.get("modelId"))
        if normalized and normalized != "auto":
            return normalized

    result = request.get("result")
    if isinstance(result, dict):
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in ("modelId", "model", "id"):
                normalized = _normalize_copilot_model(metadata.get(key))
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
                    normalized = _normalize_copilot_model(metadata.get(key))
                    if normalized:
                        return normalized
            normalized = _normalize_copilot_model(selected_model.get("identifier"))
            if normalized:
                return normalized

    return "unknown"


def _build_copilot_vscode_event(
    session: dict[str, Any],
    request: dict[str, Any],
    fallback_time: datetime,
    source_ref: str,
) -> UsageEvent | None:
    session_id = session.get("sessionId")
    request_id = request.get("requestId")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if not isinstance(request_id, str) or not request_id.strip():
        return None

    result = request.get("result")
    if not isinstance(result, dict):
        return None
    input_tokens, cache_tokens, output_tokens = _extract_copilot_vscode_usage(result)
    if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
        input_text = "\n".join(_collect_copilot_text_parts(request.get("message")))
        output_text = "\n".join(_collect_copilot_text_parts(request.get("response")))
        if not output_text:
            output_text = "\n".join(_collect_copilot_text_parts(result))
        input_tokens = _estimate_tokens_from_text(input_text)
        output_tokens = _estimate_tokens_from_text(output_text)
        if input_tokens == 0 and output_tokens == 0:
            return None

    event_time = (
        _parse_time(request.get("timestamp"))
        or _extract_time(result)
        or fallback_time
    )
    return UsageEvent(
        tool="copilot_vscode",
        model=_extract_copilot_vscode_model(session, request),
        event_time=event_time,
        input_tokens=input_tokens,
        cache_tokens=cache_tokens,
        output_tokens=output_tokens,
        session_fingerprint=f"copilot_vscode:{session_id.strip()}:{request_id.strip()}",
        source_ref=source_ref,
    )


def _extract_copilot_vscode_events(
    node: dict[str, Any],
    fallback_time: datetime,
    source_ref: str,
) -> list[UsageEvent]:
    session = node.get("v") if node.get("kind") == 0 and isinstance(node.get("v"), dict) else node
    if not isinstance(session, dict):
        return []
    session_id = session.get("sessionId")
    requests = session.get("requests")
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(requests, list):
        return []

    out: list[UsageEvent] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        event = _build_copilot_vscode_event(session, request, fallback_time, source_ref)
        if event is not None:
            out.append(event)
    return out


def _apply_copilot_delta(state: Any, delta: dict[str, Any]) -> Any:
    kind = delta.get("kind")
    path = delta.get("k")
    value = delta.get("v")

    if kind == 0:
        return value if isinstance(value, (dict, list)) else state
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
        target: Any
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


def _extract_copilot_vscode_events_from_jsonl_text(
    text: str,
    fallback_time: datetime,
    source_ref: str,
) -> list[UsageEvent]:
    state: Any = {}
    saw_delta = False
    events: list[UsageEvent] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("kind"), int):
            saw_delta = True
            state = _apply_copilot_delta(state, obj)
            continue
        events.extend(
            _extract_copilot_vscode_events(
                obj,
                fallback_time=fallback_time,
                source_ref=f"{source_ref}:{idx}",
            )
        )

    if saw_delta and isinstance(state, dict):
        return _extract_copilot_vscode_events(
            state,
            fallback_time=fallback_time,
            source_ref=source_ref,
        )
    return events


def extract_usage_events_from_node(
    node: dict[str, Any],
    tool: str,
    fallback_time: datetime,
    source_ref: str,
    codex_model_hint: str | None = None,
    session_fingerprint: str | None = None,
) -> list[UsageEvent]:
    if tool == "copilot_cli":
        return _extract_copilot_cli_events(
            node,
            fallback_time=fallback_time,
            source_ref=source_ref,
            session_fingerprint=session_fingerprint,
        )

    if tool == "copilot_vscode":
        return _extract_copilot_vscode_events(
            node,
            fallback_time=fallback_time,
            source_ref=source_ref,
        )

    if tool == "codex":
        usage = _extract_codex_token_count_usage(node)
        if usage is None:
            return []
        input_tokens, cache_tokens, output_tokens = usage
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            return []
        event_time = _extract_time(node) or fallback_time
        model = _extract_model(node)
        if model == "unknown" and codex_model_hint:
            model = codex_model_hint
        return [
            UsageEvent(
                tool=tool,
                model=model,
                event_time=event_time,
                input_tokens=input_tokens,
                cache_tokens=cache_tokens,
                output_tokens=output_tokens,
                session_fingerprint=session_fingerprint,
                source_ref=source_ref,
            )
        ]

    out: list[UsageEvent] = []
    seen: set[tuple[str, int, int, int, str]] = set()
    for candidate in walk_json_nodes(node):
        input_tokens, cache_tokens, output_tokens = _extract_usage(candidate)
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            continue
        event_time = _extract_time(candidate) or fallback_time
        model = _extract_model(candidate)
        key = (tool, input_tokens, cache_tokens, output_tokens, event_time.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            UsageEvent(
                tool=tool,
                model=model,
                event_time=event_time,
                input_tokens=input_tokens,
                cache_tokens=cache_tokens,
                output_tokens=output_tokens,
                session_fingerprint=session_fingerprint,
                source_ref=source_ref,
            )
        )
    return out


def read_events_from_text(
    text: str,
    tool: str,
    source_ref: str,
    fallback_time: datetime,
    file_suffix: str,
    session_fingerprint_source: str | None = None,
) -> tuple[list[UsageEvent], str | None]:
    events: list[UsageEvent] = []
    codex_model_hint: str | None = None
    session_fingerprint = (
        _build_session_fingerprint(Path(session_fingerprint_source), tool)
        if session_fingerprint_source
        else None
    )
    try:
        if tool == "copilot_vscode" and file_suffix.lower() == ".jsonl":
            return (
                _extract_copilot_vscode_events_from_jsonl_text(
                    text=text,
                    fallback_time=fallback_time,
                    source_ref=source_ref,
                ),
                None,
            )

        if file_suffix.lower() == ".jsonl":
            for idx, raw in enumerate(text.splitlines(), start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tool == "codex":
                    turn_model = _extract_codex_turn_model(obj)
                    if turn_model:
                        codex_model_hint = turn_model
                events.extend(
                    extract_usage_events_from_node(
                        obj,
                        tool=tool,
                        fallback_time=fallback_time,
                        source_ref=f"{source_ref}:{idx}",
                        codex_model_hint=codex_model_hint,
                        session_fingerprint=session_fingerprint,
                    )
                )
            return events, None

        if file_suffix.lower() == ".json":
            obj = json.loads(text)
            if tool == "codex":
                for candidate in walk_json_nodes(obj):
                    turn_model = _extract_codex_turn_model(candidate)
                    if turn_model:
                        codex_model_hint = turn_model
            events.extend(
                extract_usage_events_from_node(
                    obj,
                    tool=tool,
                    fallback_time=fallback_time,
                    source_ref=source_ref,
                    codex_model_hint=codex_model_hint,
                    session_fingerprint=session_fingerprint,
                )
            )
            return events, None

        return [], None
    except json.JSONDecodeError as exc:
        return [], f"failed decoding {source_ref}: {exc}"


def read_events_from_file(path: Path, tool: str) -> tuple[list[UsageEvent], str | None]:
    fallback_time = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"failed reading {path}: {exc}"
    return read_events_from_text(
        text=text,
        tool=tool,
        source_ref=str(path),
        fallback_time=fallback_time,
        file_suffix=path.suffix,
        session_fingerprint_source=str(path),
    )
