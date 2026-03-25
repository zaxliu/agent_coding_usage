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


def _extract_copilot_vscode_model(session: dict[str, Any], request: dict[str, Any]) -> str:
    agent = request.get("agent")
    if isinstance(agent, dict):
        model_id = agent.get("modelId")
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() != "copilot/auto":
            return model_id.strip()

    result = request.get("result")
    if isinstance(result, dict):
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
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()

    return "unknown"


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
        request_id = request.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            continue
        event_time = _parse_time(request.get("timestamp")) or fallback_time
        out.append(
            UsageEvent(
                tool="copilot_vscode",
                model=_extract_copilot_vscode_model(session, request),
                event_time=event_time,
                input_tokens=0,
                cache_tokens=0,
                output_tokens=0,
                session_fingerprint=f"copilot_vscode:{session_id.strip()}:{request_id.strip()}",
                source_ref=source_ref,
            )
        )
    return out


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
