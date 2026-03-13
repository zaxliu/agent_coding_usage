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

    buckets: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"input": 0, "cache": 0, "output": 0}
    )

    for event in events:
        local_date = event.event_time.astimezone(tz).date().isoformat()
        key = (local_date, event.tool, event.model)
        buckets[key]["input"] += max(0, event.input_tokens)
        buckets[key]["cache"] += max(0, event.cache_tokens)
        buckets[key]["output"] += max(0, event.output_tokens)

    out: list[AggregateRecord] = []
    updated_at = curr.isoformat()
    for (date_local, tool, model), sums in sorted(buckets.items()):
        out.append(
            AggregateRecord(
                date_local=date_local,
                user_hash=user_hash,
                tool=tool,
                model=model,
                input_tokens_sum=sums["input"],
                cache_tokens_sum=sums["cache"],
                output_tokens_sum=sums["output"],
                row_key=build_row_key(user_hash, date_local, tool, model),
                updated_at=updated_at,
            )
        )
    return out
