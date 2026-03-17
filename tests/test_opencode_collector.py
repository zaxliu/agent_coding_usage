"""Tests for OpenCode collector."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_usage.collectors import OpenCodeCollector, build_opencode_collector
from llm_usage.collectors.opencode import (
    _extract_tokens_from_part_data,
    _extract_model_from_part_data,
)


class TestOpenCodeCollectorHelpers:
    """Tests for helper functions."""

    def test_extract_tokens_from_step_finish(self) -> None:
        """Test extracting tokens from step-finish event."""
        data = json.dumps({
            "type": "step-finish",
            "reason": "tool-calls",
            "cost": 0.0054334,
            "tokens": {
                "total": 33852,
                "input": 5,
                "output": 81,
                "reasoning": 0,
                "cache": {"read": 32334, "write": 1432},
            },
        })
        result = _extract_tokens_from_part_data(data)
        assert result is not None
        input_tokens, cache_tokens, output_tokens = result
        assert input_tokens == 5
        assert cache_tokens == 33766  # read + write
        assert output_tokens == 81

    def test_extract_tokens_from_non_token_event(self) -> None:
        """Test that non-token events return None."""
        data = json.dumps({"type": "step-start"})
        result = _extract_tokens_from_part_data(data)
        assert result is None

    def test_extract_model_from_data(self) -> None:
        """Test extracting model name."""
        data = json.dumps({
            "type": "step-start",
            "model": "claude-3-5-sonnet",
        })
        result = _extract_model_from_part_data(data)
        assert result == "claude-3-5-sonnet"

    def test_extract_model_returns_unknown(self) -> None:
        """Test that unknown model returns 'unknown'."""
        data = json.dumps({"type": "other"})
        result = _extract_model_from_part_data(data)
        assert result == "unknown"


class TestOpenCodeCollector:
    """Tests for OpenCodeCollector class."""

    def test_build_collector(self) -> None:
        """Test building OpenCode collector."""
        collector = build_opencode_collector()
        assert collector.name == "opencode"

    def test_probe_missing_database(self, tmp_path: Path) -> None:
        """Test probe with missing database."""
        collector = OpenCodeCollector(db_path=tmp_path / "missing.db")
        ok, msg = collector.probe()
        assert ok is False
        assert "not found" in msg

    def test_collect_from_database(self, tmp_path: Path) -> None:
        """Test collecting from a test database."""
        # Create a test database
        db_path = tmp_path / "opencode.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create tables
        cursor.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                directory TEXT NOT NULL,
                title TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)

        # Insert test data
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cursor.execute(
            "INSERT INTO session (id, project_id, directory, title) VALUES (?, ?, ?, ?)",
            ("sess-1", "proj-1", "/tmp/test", "Test Session"),
        )
        cursor.execute(
            "INSERT INTO message (id, session_id, time_created) VALUES (?, ?, ?)",
            ("msg-1", "sess-1", now_ms),
        )
        cursor.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, data) VALUES (?, ?, ?, ?, ?)",
            (
                "part-1",
                "msg-1",
                "sess-1",
                now_ms,
                json.dumps({
                    "type": "step-finish",
                    "tokens": {
                        "input": 100,
                        "output": 50,
                        "cache": {"read": 200, "write": 10},
                    },
                }),
            ),
        )
        conn.commit()
        conn.close()

        # Collect
        collector = OpenCodeCollector(db_path=db_path)
        start = datetime.fromtimestamp(now_ms / 1000 - 3600, tz=timezone.utc)
        end = datetime.fromtimestamp(now_ms / 1000 + 3600, tz=timezone.utc)
        output = collector.collect(start, end)

        assert len(output.events) == 1
        event = output.events[0]
        assert event.tool == "opencode"
        assert event.input_tokens == 100
        assert event.output_tokens == 50
        assert event.cache_tokens == 210
