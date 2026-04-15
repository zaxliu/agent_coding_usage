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
    _extract_model_from_message_data,
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

    def test_extract_model_from_message_data_modelid(self) -> None:
        """Test extracting model name from message data via modelID."""
        data = json.dumps({
            "role": "assistant",
            "modelID": "glm-5.1",
            "providerID": "opencode",
        })
        result = _extract_model_from_message_data(data)
        assert result == "glm-5.1"

    def test_extract_model_from_message_data_fallback_model(self) -> None:
        """Test fallback to 'model' key when modelID is absent."""
        data = json.dumps({
            "role": "assistant",
            "model": "claude-3-5-sonnet",
        })
        result = _extract_model_from_message_data(data)
        assert result == "claude-3-5-sonnet"

    def test_extract_model_returns_unknown(self) -> None:
        """Test that unknown model returns 'unknown'."""
        data = json.dumps({"role": "user"})
        result = _extract_model_from_message_data(data)
        assert result == "unknown"

    def test_extract_model_invalid_json(self) -> None:
        """Test that invalid JSON returns 'unknown'."""
        result = _extract_model_from_message_data("not json")
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
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
            )
        """)

        # Insert test data
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cursor.execute(
            "INSERT INTO session (id, project_id, directory, title) VALUES (?, ?, ?, ?)",
            ("sess-1", "proj-1", "/tmp/test", "Test Session"),
        )
        cursor.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (
                "msg-1",
                "sess-1",
                now_ms,
                now_ms,
                json.dumps({
                    "role": "assistant",
                    "modelID": "glm-5.1",
                    "providerID": "opencode",
                }),
            ),
        )
        cursor.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "part-1",
                "msg-1",
                "sess-1",
                now_ms,
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
        assert event.model == "glm-5.1"
        assert event.input_tokens == 100
        assert event.output_tokens == 50
        assert event.cache_tokens == 210
