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


def test_read_copilot_cli_jsonl_extracts_model_metrics(tmp_path):
    session_dir = tmp_path / "session-state" / "sess-123"
    session_dir.mkdir(parents=True)
    path = session_dir / "events.jsonl"
    lines = [
        {
            "type": "session.start",
            "timestamp": "2026-03-25T03:45:56Z",
        },
        {
            "type": "session.shutdown",
            "timestamp": "2026-03-25T03:49:04Z",
            "data": {
                "modelMetrics": {
                    "gpt-5-mini": {
                        "usage": {
                            "inputTokens": 12002,
                            "outputTokens": 319,
                            "cacheReadTokens": 2,
                            "cacheWriteTokens": 3,
                        }
                    }
                }
            },
        },
    ]
    path.write_text("\n".join(json.dumps(v) for v in lines) + "\n", encoding="utf-8")

    events, warning = read_events_from_file(path, tool="copilot_cli")

    assert warning is None
    assert len(events) == 1
    assert events[0].tool == "copilot_cli"
    assert events[0].model == "gpt-5-mini"
    assert events[0].input_tokens == 12002
    assert events[0].cache_tokens == 5
    assert events[0].output_tokens == 319
    assert events[0].session_fingerprint == "copilot_cli:sess-123:gpt-5-mini"


def test_read_copilot_vscode_session_jsonl_extracts_requests(tmp_path):
    path = tmp_path / "session.jsonl"
    line = {
        "kind": 0,
        "v": {
            "sessionId": "session-abc",
            "requests": [
                {
                    "requestId": "request-1",
                    "timestamp": 1774411393113,
                    "agent": {"modelId": "copilot/auto"},
                    "result": {
                        "details": "Raptor mini (Preview) • 1x",
                        "metadata": {"promptTokens": 123, "outputTokens": 45},
                    },
                }
            ],
            "inputState": {
                "selectedModel": {
                    "metadata": {
                        "version": "raptor-mini",
                        "name": "Auto",
                    }
                }
            },
        },
    }
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")

    events, warning = read_events_from_file(path, tool="copilot_vscode")

    assert warning is None
    assert len(events) == 1
    assert events[0].tool == "copilot_vscode"
    assert events[0].model == "Raptor mini (Preview)"
    assert events[0].input_tokens == 123
    assert events[0].output_tokens == 45
    assert events[0].session_fingerprint == "copilot_vscode:session-abc:request-1"


def test_read_copilot_vscode_session_json_extracts_usage_variants(tmp_path):
    path = tmp_path / "session.json"
    payload = {
        "sessionId": "session-json",
        "inputState": {
            "selectedModel": {
                "metadata": {"version": "gpt-4.1"}
            }
        },
        "requests": [
            {
                "requestId": "request-old",
                "timestamp": "2026-03-25T03:45:56Z",
                "result": {"usage": {"promptTokens": 10, "completionTokens": 4}},
            },
            {
                "requestId": "request-new",
                "timestamp": "2026-03-25T03:49:04Z",
                "modelId": "copilot/gpt-4.1",
                "result": {"promptTokens": 20, "outputTokens": 8},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    events, warning = read_events_from_file(path, tool="copilot_vscode")

    assert warning is None
    assert len(events) == 2
    assert [event.model for event in events] == ["gpt-4.1", "gpt-4.1"]
    assert [event.input_tokens for event in events] == [10, 20]
    assert [event.output_tokens for event in events] == [4, 8]


def test_read_copilot_vscode_delta_jsonl_reconstructs_session(tmp_path):
    path = tmp_path / "delta-session.jsonl"
    lines = [
        {
            "kind": 0,
            "v": {
                "sessionId": "session-delta",
                "inputState": {"selectedModel": {"metadata": {"version": "gpt-4o"}}},
                "requests": [],
            },
        },
        {
            "kind": 2,
            "k": ["requests"],
            "v": [
                {
                    "requestId": "request-1",
                    "timestamp": 1774411393113,
                    "modelId": "copilot/gpt-4o-mini",
                    "result": {"metadata": {"promptTokens": 33, "outputTokens": 7}},
                }
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    events, warning = read_events_from_file(path, tool="copilot_vscode")

    assert warning is None
    assert len(events) == 1
    assert events[0].model == "gpt-4o-mini"
    assert events[0].input_tokens == 33
    assert events[0].output_tokens == 7
    assert events[0].session_fingerprint == "copilot_vscode:session-delta:request-1"


def test_read_copilot_vscode_estimates_tokens_from_text_when_usage_missing(tmp_path):
    path = tmp_path / "session.json"
    payload = {
        "sessionId": "session-estimate",
        "requests": [
            {
                "requestId": "request-1",
                "timestamp": "2026-03-25T03:49:04Z",
                "modelId": "copilot/gpt-4.1",
                "message": {"text": "hello world"},
                "response": [{"value": "this is a response"}],
                "result": {"details": "gpt-4.1 • 1x"},
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    events, warning = read_events_from_file(path, tool="copilot_vscode")

    assert warning is None
    assert len(events) == 1
    assert events[0].model == "gpt-4.1"
    assert events[0].input_tokens > 0
    assert events[0].output_tokens > 0


def test_read_cline_vscode_history_json_extracts_assistant_metrics(tmp_path):
    task_dir = tmp_path / "tasks" / "1777005150050"
    task_dir.mkdir(parents=True)
    path = task_dir / "api_conversation_history.json"
    payload = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "ts": 1777005160000,
        },
        {
            "role": "assistant",
            "ts": 1777005161617,
            "modelInfo": {"modelId": "kwaipilot/kat-coder-pro", "providerId": "cline"},
            "metrics": {
                "tokens": {
                    "prompt": 13408,
                    "completion": 42,
                    "cached": 336,
                },
                "cost": 0.00409296,
            },
        },
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    events, warning = read_events_from_file(path, tool="cline_vscode")

    assert warning is None
    assert len(events) == 1
    assert events[0].tool == "cline_vscode"
    assert events[0].model == "kwaipilot/kat-coder-pro"
    assert events[0].input_tokens == 13072
    assert events[0].cache_tokens == 336
    assert events[0].output_tokens == 42
    assert events[0].session_fingerprint == "cline_vscode:1777005150050:1:1777005161617"


def test_read_cline_vscode_history_json_skips_entries_without_metrics(tmp_path):
    task_dir = tmp_path / "tasks" / "1777005150050"
    task_dir.mkdir(parents=True)
    path = task_dir / "api_conversation_history.json"
    payload = [
        {"role": "assistant", "ts": 1777005161617, "modelInfo": {"modelId": "model-a"}},
        {
            "role": "assistant",
            "ts": 1777005162617,
            "modelInfo": {"modelId": "model-b"},
            "metrics": {"tokens": {"prompt": 200, "completion": 10, "cached": 20}},
        },
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    events, warning = read_events_from_file(path, tool="cline_vscode")

    assert warning is None
    assert len(events) == 1
    assert events[0].model == "model-b"
    assert events[0].input_tokens == 180
