from datetime import datetime, timezone

from llm_usage.aggregation import aggregate_events
from llm_usage.identity import build_row_key, hash_source_host, hash_user
from llm_usage.models import UsageEvent


def test_hash_user_stable():
    assert hash_user("san.zhang", "salt") == hash_user("san.zhang", "salt")


def test_hash_source_host_includes_user():
    assert hash_source_host("alice", "prod-a", "salt") != hash_source_host("bob", "prod-a", "salt")


def test_build_row_key_stable():
    v1 = build_row_key("u", "source", "2026-03-08", "codex", "gpt-5")
    v2 = build_row_key("u", "source", "2026-03-08", "codex", "gpt-5")
    assert v1 == v2


def test_aggregate_by_day_tool_model():
    events = [
        UsageEvent(
            tool="codex",
            model="gpt-5",
            event_time=datetime(2026, 3, 8, 0, 10, tzinfo=timezone.utc),
            input_tokens=10,
            cache_tokens=1,
            output_tokens=2,
            source_host_hash="source-a",
        ),
        UsageEvent(
            tool="codex",
            model="gpt-5",
            event_time=datetime(2026, 3, 8, 1, 10, tzinfo=timezone.utc),
            input_tokens=5,
            cache_tokens=2,
            output_tokens=3,
            source_host_hash="source-a",
        ),
    ]
    rows = aggregate_events(events, user_hash="u", timezone_name="UTC")
    assert len(rows) == 1
    assert rows[0].source_host_hash == "source-a"
    assert rows[0].input_tokens_sum == 15
    assert rows[0].cache_tokens_sum == 3
    assert rows[0].output_tokens_sum == 5


def test_aggregate_by_session_fingerprint_ignores_model_split():
    events = [
        UsageEvent(
            tool="codex",
            model="unknown",
            event_time=datetime(2026, 3, 8, 0, 10, tzinfo=timezone.utc),
            input_tokens=10,
            cache_tokens=1,
            output_tokens=2,
            session_fingerprint="codex:session-a",
            source_host_hash="source-a",
        ),
        UsageEvent(
            tool="codex",
            model="gpt-5.3-codex",
            event_time=datetime(2026, 3, 8, 1, 10, tzinfo=timezone.utc),
            input_tokens=5,
            cache_tokens=2,
            output_tokens=3,
            session_fingerprint="codex:session-a",
            source_host_hash="source-a",
        ),
    ]

    rows = aggregate_events(events, user_hash="u", timezone_name="UTC")

    assert len(rows) == 1
    assert rows[0].model == "gpt-5.3-codex"
    assert rows[0].input_tokens_sum == 15
    assert rows[0].cache_tokens_sum == 3
    assert rows[0].output_tokens_sum == 5

    expected = build_row_key(
        "u",
        "source-a",
        "2026-03-08",
        "codex",
        "gpt-5.3-codex",
        session_fingerprint="codex:session-a",
    )
    assert rows[0].row_key == expected


def test_aggregate_splits_by_source_host_hash():
    events = [
        UsageEvent(
            tool="codex",
            model="gpt-5",
            event_time=datetime(2026, 3, 8, 0, 10, tzinfo=timezone.utc),
            input_tokens=10,
            cache_tokens=1,
            output_tokens=2,
            source_host_hash="source-a",
        ),
        UsageEvent(
            tool="codex",
            model="gpt-5",
            event_time=datetime(2026, 3, 8, 1, 10, tzinfo=timezone.utc),
            input_tokens=10,
            cache_tokens=1,
            output_tokens=2,
            source_host_hash="source-b",
        ),
    ]
    rows = aggregate_events(events, user_hash="u", timezone_name="UTC")
    assert len(rows) == 2
    assert {row.source_host_hash for row in rows} == {"source-a", "source-b"}
