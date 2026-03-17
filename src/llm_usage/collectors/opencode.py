"""OpenCode token usage collector.

Collects token usage from OpenCode's SQLite database (~/.local/share/opencode/opencode.db).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput


def _get_opencode_db_path() -> Path:
    """Get OpenCode database path from env or default location."""
    env_path = os.environ.get("OPENCODE_DB_PATH", "")
    if env_path:
        return Path(env_path).expanduser()

    # Default location: ~/.local/share/opencode/opencode.db
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _parse_timestamp(ts: int | None) -> datetime:
    """Parse OpenCode timestamp (milliseconds) to datetime."""
    if ts is None:
        return datetime.now(timezone.utc)
    # OpenCode uses milliseconds
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


def _extract_tokens_from_part_data(data: str) -> tuple[int, int, int] | None:
    """Extract token usage from part data JSON.

    Returns (input_tokens, cache_tokens, output_tokens) or None if not a token record.
    """
    try:
        obj: dict[str, Any] = json.loads(data)
    except json.JSONDecodeError:
        return None

    # Only process step-finish events with tokens
    if obj.get("type") != "step-finish":
        return None

    tokens = obj.get("tokens")
    if not isinstance(tokens, dict):
        return None

    input_tokens = int(tokens.get("input", 0) or 0)
    output_tokens = int(tokens.get("output", 0) or 0)

    # Cache can be nested: {"read": N, "write": M} or flat number
    cache_info = tokens.get("cache")
    if isinstance(cache_info, dict):
        cache_tokens = int(cache_info.get("read", 0) or 0) + int(cache_info.get("write", 0) or 0)
    elif isinstance(cache_info, (int, float)):
        cache_tokens = int(cache_info)
    else:
        cache_tokens = 0

    return input_tokens, cache_tokens, output_tokens


def _extract_model_from_part_data(data: str) -> str:
    """Extract model name from part data JSON.

    Looks for model info in step-start events.
    """
    try:
        obj: dict[str, Any] = json.loads(data)
    except json.JSONDecodeError:
        return "unknown"

    # Check for model in various places
    if obj.get("type") == "step-start":
        # Some step-start events may have model info
        model = obj.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()

    # Also check for model in nested structures
    for key in ("model", "model_name", "modelName"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return "unknown"


class OpenCodeCollector(BaseCollector):
    """Collector for OpenCode token usage from SQLite database."""

    name: str = "opencode"
    source_name: str = "local"
    source_host_hash: str = ""
    db_path: Path

    def __init__(
        self,
        source_name: str = "local",
        source_host_hash: str = "",
        db_path: Path | None = None,
    ) -> None:
        self.source_name = source_name
        self.source_host_hash = source_host_hash
        self.db_path = db_path or _get_opencode_db_path()

    def probe(self) -> tuple[bool, str]:
        """Check if OpenCode database exists and has data."""
        if not self.db_path.exists():
            return False, f"OpenCode database not found at {self.db_path}"

        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM part WHERE data LIKE '%tokens%'")
            count: int = cursor.fetchone()[0]  # type: ignore
            conn.close()
            if count == 0:
                return False, "OpenCode database exists but no token records found"
            return True, f"OpenCode database found with {count} token records"
        except sqlite3.Error as e:
            return False, f"Failed to read OpenCode database: {e}"

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        """Collect token usage events from OpenCode database."""
        events: list[UsageEvent] = []
        warnings: list[str] = []

        if not self.db_path.exists():
            warnings.append(f"OpenCode database not found at {self.db_path}")
            return CollectOutput(events=events, warnings=warnings)

        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Query parts with Token Data
            # Join with Message and Session to get timestamps
            # Use LIKE patterns that match both compact and pretty-printed JSON
            query = """
                SELECT p.data, p.time_created, s.directory
                FROM part p
                JOIN message m ON p.message_id = m.id
                JOIN session s ON m.session_id = s.id
                WHERE p.data LIKE '%"type"%step-finish%'
                AND p.data LIKE '%tokens%'
                ORDER BY p.time_created
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            for data, time_created, directory in rows:
                tokens = _extract_tokens_from_part_data(str(data))
                if tokens is None:
                    continue

                input_tokens, cache_tokens, output_tokens = tokens
                if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                    continue

                event_time = _parse_timestamp(int(time_created)) if time_created else datetime.now(timezone.utc)

                # Filter by time range
                if not (start <= event_time <= end):
                    continue

                model = _extract_model_from_part_data(str(data))

                events.append(
                    UsageEvent(
                        tool=self.name,
                        model=model,
                        event_time=event_time,
                        input_tokens=input_tokens,
                        cache_tokens=cache_tokens,
                        output_tokens=output_tokens,
                        source_ref=f"opencode:{directory}",
                    )
                )

            if not events:
                warnings.append(f"{self.name}: no usage events in selected time range")

        except sqlite3.Error as e:
            warnings.append(f"Failed to read OpenCode database: {e}")

        return CollectOutput(events=events, warnings=warnings)


def build_opencode_collector(
    source_name: str = "local",
    source_host_hash: str = "",
) -> OpenCodeCollector:
    """Build an OpenCode collector instance."""
    return OpenCodeCollector(
        source_name=source_name,
        source_host_hash=source_host_hash,
    )
