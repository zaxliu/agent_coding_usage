from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .models import AggregateRecord


def _group_terminal_rows(rows: list[AggregateRecord]) -> list[AggregateRecord]:
    buckets: dict[tuple[str, str, str], dict[str, int | AggregateRecord]] = defaultdict(
        lambda: {
            "input_tokens_sum": 0,
            "cache_tokens_sum": 0,
            "output_tokens_sum": 0,
            "sample": None,
        }
    )

    for row in rows:
        key = (row.date_local, row.tool, row.model)
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
                source_host_hash="",
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


def print_terminal_report(rows: list[AggregateRecord]) -> None:
    headers = ["日期", "工具", "模型", "输入", "缓存", "输出"]
    widths = [10, 10, 28, 10, 10, 10]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in _group_terminal_rows(rows):
        data = [
            row.date_local,
            row.tool,
            row.model[:28],
            str(row.input_tokens_sum),
            str(row.cache_tokens_sum),
            str(row.output_tokens_sum),
        ]
        print(" | ".join(v.ljust(w) for v, w in zip(data, widths)))


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
