from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

from .models import AggregateRecord


def _group_terminal_rows(rows: list[AggregateRecord]) -> list[AggregateRecord]:
    buckets: dict[tuple[str, str, str, str], dict[str, Union[int, AggregateRecord]]] = defaultdict(
        lambda: {
            "input_tokens_sum": 0,
            "cache_tokens_sum": 0,
            "output_tokens_sum": 0,
            "sample": None,
        }
    )

    for row in rows:
        key = (row.date_local, row.source_host_hash, row.tool, row.model)
        bucket = buckets[key]
        bucket["input_tokens_sum"] += row.input_tokens_sum
        bucket["cache_tokens_sum"] += row.cache_tokens_sum
        bucket["output_tokens_sum"] += row.output_tokens_sum
        if bucket["sample"] is None:
            bucket["sample"] = row

    grouped_rows: list[AggregateRecord] = []
    for _key, bucket in sorted(buckets.items()):
        sample = bucket["sample"]
        if not isinstance(sample, AggregateRecord):
            continue
        grouped_rows.append(
            AggregateRecord(
                date_local=sample.date_local,
                user_hash=sample.user_hash,
                source_host_hash=sample.source_host_hash,
                tool=sample.tool,
                model=sample.model,
                input_tokens_sum=int(bucket["input_tokens_sum"]),
                cache_tokens_sum=int(bucket["cache_tokens_sum"]),
                output_tokens_sum=int(bucket["output_tokens_sum"]),
                row_key=sample.row_key,
                updated_at=sample.updated_at,
            )
        )
    return grouped_rows


def _host_display_cell(source_host_hash: str, host_labels: dict[str, str]) -> str:
    if not source_host_hash:
        return "local"
    if source_host_hash in host_labels:
        return host_labels[source_host_hash]
    return source_host_hash[:8]


def _terminal_column_widths(headers: list[str], data_rows: list[list[str]]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in data_rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    return widths


def print_terminal_report(
    rows: list[AggregateRecord],
    *,
    host_labels: Optional[dict[str, str]] = None,
) -> None:
    labels = host_labels or {}
    grouped = _group_terminal_rows(rows)
    headers = ["日期", "Host", "工具", "模型", "输入", "缓存", "输出"]

    data_rows: list[list[str]] = []
    for row in grouped:
        data_rows.append(
            [
                row.date_local,
                _host_display_cell(row.source_host_hash, labels),
                row.tool,
                row.model,
                str(row.input_tokens_sum),
                str(row.cache_tokens_sum),
                str(row.output_tokens_sum),
            ]
        )

    widths = _terminal_column_widths(headers, data_rows)
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for cells in data_rows:
        print(" | ".join(v.ljust(w) for v, w in zip(cells, widths)))


def write_csv_report(rows: list[AggregateRecord], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / "usage_report.csv"
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
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
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
    return filename
