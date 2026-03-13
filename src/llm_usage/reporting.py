from __future__ import annotations

import csv
from pathlib import Path

from .models import AggregateRecord


def print_terminal_report(rows: list[AggregateRecord]) -> None:
    headers = ["date", "tool", "model", "input", "cache", "output"]
    widths = [10, 10, 28, 10, 10, 10]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
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
