from __future__ import annotations

from .feishu_schema import REQUIRED_FEISHU_FIELDS, field_names
from .models import AggregateRecord


UPLOAD_FIELD_ORDER = tuple(field_names(REQUIRED_FEISHU_FIELDS))
UPLOAD_FIELDS = set(UPLOAD_FIELD_ORDER)


def to_feishu_fields(row: AggregateRecord) -> dict[str, object]:
    raw = row.__dict__.copy()
    return {k: raw[k] for k in UPLOAD_FIELD_ORDER}
