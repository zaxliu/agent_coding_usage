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
        tool="claude_code",
        fallback_time=datetime.now(timezone.utc),
        source_ref="x",
    )
    assert len(events) == 1
    assert events[0].input_tokens == 100
    assert events[0].output_tokens == 25
    assert events[0].cache_tokens == 7


def test_extract_usage_supports_cached_input_tokens():
    payload = {
        "timestamp": "2026-03-08T01:02:03Z",
        "response": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 25,
                "cached_input_tokens": 33,
            },
        },
    }
    events = extract_usage_events_from_node(
        payload,
        tool="claude_code",
        fallback_time=datetime.now(timezone.utc),
        source_ref="x",
    )
    assert len(events) == 1
    assert events[0].cache_tokens == 33


def test_extract_codex_uses_last_token_usage_only():
    payload = {
        "timestamp": "2026-03-08T01:02:03Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 9999,
                    "cached_input_tokens": 8888,
                    "output_tokens": 7777,
                },
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 5,
                },
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
    assert events[0].input_tokens == 80
    assert events[0].cache_tokens == 20
    assert events[0].output_tokens == 5


def test_extract_codex_input_does_not_go_negative():
    payload = {
        "timestamp": "2026-03-08T01:02:03Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 20,
                    "output_tokens": 1,
                },
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
    assert events[0].input_tokens == 0
    assert events[0].cache_tokens == 20
