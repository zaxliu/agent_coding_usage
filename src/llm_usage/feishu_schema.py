from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class FeishuFieldSpec:
    """Standard Bitable column expected for usage aggregate uploads."""

    name: str
    field_type: str
    warn_only_type_mismatch: bool = True

    def feishu_type(self) -> int:
        """Feishu Bitable API type: 1 text, 2 number, 5 date/time (ms since epoch)."""
        mapping = {"text": 1, "number": 2, "datetime": 5}
        try:
            return mapping[self.field_type]
        except KeyError as exc:
            raise ValueError(f"unknown feishu field_type: {self.field_type!r}") from exc


REQUIRED_FEISHU_FIELDS: tuple[FeishuFieldSpec, ...] = (
    FeishuFieldSpec("date_local", "datetime"),
    FeishuFieldSpec("user_hash", "text"),
    FeishuFieldSpec("source_host_hash", "text"),
    FeishuFieldSpec("tool", "text"),
    FeishuFieldSpec("model", "text"),
    FeishuFieldSpec("input_tokens_sum", "number"),
    FeishuFieldSpec("cache_tokens_sum", "number"),
    FeishuFieldSpec("output_tokens_sum", "number"),
    FeishuFieldSpec("row_key", "text"),
    FeishuFieldSpec("updated_at", "datetime"),
)


def field_names(fields: Sequence[FeishuFieldSpec]) -> list[str]:
    return [item.name for item in fields]


def feishu_schema_warnings(
    field_type_map: Mapping[str, int],
    specs: Sequence[FeishuFieldSpec] = REQUIRED_FEISHU_FIELDS,
) -> list[str]:
    """Human-readable warnings for doctor: missing columns and optional type mismatches."""
    warnings: list[str] = []
    for spec in specs:
        if spec.name not in field_type_map:
            warnings.append(f"missing column: {spec.name}")
            continue
        actual = field_type_map[spec.name]
        expected = spec.feishu_type()
        if actual != expected and spec.warn_only_type_mismatch:
            warnings.append(
                f"type mismatch for {spec.name}: table has type {actual}, expected {expected}"
            )
    return warnings
