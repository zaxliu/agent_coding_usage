from __future__ import annotations

import csv
from datetime import datetime
import io
import json
from pathlib import Path
from typing import Iterable
import zipfile
from zoneinfo import ZoneInfo

from .models import AggregateRecord


BUNDLE_SCHEMA_VERSION = 1
BUNDLE_KIND_AGGREGATE_ROWS = "aggregate_rows"
MANIFEST_FILENAME = "manifest.json"
ROWS_FILENAME = "rows.jsonl"
CSV_FILENAME = "usage_report.csv"
ROW_FIELDNAMES = (
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
)
MANIFEST_REQUIRED_FIELDS = (
    "schema_version",
    "bundle_kind",
    "generated_at",
    "tool_version",
    "timezone",
    "lookback_days",
    "row_count",
    "warning_count",
    "warnings",
)


class OfflineBundleError(ValueError):
    pass


def write_offline_bundle(
    rows: list[AggregateRecord],
    output_path: Path,
    *,
    warnings: list[str],
    timezone_name: str,
    lookback_days: int,
    tool_version: str,
    include_csv: bool = True,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_kind": BUNDLE_KIND_AGGREGATE_ROWS,
        "generated_at": datetime.now(ZoneInfo(timezone_name)).isoformat(),
        "tool_version": tool_version,
        "timezone": timezone_name,
        "lookback_days": lookback_days,
        "row_count": len(rows),
        "warning_count": len(warnings),
        "warnings": list(warnings),
    }
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_FILENAME, json.dumps(manifest, ensure_ascii=True, indent=2) + "\n")
        archive.writestr(ROWS_FILENAME, _render_rows_jsonl(rows))
        if include_csv:
            archive.writestr(CSV_FILENAME, _render_rows_csv(rows))
    return output_path


def read_offline_bundle(path: Path) -> tuple[list[AggregateRecord], list[str], dict[str, object]]:
    return validate_offline_bundle(path)


def validate_offline_bundle(path: Path) -> tuple[list[AggregateRecord], list[str], dict[str, object]]:
    members = _read_bundle_members(path)
    if MANIFEST_FILENAME not in members:
        raise OfflineBundleError(f"bundle missing required file: {MANIFEST_FILENAME}")
    if ROWS_FILENAME not in members:
        raise OfflineBundleError(f"bundle missing required file: {ROWS_FILENAME}")

    manifest = _parse_manifest(members[MANIFEST_FILENAME])
    rows = _parse_rows(members[ROWS_FILENAME])
    if manifest["row_count"] != len(rows):
        raise OfflineBundleError(
            f"manifest row_count={manifest['row_count']} does not match {ROWS_FILENAME} lines={len(rows)}"
        )
    if manifest["warning_count"] != len(manifest["warnings"]):
        raise OfflineBundleError("manifest warning_count does not match warnings length")

    warnings = list(manifest["warnings"])
    extras = sorted(set(members) - {MANIFEST_FILENAME, ROWS_FILENAME, CSV_FILENAME})
    if extras:
        warnings.append(f"extra bundle files ignored: {', '.join(extras)}")
    return rows, warnings, manifest


def _read_bundle_members(path: Path) -> dict[str, bytes]:
    path = Path(path)
    if path.is_dir():
        members: dict[str, bytes] = {}
        for item in path.iterdir():
            if item.is_file():
                members[item.name] = item.read_bytes()
        return members
    if path.is_file():
        with zipfile.ZipFile(path) as archive:
            return {info.filename: archive.read(info.filename) for info in archive.infolist() if not info.is_dir()}
    raise OfflineBundleError(f"bundle path not found: {path}")


