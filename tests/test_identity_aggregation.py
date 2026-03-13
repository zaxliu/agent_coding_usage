from datetime import datetime, timezone

from llm_usage.aggregation import aggregate_events
from llm_usage.identity import build_row_key, hash_user
from llm_usage.models import UsageEvent


def test_hash_user_stable():
    assert hash_user("san.zhang", "salt") == hash_user("san.zhang", "salt")


def test_build_row_key_stable():
    v1 = build_row_key("u", "2026-03-08", "codex", "gpt-5")
    v2 = build_row_key("u", "2026-03-08", "codex", "gpt-5")
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
        ),
        UsageEvent(
            tool="codex",
            model="gpt-5",
            event_time=datetime(2026, 3, 8, 1, 10, tzinfo=timezone.utc),
            input_tokens=5,
            cache_tokens=2,
            output_tokens=3,
        ),
    ]
    rows = aggregate_events(events, user_hash="u", timezone_name="UTC")
    assert len(rows) == 1
    assert rows[0].input_tokens_sum == 15
    assert rows[0].cache_tokens_sum == 3
    assert rows[0].output_tokens_sum == 5
