from __future__ import annotations

from .models import AggregateRecord


UPLOAD_FIELDS = {
    "date_local",
    "user_hash",
    "source_host_hash",
    "tool",
    "model",
    "input_tokens_sum",
    "cache_tokens_sum",
    "output_tokens_sum",
    "row_key",
    "updated_at",
}


def to_feishu_fields(row: AggregateRecord) -> dict[str, object]:
    raw = row.__dict__.copy()
    return {k: raw[k] for k in UPLOAD_FIELDS}
