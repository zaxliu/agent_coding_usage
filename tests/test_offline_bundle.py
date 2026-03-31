from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from llm_usage.models import AggregateRecord
from llm_usage.offline_bundle import (
    OfflineBundleError,
    read_offline_bundle,
    validate_offline_bundle,
    write_offline_bundle,
)


def _row(
    *,
    date_local: str = "2026-03-31",
    user_hash: str = "user-hash",
    source_host_hash: str = "source-a",
    tool: str = "codex",
    model: str = "gpt-5",
    input_tokens_sum: int = 10,
    cache_tokens_sum: int = 2,
    output_tokens_sum: int = 3,
    row_key: str = "row-key",
    updated_at: str = "2026-03-31T12:00:00+08:00",
) -> AggregateRecord:
    return AggregateRecord(
        date_local=date_local,
        user_hash=user_hash,
        source_host_hash=source_host_hash,
        tool=tool,
        model=model,
        input_tokens_sum=input_tokens_sum,
        cache_tokens_sum=cache_tokens_sum,
        output_tokens_sum=output_tokens_sum,
        row_key=row_key,
        updated_at=updated_at,
    )


def _write_bundle_members(path: Path, *, manifest: dict[str, object], rows: list[dict[str, object]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(
            "rows.jsonl",
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else ""),
        )


def test_write_offline_bundle_writes_zip_with_manifest_rows_and_csv(tmp_path):
    path = write_offline_bundle(
        [_row()],
        tmp_path / "bundle.zip",
        warnings=["cursor local logs unavailable"],
        timezone_name="Asia/Shanghai",
        lookback_days=30,
        tool_version="0.1.1",
        include_csv=True,
    )

    assert path == tmp_path / "bundle.zip"
    assert path.exists()

    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"manifest.json", "rows.jsonl", "usage_report.csv"}
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        rows_jsonl = archive.read("rows.jsonl").decode("utf-8")
        csv_text = archive.read("usage_report.csv").decode("utf-8")

    assert manifest["schema_version"] == 1
    assert manifest["bundle_kind"] == "aggregate_rows"
    assert manifest["timezone"] == "Asia/Shanghai"
    assert manifest["lookback_days"] == 30
    assert manifest["row_count"] == 1
    assert manifest["warning_count"] == 1
    assert manifest["warnings"] == ["cursor local logs unavailable"]
    assert '"row_key": "row-key"' in rows_jsonl
    assert "row-key" in csv_text


def test_read_offline_bundle_round_trips_rows_warnings_and_manifest(tmp_path):
    row = _row(row_key="row-a")
    path = write_offline_bundle(
        [row],
        tmp_path / "bundle.zip",
        warnings=["warn-a"],
        timezone_name="UTC",
        lookback_days=7,
        tool_version="0.1.1",
    )

    rows, warnings, manifest = read_offline_bundle(path)

    assert rows == [row]
    assert warnings == ["warn-a"]
    assert manifest["bundle_kind"] == "aggregate_rows"
    assert manifest["row_count"] == 1


def test_read_offline_bundle_accepts_directory_input(tmp_path):
    row = _row(row_key="row-a")
    zip_path = write_offline_bundle(
        [row],
        tmp_path / "bundle.zip",
        warnings=[],
        timezone_name="UTC",
        lookback_days=7,
        tool_version="0.1.1",
    )
    bundle_dir = tmp_path / "bundle-dir"
    bundle_dir.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(bundle_dir)

    rows, warnings, manifest = read_offline_bundle(bundle_dir)

    assert rows == [row]
    assert warnings == []
    assert manifest["timezone"] == "UTC"


def test_validate_offline_bundle_rejects_missing_manifest(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle_path, "w") as archive:
        archive.writestr("rows.jsonl", "")

    with pytest.raises(OfflineBundleError, match="manifest.json"):
        validate_offline_bundle(bundle_path)


def test_validate_offline_bundle_rejects_row_count_mismatch(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    row = _row().__dict__
    _write_bundle_members(
        bundle_path,
        manifest={
            "schema_version": 1,
            "bundle_kind": "aggregate_rows",
            "generated_at": "2026-03-31T12:00:00+08:00",
            "tool_version": "0.1.1",
            "timezone": "UTC",
            "lookback_days": 7,
            "row_count": 2,
            "warning_count": 0,
            "warnings": [],
        },
        rows=[row],
    )

    with pytest.raises(OfflineBundleError, match="row_count"):
        validate_offline_bundle(bundle_path)


def test_validate_offline_bundle_rejects_extra_row_fields(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    row = _row().__dict__.copy()
    row["unexpected"] = "nope"
    _write_bundle_members(
        bundle_path,
        manifest={
            "schema_version": 1,
            "bundle_kind": "aggregate_rows",
            "generated_at": "2026-03-31T12:00:00+08:00",
            "tool_version": "0.1.1",
            "timezone": "UTC",
            "lookback_days": 7,
            "row_count": 1,
            "warning_count": 0,
            "warnings": [],
        },
        rows=[row],
    )

    with pytest.raises(OfflineBundleError, match="unexpected"):
        validate_offline_bundle(bundle_path)


def test_validate_offline_bundle_rejects_duplicate_row_keys(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    row_a = _row(row_key="dup").__dict__
    row_b = _row(source_host_hash="source-b", row_key="dup").__dict__
    _write_bundle_members(
        bundle_path,
        manifest={
            "schema_version": 1,
            "bundle_kind": "aggregate_rows",
            "generated_at": "2026-03-31T12:00:00+08:00",
            "tool_version": "0.1.1",
            "timezone": "UTC",
            "lookback_days": 7,
            "row_count": 2,
            "warning_count": 0,
            "warnings": [],
        },
        rows=[row_a, row_b],
    )

    with pytest.raises(OfflineBundleError, match="row_key"):
        validate_offline_bundle(bundle_path)


def test_validate_offline_bundle_rejects_fractional_token_counts(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    row = _row().__dict__.copy()
    row["input_tokens_sum"] = 10.5
    _write_bundle_members(
        bundle_path,
        manifest={
            "schema_version": 1,
            "bundle_kind": "aggregate_rows",
            "generated_at": "2026-03-31T12:00:00+08:00",
            "tool_version": "0.1.1",
            "timezone": "UTC",
            "lookback_days": 7,
            "row_count": 1,
            "warning_count": 0,
            "warnings": [],
        },
        rows=[row],
    )

    with pytest.raises(OfflineBundleError, match="input_tokens_sum"):
        validate_offline_bundle(bundle_path)


def test_validate_offline_bundle_rejects_empty_source_host_hash(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    row = _row(source_host_hash="").__dict__
    _write_bundle_members(
        bundle_path,
        manifest={
            "schema_version": 1,
            "bundle_kind": "aggregate_rows",
            "generated_at": "2026-03-31T12:00:00+08:00",
            "tool_version": "0.1.1",
            "timezone": "UTC",
            "lookback_days": 7,
            "row_count": 1,
            "warning_count": 0,
            "warnings": [],
        },
        rows=[row],
    )

    with pytest.raises(OfflineBundleError, match="source_host_hash"):
        validate_offline_bundle(bundle_path)
