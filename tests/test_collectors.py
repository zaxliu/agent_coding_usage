import json

from llm_usage.collectors import (
    build_claude_collector,
    build_copilot_cli_collector,
    build_copilot_vscode_collector,
    build_opencode_collector,
)


def test_claude_tool_name_is_claude_code():
    collector = build_claude_collector()
    assert collector.name == "claude_code"


def test_opencode_tool_name():
    collector = build_opencode_collector()
    assert collector.name == "opencode"


def test_copilot_cli_tool_name():
    collector = build_copilot_cli_collector()
    assert collector.name == "copilot_cli"


def test_copilot_vscode_tool_name():
    collector = build_copilot_vscode_collector()
    assert collector.name == "copilot_vscode"


def test_copilot_vscode_probe_reports_parsable_events(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "kind": 0,
                "v": {
                    "sessionId": "session-1",
                    "requests": [
                        {
                            "requestId": "request-1",
                            "timestamp": 1774411393113,
                            "result": {
                                "details": "Raptor mini (Preview) • 1x",
                                "metadata": {"promptTokens": 42, "outputTokens": 9},
                            },
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    collector = build_copilot_vscode_collector(patterns=[str(path)])
    ok, msg = collector.probe()

    assert ok is True
    assert msg == "1 files detected, 1 parsable events"


def test_copilot_vscode_probe_warns_when_files_have_no_parsable_events(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"kind": 0, "v": {"sessionId": "session-1", "requests": []}}) + "\n", encoding="utf-8")

    collector = build_copilot_vscode_collector(patterns=[str(path)])
    ok, msg = collector.probe()

    assert ok is False
    assert msg == "1 files detected, 0 parsable events"