def _parse_manifest(raw_bytes: bytes) -> dict[str, object]:
    try:
        manifest = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineBundleError(f"invalid {MANIFEST_FILENAME}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise OfflineBundleError(f"{MANIFEST_FILENAME} must contain a JSON object")
    missing = [field for field in MANIFEST_REQUIRED_FIELDS if field not in manifest]
    if missing:
        raise OfflineBundleError(f"{MANIFEST_FILENAME} missing required fields: {', '.join(missing)}")
    if manifest["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise OfflineBundleError(f"unsupported schema_version: {manifest['schema_version']}")
    if manifest["bundle_kind"] != BUNDLE_KIND_AGGREGATE_ROWS:
        raise OfflineBundleError(f"unsupported bundle_kind: {manifest['bundle_kind']}")
    if not isinstance(manifest["warnings"], list) or not all(isinstance(item, str) for item in manifest["warnings"]):
        raise OfflineBundleError(f"{MANIFEST_FILENAME} warnings must be a string list")
    if not isinstance(manifest["generated_at"], str) or not manifest["generated_at"].strip():
        raise OfflineBundleError(f"{MANIFEST_FILENAME} generated_at must be a non-empty string")
    _parse_datetime(manifest["generated_at"], "generated_at")
    manifest["lookback_days"] = _require_non_negative_int(manifest["lookback_days"], "lookback_days")
    manifest["row_count"] = _require_non_negative_int(manifest["row_count"], "row_count")
    manifest["warning_count"] = _require_non_negative_int(manifest["warning_count"], "warning_count")
    for field in ("tool_version", "timezone"):
        if not isinstance(manifest[field], str) or not manifest[field].strip():
            raise OfflineBundleError(f"{MANIFEST_FILENAME} {field} must be a non-empty string")
    return manifest


def _parse_rows(raw_bytes: bytes) -> list[AggregateRecord]:
    text = raw_bytes.decode("utf-8")
    rows: list[AggregateRecord] = []
    user_hashes: set[str] = set()
    row_keys: set[str] = set()
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} must be a JSON object")
        extra_fields = sorted(set(payload) - set(ROW_FIELDNAMES))
        if extra_fields:
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} has unexpected fields: {', '.join(extra_fields)}")
        missing_fields = [field for field in ROW_FIELDNAMES if field not in payload]
        if missing_fields:
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} missing fields: {', '.join(missing_fields)}")
        for field in ("date_local", "user_hash", "tool", "model", "row_key", "updated_at"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise OfflineBundleError(f"{ROWS_FILENAME} line {index} field {field} must be a non-empty string")
        if not isinstance(payload["source_host_hash"], str) or not payload["source_host_hash"].strip():
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} field source_host_hash must be a non-empty string")
        _parse_datetime(payload["updated_at"], f"{ROWS_FILENAME} line {index} updated_at")
        for field in ("input_tokens_sum", "cache_tokens_sum", "output_tokens_sum"):
            payload[field] = _require_non_negative_int(payload[field], f"{ROWS_FILENAME} line {index} field {field}")
        if payload["row_key"] in row_keys:
            raise OfflineBundleError(f"{ROWS_FILENAME} line {index} duplicates row_key {payload['row_key']}")
        row_keys.add(payload["row_key"])
        user_hashes.add(payload["user_hash"])
        rows.append(AggregateRecord(**payload))
    if len(user_hashes) > 1:
        raise OfflineBundleError(f"{ROWS_FILENAME} must contain exactly one user_hash")
    return rows


def _require_non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise OfflineBundleError(f"{label} must be a non-negative integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise OfflineBundleError(f"{label} must be a non-negative integer")
        value = int(value)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise OfflineBundleError(f"{label} must be a non-negative integer") from exc
    if number < 0:
        raise OfflineBundleError(f"{label} must be a non-negative integer")
    return number


def _parse_datetime(value: str, label: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OfflineBundleError(f"{label} must be a valid ISO datetime") from exc


def _render_rows_jsonl(rows: Iterable[AggregateRecord]) -> str:
    return "".join(json.dumps(row.__dict__, ensure_ascii=True, sort_keys=True) + "\n" for row in rows)


def _render_rows_csv(rows: Iterable[AggregateRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(ROW_FIELDNAMES))
    writer.writeheader()
    for row in rows:
        writer.writerow(row.__dict__)
    return buffer.getvalue()
