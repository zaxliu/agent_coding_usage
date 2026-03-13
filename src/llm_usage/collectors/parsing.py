from __future__ import annotations

import json
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
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
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

    cache_tokens = _coerce_int(usage.get("cache_tokens") or usage.get("cached_tokens"))
    if cache_tokens == 0:
        cache_tokens = _coerce_int(usage.get("cache_read_input_tokens")) + _coerce_int(
            usage.get("cache_creation_input_tokens")
        )

    return input_tokens, cache_tokens, output_tokens


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


def extract_usage_events_from_node(
    node: dict[str, Any],
    tool: str,
    fallback_time: datetime,
    source_ref: str,
) -> list[UsageEvent]:
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
                source_ref=source_ref,
            )
        )
    return out


def read_events_from_file(path: Path, tool: str) -> tuple[list[UsageEvent], str | None]:
    fallback_time = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    events: list[UsageEvent] = []
    try:
        if path.suffix.lower() == ".jsonl":
            for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.extend(
                    extract_usage_events_from_node(
                        obj,
                        tool=tool,
                        fallback_time=fallback_time,
                        source_ref=f"{path}:{idx}",
                    )
                )
            return events, None

        if path.suffix.lower() == ".json":
            obj = json.loads(path.read_text(encoding="utf-8"))
            events.extend(
                extract_usage_events_from_node(
                    obj,
                    tool=tool,
                    fallback_time=fallback_time,
                    source_ref=str(path),
                )
            )
            return events, None

        return [], None
    except OSError as exc:
        return [], f"failed reading {path}: {exc}"
    except json.JSONDecodeError as exc:
        return [], f"failed decoding {path}: {exc}"
