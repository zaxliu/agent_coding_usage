from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UsageEvent:
    tool: str
    model: str
    event_time: datetime
    input_tokens: int
    cache_tokens: int
    output_tokens: int
    session_fingerprint: str | None = None
    source_ref: str | None = None
    source_host_hash: str = ""


@dataclass(frozen=True)
class AggregateRecord:
    date_local: str
    user_hash: str
    source_host_hash: str
    tool: str
    model: str
    input_tokens_sum: int
    cache_tokens_sum: int
    output_tokens_sum: int
    row_key: str
    updated_at: str
