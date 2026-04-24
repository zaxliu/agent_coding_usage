import json

from llm_usage.collectors import (
    build_claude_collector,
    build_cline_vscode_collector,
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


def test_cline_vscode_tool_name():
    collector = build_cline_vscode_collector()
    assert collector.name == "cline_vscode"


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


def test_cline_vscode_probe_reports_parsable_events_and_version(tmp_path):
    ext_dir = tmp_path / ".vscode" / "extensions" / "saoudrizwan.claude-dev-3.80.0"
    ext_dir.mkdir(parents=True)
    (ext_dir / "package.json").write_text(
        json.dumps({"name": "claude-dev", "publisher": "saoudrizwan", "version": "3.80.0"}),
        encoding="utf-8",
    )
    task_dir = tmp_path / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "tasks" / "1777005150050"
    task_dir.mkdir(parents=True)
    (task_dir / "api_conversation_history.json").write_text(
        json.dumps(
            [
                {
                    "role": "assistant",
                    "ts": 1777005161617,
                    "modelInfo": {"modelId": "kwaipilot/kat-coder-pro"},
                    "metrics": {"tokens": {"prompt": 100, "completion": 9, "cached": 4}},
                }
            ]
        ),
        encoding="utf-8",
    )

    collector = build_cline_vscode_collector(
        patterns=[str(task_dir / "api_conversation_history.json")],
        version_patterns=[str(ext_dir / "package.json")],
    )
    ok, msg = collector.probe()

    assert ok is True
    assert msg == "1 files detected, 1 parsable events, version 3.80.0"
