import json
from datetime import datetime, timezone
from pathlib import Path

from llm_usage.collectors.remote_file import RemoteFileCollector, SshTarget


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_remote_file_collector_supports_python_fallback(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        if cmd[:1] == ["scp"]:
            return _Completed()
        if cmd[:1] == ["ssh"] and cmd[-1].startswith("command -v python3"):
            return _Completed(stdout="python")
        if cmd[:1] == ["ssh"] and cmd[-2:] == ["sh", "-lc"]:
            return _Completed()
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    local_output = tmp_path / "probe_output.json"
    local_output.write_text(json.dumps({"matches": 1}), encoding="utf-8")
    collector._temp_path = lambda suffix: local_output  # type: ignore[method-assign]
    ok, msg = collector.probe()
    assert ok
    assert "remote files detected" in msg
    assert calls[0][-1].startswith("command -v python3")
    assert calls[1][0] == "scp"
    assert calls[2][0] == "scp"
    assert calls[3][0] == "ssh"
    assert "BatchMode=yes" in calls[1]


def test_remote_file_collector_collects_events_with_source_hash(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["scp"]:
            return _Completed()
        if cmd[:1] == ["ssh"] and cmd[-1].startswith("command -v python3"):
            return _Completed(stdout="python3")
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    local_output = tmp_path / "collect_output.json"
    local_output.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "tool": "codex",
                        "model": "unknown",
                        "event_time": "2026-03-08T01:02:03+00:00",
                        "input_tokens": 80,
                        "cache_tokens": 20,
                        "output_tokens": 5,
                        "session_fingerprint": "codex:019ceb08-9d8d-7dc3-a63f-123587dd33fe",
                        "source_ref": "/tmp/rollout-019ceb08-9d8d-7dc3-a63f-123587dd33fe.jsonl:1",
                    }
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    collector._temp_path = lambda suffix: local_output  # type: ignore[method-assign]
    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert len(out.events) == 1
    assert out.events[0].source_host_hash == "hash"
    assert out.events[0].input_tokens == 80


def test_remote_file_collector_writes_collect_payload_with_limits(tmp_path):
    uploads: dict[str, str] = {}

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["scp"] and ":" in cmd[-1]:
            uploads[Path(cmd[-2]).name] = Path(cmd[-2]).read_text(encoding="utf-8")
            return _Completed()
        if cmd[:1] == ["scp"]:
            return _Completed()
        if cmd[:1] == ["ssh"] and cmd[-1].startswith("command -v python3"):
            return _Completed(stdout="python3")
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        max_files=12,
        max_total_bytes=3456,
        runner=_runner,
    )
    local_output = tmp_path / "probe_output.json"
    local_output.write_text(json.dumps({"matches": 1}), encoding="utf-8")
    collector._temp_path = lambda suffix: local_output  # type: ignore[method-assign]

    ok, _msg = collector.probe()

    assert ok
    pattern_payload = next(value for key, value in uploads.items() if key.endswith(".json"))
    payload = json.loads(pattern_payload)
    assert payload["patterns"] == ["~/.claude/**/*.jsonl"]
    assert payload["max_files"] == 12
    assert payload["max_total_bytes"] == 3456
    assert payload["log_path"].endswith(".log")


def test_remote_file_collector_logs_remote_stderr_progress(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["scp"]:
            return _Completed()
        if cmd[:1] == ["ssh"] and cmd[-1].startswith("command -v python3"):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"]:
            return _Completed(stderr="info: processing file 1 size=123 path=/tmp/a.jsonl")
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    local_output = tmp_path / "collect_output.json"
    local_output.write_text(json.dumps({"events": [], "warnings": []}), encoding="utf-8")
    collector._temp_path = lambda suffix: local_output  # type: ignore[method-assign]
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert any("remote stderr: info: processing file 1 size=123" in line for line in printed)
