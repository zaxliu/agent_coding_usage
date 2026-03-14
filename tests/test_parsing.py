import json
from datetime import datetime, timezone

from llm_usage.collectors.parsing import extract_usage_events_from_node, read_events_from_file


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


def test_read_codex_jsonl_uses_turn_context_model(tmp_path):
    path = tmp_path / "rollout-2026-03-08T01-00-00-019ceb08-9d8d-7dc3-a63f-123587dd33fe.jsonl"
    lines = [
        {
            "timestamp": "2026-03-08T01:00:00Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.3-codex"},
        },
        {
            "timestamp": "2026-03-08T01:02:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 5,
                    }
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(v) for v in lines) + "\n", encoding="utf-8")

    events, warning = read_events_from_file(path, tool="codex")

    assert warning is None
    assert len(events) == 1
    assert events[0].model == "gpt-5.3-codex"
    assert events[0].input_tokens == 80
    assert events[0].cache_tokens == 20
    assert events[0].output_tokens == 5
    assert events[0].session_fingerprint == "codex:019ceb08-9d8d-7dc3-a63f-123587dd33fe"


def test_read_codex_jsonl_tracks_model_changes_by_turn(tmp_path):
    path = tmp_path / "rollout-2026-03-08T01-00-00-019ceb08-9d8d-7dc3-a63f-123587dd33fe.jsonl"
    lines = [
        {
            "timestamp": "2026-03-08T01:00:00Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.3-codex"},
        },
        {
            "timestamp": "2026-03-08T01:02:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 5,
                    }
                },
            },
        },
        {
            "timestamp": "2026-03-08T01:03:00Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.4"},
        },
        {
            "timestamp": "2026-03-08T01:05:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 10,
                        "output_tokens": 4,
                    }
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(v) for v in lines) + "\n", encoding="utf-8")

    events, warning = read_events_from_file(path, tool="codex")

    assert warning is None
    assert len(events) == 2
    assert [event.model for event in events] == ["gpt-5.3-codex", "gpt-5.4"]
    assert [event.input_tokens for event in events] == [80, 40]
    assert [event.cache_tokens for event in events] == [20, 10]
    assert [event.output_tokens for event in events] == [5, 4]
    assert {event.session_fingerprint for event in events} == {
        "codex:019ceb08-9d8d-7dc3-a63f-123587dd33fe"
    }
