from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .identity import build_row_key
from .models import AggregateRecord, UsageEvent


def aggregate_events(
    events: list[UsageEvent],
    user_hash: str,
    timezone_name: str,
    now: datetime | None = None,
) -> list[AggregateRecord]:
    tz = ZoneInfo(timezone_name)
    curr = (now or datetime.now(tz)).astimezone(tz)

    buckets: dict[tuple[str, str, str, str], dict[str, object]] = defaultdict(
        lambda: {
            "input": 0,
            "cache": 0,
            "output": 0,
            "model": "unknown",
            "model_time": None,
            "session_fingerprint": None,
            "source_host_hash": "",
        }
    )

    for event in events:
        local_date = event.event_time.astimezone(tz).date().isoformat()
        identity = event.session_fingerprint.strip() if event.session_fingerprint else f"model:{event.model}"
        key = (local_date, event.tool, event.source_host_hash, identity)
        buckets[key]["input"] += max(0, event.input_tokens)
        buckets[key]["cache"] += max(0, event.cache_tokens)
        buckets[key]["output"] += max(0, event.output_tokens)
        buckets[key]["source_host_hash"] = event.source_host_hash
        if event.session_fingerprint:
            buckets[key]["session_fingerprint"] = event.session_fingerprint.strip()
        if event.model != "unknown":
            model_time = buckets[key]["model_time"]
            if not isinstance(model_time, datetime) or event.event_time >= model_time:
                buckets[key]["model"] = event.model
                buckets[key]["model_time"] = event.event_time

    out: list[AggregateRecord] = []
    updated_at = curr.isoformat()
    for (date_local, tool, source_host_hash, _identity), sums in sorted(buckets.items()):
        model = str(sums["model"])
        session_fingerprint = sums["session_fingerprint"]
        if not isinstance(session_fingerprint, str):
            session_fingerprint = None
        out.append(
            AggregateRecord(
                date_local=date_local,
                user_hash=user_hash,
                source_host_hash=source_host_hash,
                tool=tool,
                model=model,
                input_tokens_sum=sums["input"],
                cache_tokens_sum=sums["cache"],
                output_tokens_sum=sums["output"],
                row_key=build_row_key(
                    user_hash,
                    source_host_hash,
                    date_local,
                    tool,
                    model,
                    session_fingerprint=session_fingerprint,
                ),
                updated_at=updated_at,
            )
        )
    return out
