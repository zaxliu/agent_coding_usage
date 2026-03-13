from datetime import datetime, timezone

from llm_usage.collectors.parsing import extract_usage_events_from_node


def test_extract_usage_from_nested_node():
    payload = {
        "timestamp": "2026-03-08T01:02:03Z",
        "response": {
            "model": "gpt-5",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 2,
            },
        },
    }
    events = extract_usage_events_from_node(
        payload,
        tool="codex",
        fallback_time=datetime.now(timezone.utc),
        source_ref="x",
    )
    assert len(events) == 1
    assert events[0].input_tokens == 100
    assert events[0].output_tokens == 25
    assert events[0].cache_tokens == 7
