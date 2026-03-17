from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput


class OpenCodeCollector(BaseCollector):
    name = "opencode"
    DEFAULT_DB_PATHS = [
        "~/.local/share/opencode/opencode.db",
    ]

    def __init__(
        self,
        source_name: str = "local",
        source_host_hash: str = "",
        db_paths: list[str] | None = None,
    ) -> None:
        self.source_name = source_name
        self.source_host_hash = source_host_hash
        if db_paths is None:
            env_paths = os.getenv("OPENCODE_DB_PATHS", "").strip()
            if env_paths:
                db_paths = [p.strip() for p in env_paths.split(",") if p.strip()]
            else:
                db_paths = self.DEFAULT_DB_PATHS
        self.db_paths = [Path(p).expanduser() for p in db_paths]

    def _find_db(self) -> Path | None:
        for p in self.db_paths:
            if p.exists() and p.is_file():
                return p
        return None

    def probe(self) -> tuple[bool, str]:
        db_path = self._find_db()
        if db_path is None:
            paths_str = ", ".join(str(p) for p in self.db_paths)
            return False, f"no OpenCode database found at: {paths_str}"
        return True, f"database found at {db_path}"

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        db_path = self._find_db()
        if db_path is None:
            return CollectOutput(
                events=[],
                warnings=["opencode: database not found"],
            )

        events: list[UsageEvent] = []
        warnings: list[str] = []

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            query = """
                SELECT
                    id,
                    session_id,
                    time_created,
                    json_extract(data, '$.modelID') as model_id,
                    json_extract(data, '$.tokens.input') as input_tokens,
                    json_extract(data, '$.tokens.output') as output_tokens,
                    json_extract(data, '$.tokens.cache.read') as cache_read_tokens,
                    json_extract(data, '$.tokens.cache.write') as cache_write_tokens
                FROM message
                WHERE time_created >= ? AND time_created <= ?
                AND json_extract(data, '$.tokens') IS NOT NULL
                ORDER BY time_created
            """

            cursor.execute(query, (start_ms, end_ms))
            rows = cursor.fetchall()

            for row in rows:
                event_time = datetime.fromtimestamp(
                    row["time_created"] / 1000, tz=timezone.utc
                )

                model = row["model_id"] or "unknown"
                input_tokens = row["input_tokens"] or 0
                output_tokens = row["output_tokens"] or 0
                cache_read = row["cache_read_tokens"] or 0
                cache_write = row["cache_write_tokens"] or 0
                cache_tokens = cache_read + cache_write

                if input_tokens == 0 and output_tokens == 0 and cache_tokens == 0:
                    continue

                events.append(
                    UsageEvent(
                        tool="opencode",
                        model=model,
                        event_time=event_time,
                        input_tokens=input_tokens,
                        cache_tokens=cache_tokens,
                        output_tokens=output_tokens,
                        session_fingerprint=f"opencode:{row['session_id']}",
                        source_ref=f"{db_path}:{row['id']}",
                        source_host_hash=self.source_host_hash,
                    )
                )

            conn.close()

        except sqlite3.Error as e:
            warnings.append(f"opencode: database error: {e}")
        except Exception as e:
            warnings.append(f"opencode: unexpected error: {e}")

        if not events:
            warnings.append("opencode: no usage events in selected time range")

        return CollectOutput(events=events, warnings=warnings)


def build_opencode_collector(
    source_name: str = "local", source_host_hash: str = ""
) -> OpenCodeCollector:
    return OpenCodeCollector(
        source_name=source_name,
        source_host_hash=source_host_hash,
    )
