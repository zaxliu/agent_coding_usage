import ast
import base64
import io
import json
import subprocess
from datetime import datetime, timezone
from contextlib import redirect_stdout

from llm_usage.collectors import remote_file
from llm_usage.collectors.remote_file import RemoteCollectJob, RemoteFileCollector, SshAuthenticationError, SshTarget


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _extract_stdin_payload(input_text: str) -> dict:
    payload_line, _script_text = input_text.split("\n", 1)
    return json.loads(__import__("base64").b64decode(payload_line).decode("utf-8"))


def _is_ssh_command(cmd: list[str]) -> bool:
    return cmd[:1] == ["ssh"] or cmd[:3] == ["sshpass", "-e", "ssh"]


def _remote_command(cmd: list[str]) -> str:
    return cmd[-1] if _is_ssh_command(cmd) else ""


def _paramiko_python_discovery_result(cmd: list[str]) -> _Completed:
    remote_command = " ".join(cmd)
    if "command -v python3" in remote_command:
        return _Completed(returncode=0, stdout="python3")
    if "sys.version_info" in remote_command:
        return _Completed(returncode=0, stdout="3.9.0\n")
    return _Completed(returncode=0, stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))


def test_remote_file_collector_supports_python_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_file, "_is_windows_platform", lambda: False)
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python")
        if cmd[:1] == ["ssh"] and "sys.version_info" in _remote_command(cmd):
            return _Completed(stdout="3.9.0\n")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    ok, msg = collector.probe()
    assert ok
    assert "remote files detected" in msg
    assert "command -v python3" in _remote_command(calls[0])
    assert calls[1][0] == "ssh"
    assert "ControlMaster=auto" in calls[1]
    assert "ControlPersist=5m" in calls[1]
    assert "ControlPath=/tmp/llm-usage-ssh-%C" in calls[1]
    assert len(calls) == 3
    assert "BatchMode=yes" in calls[1]


def test_remote_file_collector_uses_paramiko_for_collect(tmp_path, monkeypatch):
    captured = []
    call_count = {"n": 0}

    def _fake_paramiko(**kwargs):  # noqa: ANN001, ANN201
        call_count["n"] += 1
        captured.append(kwargs["remote_args"])
        if call_count["n"] == 1:
            return _Completed(returncode=0, stdout="python3")
        if call_count["n"] == 2:
            return _Completed(returncode=0, stdout="3.9.0\n")
        return _Completed(returncode=0, stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))

    monkeypatch.setattr(remote_file, "_run_remote_command_with_paramiko", _fake_paramiko)

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        ssh_password="  secret  ",
    )

    collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert any("command -v python3" in " ".join(cmd) for cmd in captured)
    assert any("sys.version_info" in " ".join(cmd) for cmd in captured)


def test_remote_file_collector_without_password_uses_system_ssh(tmp_path, monkeypatch):
    captured = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None, env=None):  # noqa: ANN001, ANN201
        captured.append((cmd, env))
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed(stdout="3.9.0\n")

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert captured[0][0][0] == "ssh"
    assert "BatchMode=yes" in captured[0][0]


def test_remote_file_collector_reports_paramiko_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        remote_file,
        "_run_remote_command_with_paramiko",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("paramiko failed")),
    )

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        ssh_password="secret",
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert out.warnings == ["server_a/codex: paramiko failed"]


def test_remote_file_collector_reports_missing_ssh_binary_in_key_mode(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None, env=None):  # noqa: ANN001, ANN201
        raise FileNotFoundError("ssh")

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert out.warnings == ["server_a/codex: SSH 命令未找到"]


def test_remote_file_collector_filters_bastion_noise_from_python_probe(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="match asset failed: 未发现匹配的资产 %s\npython3\n")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    ok, msg = collector.probe()

    assert ok
    assert "remote files detected" in msg


def test_remote_file_collector_falls_back_to_login_shell_python_discovery(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        if cmd[:1] == ["ssh"] and _remote_command(cmd) == "'sh' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(stdout="")
        if cmd[:1] == ["ssh"] and _remote_command(cmd) == "'bash' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(stdout="/opt/homebrew/bin/python3\n")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    ok, msg = collector.probe()

    assert ok
    assert "remote files detected" in msg
    assert calls[0][-1].startswith("'sh' '-lc'")
    assert calls[1][-1].startswith("'bash' '-lc'")


def test_remote_file_collector_falls_back_to_common_python_paths(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        if cmd[:1] == ["ssh"] and _remote_command(cmd) == "'sh' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(stdout="")
        if cmd[:1] == ["ssh"] and _remote_command(cmd) == "'bash' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(returncode=127, stderr="bash: not found")
        if cmd[:1] == ["ssh"] and _remote_command(cmd) == "'zsh' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(returncode=127, stderr="zsh: not found")
        if cmd[:1] == ["ssh"] and "for candidate in /usr/bin/python3" in _remote_command(cmd):
            return _Completed(stdout="/usr/bin/python3\n")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    ok, msg = collector.probe()

    assert ok
    assert "remote files detected" in msg
    assert any("for candidate in /usr/bin/python3" in _remote_command(cmd) for cmd in calls)


def test_remote_file_collector_collect_aggregates_multiple_pages():
    """collect() loops until next_cursor is null; events and warnings aggregate across pages."""
    collect_payloads: list[dict] = []
    cursor_resume = {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 3}

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            collect_payloads.append(_extract_stdin_payload(input))
            if len(collect_payloads) == 1:
                assert "cursor" not in collect_payloads[0]
                return _Completed(
                    stdout=json.dumps(
                        {
                            "events": [
                                {
                                    "tool": "codex",
                                    "model": "alpha",
                                    "event_time": "2026-03-08T01:02:03+00:00",
                                    "input_tokens": 1,
                                    "cache_tokens": 0,
                                    "output_tokens": 0,
                                }
                            ],
                            "warnings": ["page1_warn"],
                            "next_cursor": cursor_resume,
                        }
                    )
                )
            assert collect_payloads[1]["cursor"] == cursor_resume
            return _Completed(
                stdout=json.dumps(
                    {
                        "events": [
                            {
                                "tool": "codex",
                                "model": "beta",
                                "event_time": "2026-03-08T02:02:03+00:00",
                                "input_tokens": 2,
                                "cache_tokens": 0,
                                "output_tokens": 0,
                            }
                        ],
                        "warnings": ["page2_warn"],
                        "next_cursor": None,
                    }
                )
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert len(collect_payloads) == 2
    assert [e.model for e in out.events] == ["alpha", "beta"]
    assert any("page1_warn" in w for w in out.warnings)
    assert any("page2_warn" in w for w in out.warnings)


def test_remote_file_collector_collect_rejects_non_advancing_cursor():
    stuck = {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 7}

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            payload = _extract_stdin_payload(input)
            if "cursor" not in payload:
                return _Completed(
                    stdout=json.dumps({"events": [], "warnings": [], "next_cursor": stuck})
                )
            assert payload["cursor"] == stuck
            return _Completed(
                stdout=json.dumps({"events": [], "warnings": [], "next_cursor": stuck})
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert out.events == []
    assert out.warnings == ["server_a/codex: remote pagination cursor did not advance"]


def test_remote_file_collector_collect_keeps_prior_events_when_cursor_stalls():
    stuck = {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 1}

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            payload = _extract_stdin_payload(input)
            if "cursor" not in payload:
                return _Completed(
                    stdout=json.dumps(
                        {
                            "events": [
                                {
                                    "tool": "codex",
                                    "model": "first-page",
                                    "event_time": "2026-03-08T01:02:03+00:00",
                                    "input_tokens": 80,
                                    "cache_tokens": 0,
                                    "output_tokens": 10,
                                }
                            ],
                            "warnings": [],
                            "next_cursor": stuck,
                        }
                    )
                )
            assert payload["cursor"] == stuck
            return _Completed(
                stdout=json.dumps({"events": [], "warnings": [], "next_cursor": stuck})
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert [event.model for event in out.events] == ["first-page"]
    assert out.warnings == ["server_a/codex: remote pagination cursor did not advance"]


def test_remote_file_collector_collects_events_with_source_hash(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(
                stdout=json.dumps(
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
                        "next_cursor": None,
                    }
                )
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert len(out.events) == 1
    assert out.events[0].source_host_hash == "hash"
    assert out.events[0].input_tokens == 80


def test_remote_collect_script_emits_chunked_stdout_protocol(tmp_path):
    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
    )
    _cmd, script_input = collector._python_stdin_command("python3", remote_file._COLLECT_SCRIPT)
    _payload_line, script_text = script_input.split("\n", 1)
    first_line = next(line for line in script_text.splitlines() if line.strip())
    assert first_line == "import base64, glob, hashlib, json, os, re, sys"
    assert "_emit_chunked_payload" in script_text
    assert repr(remote_file._CHUNKED_STDOUT_PREFIX) in script_text
    assert f"chunk_size = {remote_file._DEFAULT_STDOUT_CHUNK_SIZE}" in script_text
    assert 'print(json.dumps({"events": events, "warnings": warnings}))' not in script_text
    assert '_emit_chunked_payload({"events": events, "warnings": warnings, "next_cursor":' in script_text


def test_remote_collect_script_does_not_duplicate_nested_usage_as_unknown(tmp_path):
    fake_file = tmp_path / "fake_usage.jsonl"
    file_mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    fake_file.write_text(
        json.dumps(
            {
                "created_at": "2026-03-08T01:02:03Z",
                "model": "fake_model",
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fake_file.touch()
    __import__("os").utime(fake_file, (file_mtime, file_mtime))
    payload = base64.b64encode(
        json.dumps(
            {
                "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
                "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                "max_files": 100,
                "max_total_bytes": 1024 * 1024,
            }
        ).encode("utf-8")
    ).decode("ascii")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
    assert error is None
    result = parsed
    assert result["warnings"] == []
    assert [(event["model"], event["input_tokens"], event["cache_tokens"], event["output_tokens"]) for event in result["events"]] == [
        ("fake_model", 80, 20, 5)
    ]


def test_remote_file_collector_parses_z_suffix_event_time_without_fromisoformat(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(
                stdout=json.dumps(
                    {
                        "events": [
                            {
                                "tool": "claude_code",
                                "model": "claude-3-7-sonnet-20250219",
                                "event_time": "2026-03-08T01:02:03Z",
                                "input_tokens": 80,
                                "cache_tokens": 20,
                                "output_tokens": 5,
                            }
                        ],
                        "warnings": [],
                        "next_cursor": None,
                    }
                )
            )
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert len(out.events) == 1
    assert out.events[0].event_time == datetime(2026, 3, 8, 1, 2, 3, tzinfo=timezone.utc)


def test_remote_collect_script_avoids_fromisoformat_for_python36_compatibility():
    assert "fromisoformat" not in remote_file._COLLECT_SCRIPT


def test_remote_file_collector_parses_minute_precision_event_times():
    assert remote_file._parse_datetime_value("2026-03-08T01:02Z") == datetime(
        2026, 3, 8, 1, 2, tzinfo=timezone.utc
    )
    assert remote_file._parse_datetime_value("2026-03-08 01:02+00:00") == datetime(
        2026, 3, 8, 1, 2, tzinfo=timezone.utc
    )


def test_remote_file_collector_preserves_broader_iso8601_variants():
    assert remote_file._parse_datetime_value("2026-03-08T01:02:03+00") == datetime(
        2026, 3, 8, 1, 2, 3, tzinfo=timezone.utc
    )
    assert remote_file._parse_datetime_value("2026-03-08T01:02:03,5+00:00") == datetime(
        2026, 3, 8, 1, 2, 3, 500000, tzinfo=timezone.utc
    )


def test_remote_file_collector_tolerates_stdout_noise_around_json(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(
                stdout=(
                    "Last login: today from bastion\n"
                    + json.dumps({"events": [], "warnings": [], "next_cursor": None})
                    + "\nwelcome banner"
                )
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("remote stdout noise: Last login: today from bastion" in line for line in printed)
    assert any("remote stdout noise: welcome banner" in line for line in printed)


def test_remote_file_collector_tolerates_inline_stdout_noise_around_json(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(
                stdout="audit prefix >>> " + json.dumps({"events": [], "warnings": [], "next_cursor": None}) + " <<< audit suffix"
            )
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("remote stdout noise: audit prefix >>>" in line for line in printed)
    assert any("<<< audit suffix" in line for line in printed)


def test_remote_file_collector_logs_debug_preview_for_non_json_output(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            return _Completed(stdout="bad output", stderr="stderr note")
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == [
        "server_a/codex: remote pagination payload: could not extract JSON from remote stdout",
    ]
    assert any("remote stdout debug:" in line for line in printed)
    assert any("remote stdout preview: bad output" in line for line in printed)
    assert any("remote stderr preview: stderr note" in line for line in printed)


def test_remote_file_collector_writes_collect_payload_with_limits(tmp_path):
    commands = []
    inputs = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        commands.append(cmd)
        inputs.append(input)
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            assert input is not None
            assert "import base64" in input
            return _Completed(stdout=json.dumps({"matches": 1}))
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
    ok, _msg = collector.probe()

    assert ok
    payload = _extract_stdin_payload(next(item for item in inputs if item is not None))
    assert payload["jobs"] == [{"tool": "claude_code", "patterns": ["~/.claude/**/*.jsonl"]}]
    assert payload["max_files"] == 12
    assert payload["max_total_bytes"] == 3456


def test_remote_file_collector_collect_writes_requested_time_window(tmp_path):
    commands = []
    inputs = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        commands.append(cmd)
        inputs.append(input)
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    start = datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)

    collector.collect(start=start, end=end)

    payload = _extract_stdin_payload(next(item for item in inputs if item is not None))
    assert payload["start_ts"] == start.timestamp()
    assert payload["end_ts"] == end.timestamp()


def test_remote_file_collector_logs_remote_stderr_progress(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(
                stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}),
                stderr="info: processing file 1 size=123 path=/tmp/a.jsonl",
            )
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert any("remote stderr: info: processing file 1 size=123" in line for line in printed)


def test_remote_file_collector_retries_without_connection_sharing_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_file, "_is_windows_platform", lambda: False)
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        has_sharing = "ControlMaster=auto" in cmd
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None and has_sharing:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    execution_calls = [cmd for cmd in calls if cmd[:1] == ["ssh"] and " -c " in _remote_command(cmd)]
    assert len(execution_calls) >= 2
    assert "ControlMaster=auto" in execution_calls[0]
    assert any("ControlMaster=auto" not in cmd for cmd in execution_calls[1:])


def test_remote_file_collector_combines_multiple_jobs_into_single_remote_call(tmp_path):
    commands = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        commands.append(cmd)
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(
                stdout=json.dumps(
                    {
                        "events": [
                            {
                                "tool": "codex",
                                "model": "gpt-5",
                                "event_time": "2026-03-08T01:02:03+00:00",
                                "input_tokens": 10,
                                "cache_tokens": 0,
                                "output_tokens": 2,
                            },
                            {
                                "tool": "claude_code",
                                "model": "sonnet",
                                "event_time": "2026-03-08T01:04:03+00:00",
                                "input_tokens": 3,
                                "cache_tokens": 1,
                                "output_tokens": 4,
                            },
                        ],
                        "warnings": [],
                        "next_cursor": None,
                    }
                )
            )
        return _Completed()

    collector = RemoteFileCollector(
        "remote",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        jobs=[
            RemoteCollectJob(tool="codex", patterns=["~/.codex/**/*.jsonl"]),
            RemoteCollectJob(tool="claude_code", patterns=["~/.claude/**/*.jsonl"]),
        ],
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert [event.tool for event in out.events] == ["codex", "claude_code"]
    execution_calls = [
        cmd
        for cmd in commands
        if cmd[:1] == ["ssh"]
        and "command -v python3" not in _remote_command(cmd)
        and "sys.version_info" not in _remote_command(cmd)
    ]
    assert len(execution_calls) == 1


def test_remote_file_collector_falls_back_to_uploaded_script_when_stdin_is_consumed(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append((cmd, input))
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and "cat > " in _remote_command(cmd):
            assert input is not None and input.startswith("PAYLOAD_B64 = ")
            return _Completed()
        if cmd[:1] == ["ssh"] and "rm -f " in _remote_command(cmd):
            return _Completed()
        if cmd[:1] == ["ssh"]:
            if input:
                return _Completed(
                    stdout=(
                        "Traceback (most recent call last):\n"
                        '  File "<stdin>", line 1, in <module>\n'
                        "NameError: name 'eyJ...' is not defined\n"
                    )
                )
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("cat > " in _remote_command(cmd) for cmd, _input in calls if cmd[:1] == ["ssh"])


def test_remote_file_collector_falls_back_to_uploaded_script_when_stdin_traceback_is_on_stderr(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append((cmd, input))
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and "cat > " in _remote_command(cmd):
            assert input is not None and input.startswith("PAYLOAD_B64 = ")
            return _Completed()
        if cmd[:1] == ["ssh"] and "rm -f " in _remote_command(cmd):
            return _Completed()
        if cmd[:1] == ["ssh"]:
            if input:
                return _Completed(
                    stderr=(
                        "Traceback (most recent call last):\n"
                        '  File "<stdin>", line 1, in <module>\n'
                        "NameError: name 'eyJ...' is not defined\n"
                    )
                )
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("cat > " in _remote_command(cmd) for cmd, _input in calls if cmd[:1] == ["ssh"])


def test_remote_file_collector_falls_back_to_uploaded_script_when_stdin_traceback_returns_nonzero(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append((cmd, input))
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and "cat > " in _remote_command(cmd):
            assert input is not None and input.startswith("PAYLOAD_B64 = ")
            return _Completed()
        if cmd[:1] == ["ssh"] and "rm -f " in _remote_command(cmd):
            return _Completed()
        if cmd[:1] == ["ssh"]:
            if input:
                return _Completed(
                    returncode=1,
                    stderr=(
                        "Traceback (most recent call last):\n"
                        '  File "<stdin>", line 1, in <module>\n'
                        "NameError: name 'eyJ...' is not defined\n"
                    ),
                )
            return _Completed(stdout=json.dumps({"events": [], "warnings": [], "next_cursor": None}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("cat > " in _remote_command(cmd) for cmd, _input in calls if cmd[:1] == ["ssh"])


def test_remote_file_collector_logs_stdout_receive_progress_with_popen(monkeypatch):
    printed: list[str] = []
    collector = RemoteFileCollector(
        "remote",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        jobs=[RemoteCollectJob(tool="codex", patterns=["~/.codex/**/*.jsonl"])],
        runner=lambda *args, **kwargs: _Completed(stdout="python3"),
        popen_factory=subprocess.Popen,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))
    collector._ssh_command_and_env = lambda _args: (  # type: ignore[method-assign]
        [
            "python3",
            "-c",
            (
                "import sys;"
                "sys.stderr.write('info: starting\\n');"
                "sys.stderr.flush();"
                "sys.stdout.write('{\"events\":[],\"warnings\":[],\"padding\":\"');"
                "sys.stdout.write('x'*300000);"
                "sys.stdout.write('\"}');"
                "sys.stdout.flush()"
            ),
        ],
        None,
    )

    completed, error = collector._ssh_run_python_command(["ignored"], input_text="print('x')\n")

    assert error is None
    assert completed is not None
    assert any("remote stderr: info: starting" in line for line in printed)
    assert any("remote stdout received" in line for line in printed)
    assert any("remote stdout complete" in line for line in printed)


def test_remote_file_collector_falls_back_to_uploaded_script_for_stdin_traceback_with_popen(monkeypatch):
    collector = RemoteFileCollector(
        "remote",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        jobs=[RemoteCollectJob(tool="codex", patterns=["~/.codex/**/*.jsonl"])],
        runner=lambda *args, **kwargs: _Completed(stdout="python3"),
        popen_factory=subprocess.Popen,
    )
    collector._ssh_command_and_env = lambda _args: (  # type: ignore[method-assign]
        [
            "python3",
            "-c",
            (
                "import sys;"
                "sys.stderr.write('Traceback (most recent call last):\\n');"
                "sys.stderr.write('  File \"<stdin>\", line 1, in <module>\\n');"
                "sys.stderr.write(\"NameError: name 'eyJ...' is not defined\\n\");"
                "sys.stderr.flush();"
                "raise SystemExit(1)"
            ),
        ],
        None,
    )

    called = {"fallback": False}

    def _fallback(python_cmd, script, cursor=None, **kwargs):  # noqa: ANN001, ANN201
        called["fallback"] = True
        return {"events": [], "warnings": [], "next_cursor": None}, None

    collector._run_python_script_via_uploaded_file = _fallback  # type: ignore[method-assign]

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert called["fallback"] is True
    assert out.warnings == ["server_a/remote: no usage events in selected time range"]


def test_remote_file_collector_collect_accepts_chunked_stdout(monkeypatch):
    printed: list[str] = []
    payload = {"events": [], "warnings": [], "next_cursor": None}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=24)

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=chunked)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert not any("remote stdout noise:" in line for line in printed)


def test_decode_chunked_stdout_payload_returns_discarded_noise_around_frame():
    payload = {"events": [], "warnings": []}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=80)
    stdout = "Last login: bastion\n" + chunked + "\ntrailer line\n"
    parsed, discarded, error = remote_file._decode_chunked_stdout_payload(stdout)
    assert error is None
    assert parsed == payload
    assert "Last login: bastion" in discarded
    assert "trailer line" in discarded
    assert remote_file._CHUNKED_STDOUT_PREFIX not in discarded


def test_remote_file_collector_collect_accepts_chunked_stdout_with_surrounding_noise(monkeypatch):
    """Chunked frame may be embedded in SSH/bastion lines; non-protocol lines are returned as discarded noise."""
    printed: list[str] = []
    payload = {"events": [], "warnings": [], "next_cursor": None}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=24)
    stdout_mixed = "Last login: bastion\n" + chunked + "\ntrailer line\n"

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=stdout_mixed)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("remote stdout noise: Last login: bastion" in line for line in printed)
    assert any("remote stdout noise: trailer line" in line for line in printed)


def test_decode_chunked_stdout_payload_rejects_missing_chunk():
    payload = {"events": [{"tool": "claude_code"}], "warnings": []}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=12).splitlines()
    broken = "\n".join(line for line in chunked if " index=1 " not in line)

    parsed, discarded, error = remote_file._decode_chunked_stdout_payload(broken)

    assert parsed is None
    assert discarded == broken
    assert error == "remote chunked stdout missing chunks"


def test_decode_chunked_stdout_payload_rejects_hash_mismatch():
    payload = {"events": [{"tool": "claude_code"}], "warnings": []}
    lines = remote_file._encode_chunked_stdout_payload(payload, chunk_size=12).splitlines()
    # Tamper with the declared digest so decoded bytes still match total_bytes but SHA-256 check fails.
    begin = lines[0]
    head, sep, _rest = begin.partition(" sha256=")
    lines[0] = head + sep + ("0" * 64)

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload("\n".join(lines))

    assert parsed is None
    assert error == "remote chunked stdout hash mismatch"


def test_remote_file_collector_collect_prefers_chunked_protocol_before_legacy_noise(monkeypatch):
    """Legacy JSON in leading noise must not win when a valid chunked frame is also present."""
    printed: list[str] = []
    payload = {"events": [], "warnings": [], "next_cursor": None}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=24)
    misleading = json.dumps({"events": [{"tool": "from_noise"}], "warnings": []})
    stdout_mixed = "banner\n" + misleading + "\n" + chunked + "\n"

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=stdout_mixed)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    noise = [line for line in printed if "remote stdout noise:" in line]
    assert any("banner" in line for line in noise)
    assert len(noise) >= 2


def test_extract_remote_page_payload_accepts_null_cursor():
    payload = {"events": [], "warnings": [], "next_cursor": None}
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert err is None
    assert parsed == payload
    assert discarded == ""


def test_extract_remote_page_payload_rejects_invalid_cursor_shape():
    payload = {"events": [], "warnings": [], "next_cursor": "not-a-cursor"}
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert parsed is None
    assert err == "remote pagination returned invalid cursor"
    assert discarded == ""


def test_extract_remote_page_payload_accepts_non_null_cursor_dict():
    payload = {
        "events": [],
        "warnings": [],
        "next_cursor": {"job_index": 0, "pattern_index": 1, "file_index": 2, "line_index": 3},
    }
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert err is None
    assert parsed == payload
    assert discarded == ""


def test_extract_remote_page_payload_rejects_missing_next_cursor():
    payload = {"events": [], "warnings": []}
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert parsed is None
    assert err == "remote pagination payload missing next_cursor"
    assert discarded == ""


def test_extract_remote_page_payload_rejects_non_list_events():
    payload = {"events": {}, "warnings": [], "next_cursor": None}
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert parsed is None
    assert err == "remote pagination payload invalid: events must be a list"
    assert discarded == ""


def test_extract_remote_page_payload_rejects_non_list_warnings():
    payload = {"events": [], "warnings": "x", "next_cursor": None}
    stdout = json.dumps(payload)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert parsed is None
    assert err == "remote pagination payload invalid: warnings must be a list"
    assert discarded == ""


def test_extract_remote_page_payload_accepts_chunked_stdout():
    payload = {"events": [], "warnings": [], "next_cursor": None}
    stdout = remote_file._encode_chunked_stdout_payload(payload, chunk_size=48)
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert err is None
    assert parsed == payload
    assert discarded == ""


def test_extract_remote_page_payload_accepts_chunked_stdout_with_noise():
    """Paginated page parser must use the same chunked framing path as collect, including banner noise."""
    payload = {"events": [], "warnings": [], "next_cursor": None}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=40)
    stdout = "Last login: bastion\n" + chunked + "\ntrailer\n"
    parsed, discarded, err = remote_file._extract_remote_page_payload(stdout)
    assert err is None
    assert parsed == payload
    assert "Last login: bastion" in discarded
    assert "trailer" in discarded


def test_extract_remote_page_payload_rejects_non_json_stdout():
    parsed, discarded, err = remote_file._extract_remote_page_payload("not json at all")
    assert parsed is None
    assert err == "remote pagination payload: could not extract JSON from remote stdout"
    assert discarded == "not json at all"


def test_extract_remote_page_payload_rejects_non_object_json_root():
    parsed, discarded, err = remote_file._extract_remote_page_payload("[1,2,3]")
    assert parsed is None
    assert err == "remote pagination payload: JSON root must be an object"
    assert discarded == ""


def test_remote_file_collector_builds_collect_payload_with_page_budget_and_cursor():
    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
    )
    start = datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)
    collector._active_start_value = start
    collector._active_end_value = end

    payload = collector._build_remote_payload()
    assert payload["stdout_page_budget_bytes"] == remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES
    assert payload["start_ts"] == start.timestamp()
    assert payload["end_ts"] == end.timestamp()
    assert "cursor" not in payload

    cursor = {"job_index": 0, "pattern_index": 1, "file_index": 2, "line_index": 40}
    payload_with_cursor = collector._build_remote_payload(cursor)
    assert payload_with_cursor["stdout_page_budget_bytes"] == remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES
    assert payload_with_cursor["cursor"] == cursor
    assert payload_with_cursor["jobs"] == payload["jobs"]

    _cmd, script_input = collector._python_stdin_command("python3", remote_file._COLLECT_SCRIPT, cursor=cursor)
    decoded = _extract_stdin_payload(script_input)
    assert decoded["cursor"] == cursor
    assert decoded["stdout_page_budget_bytes"] == remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES


def test_default_remote_stdout_page_budget_stays_below_restrictive_bastion_limit():
    assert remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES <= 48 * 1024


def test_remote_collect_script_emits_next_cursor_when_budget_is_tight(tmp_path):
    lines = []
    for i in range(8):
        lines.append(
            json.dumps(
                {
                    "created_at": "2026-03-08T01:02:03Z",
                    "model": "m",
                    "usage": {"input_tokens": 50 + i, "output_tokens": 1, "cache_read_input_tokens": 0},
                }
            )
        )
    fake_file = tmp_path / "tight.jsonl"
    file_mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    fake_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake_file.touch()
    __import__("os").utime(fake_file, (file_mtime, file_mtime))

    payload = base64.b64encode(
        json.dumps(
            {
                "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
                "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                "max_files": 100,
                "max_total_bytes": 1024 * 1024,
                "stdout_page_budget_bytes": 900,
                "cursor": None,
            }
        ).encode("utf-8")
    ).decode("ascii")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
    assert error is None
    assert parsed is not None
    assert parsed["next_cursor"] is not None
    assert isinstance(parsed["next_cursor"], dict)
    assert len(parsed["events"]) < 8

    budget = 900
    raw_page = json.dumps(
        {"events": parsed["events"], "warnings": parsed["warnings"], "next_cursor": parsed["next_cursor"]},
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(raw_page) <= budget

    nc = parsed["next_cursor"]
    assert nc["job_index"] == 0
    assert nc["pattern_index"] == 0
    assert nc["file_index"] == 0
    # Next page resumes at the first source line not included in this page (0-based line index).
    assert nc["line_index"] == len(parsed["events"])


def _serialized_remote_page_bytes(parsed: dict) -> int:
    return len(
        json.dumps(
            {
                "events": parsed["events"],
                "warnings": parsed["warnings"],
                "next_cursor": parsed["next_cursor"],
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _chunked_wire_stdout_bytes(parsed: dict) -> int:
    framed = remote_file._encode_chunked_stdout_payload(
        {
            "events": parsed["events"],
            "warnings": parsed["warnings"],
            "next_cursor": parsed["next_cursor"],
        }
    )
    return len((framed + "\n").encode("utf-8"))


def test_remote_collect_script_respects_budget_including_serialized_next_cursor(tmp_path):
    """Trimmed page JSON (events + warnings + non-null next_cursor) must stay within stdout_page_budget_bytes."""
    lines = []
    for i in range(12):
        lines.append(
            json.dumps(
                {
                    "created_at": "2026-03-08T01:02:03Z",
                    "model": "m",
                    "usage": {"input_tokens": 100 + i, "output_tokens": 2, "cache_read_input_tokens": 0},
                }
            )
        )
    fake_file = tmp_path / "budget.jsonl"
    file_mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    fake_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake_file.touch()
    __import__("os").utime(fake_file, (file_mtime, file_mtime))

    # Several tight budgets: page must never exceed budget once next_cursor is included in the serialized page.
    for budget in (600, 650, 700, 800, 900, 1200):
        payload = base64.b64encode(
            json.dumps(
                {
                    "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
                    "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                    "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                    "max_files": 100,
                    "max_total_bytes": 1024 * 1024,
                    "stdout_page_budget_bytes": budget,
                    "cursor": None,
                }
            ).encode("utf-8")
        ).decode("ascii")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

        parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
        assert error is None
        assert parsed is not None
        if parsed["next_cursor"] is not None:
            assert _serialized_remote_page_bytes(parsed) <= budget

    payload_650 = base64.b64encode(
        json.dumps(
            {
                "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
                "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                "max_files": 100,
                "max_total_bytes": 1024 * 1024,
                "stdout_page_budget_bytes": 650,
                "cursor": None,
            }
        ).encode("utf-8")
    ).decode("ascii")
    stdout_650 = io.StringIO()
    with redirect_stdout(stdout_650):
        exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload_650, "__name__": "__main__"})
    parsed_650, _, err_650 = remote_file._decode_chunked_stdout_payload(stdout_650.getvalue())
    assert err_650 is None and parsed_650 is not None
    assert parsed_650["next_cursor"] is not None
    assert len(parsed_650["events"]) < 12


def test_remote_collect_script_respects_budget_for_chunked_wire_stdout(tmp_path):
    """Chunked stdout framing must also fit within stdout_page_budget_bytes, not just the raw JSON page."""
    lines = []
    for i in range(12):
        lines.append(
            json.dumps(
                {
                    "created_at": "2026-03-08T01:02:03Z",
                    "model": "m",
                    "usage": {"input_tokens": 100 + i, "output_tokens": 2, "cache_read_input_tokens": 0},
                }
            )
        )
    fake_file = tmp_path / "wire-budget.jsonl"
    file_mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    fake_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake_file.touch()
    __import__("os").utime(fake_file, (file_mtime, file_mtime))

    for budget in (900, 1200, 1600):
        payload = base64.b64encode(
            json.dumps(
                {
                    "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
                    "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                    "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                    "max_files": 100,
                    "max_total_bytes": 1024 * 1024,
                    "stdout_page_budget_bytes": budget,
                    "cursor": None,
                }
            ).encode("utf-8")
        ).decode("ascii")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

        parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
        assert error is None
        assert parsed is not None
        assert _chunked_wire_stdout_bytes(parsed) <= budget


def test_remote_collect_script_resumes_jsonl_from_line_cursor(tmp_path):
    """With a non-null cursor, skip earlier physical lines in the current JSONL file (0-based line_index)."""
    lines = []
    for i in range(4):
        lines.append(
            json.dumps(
                {
                    "created_at": "2026-03-08T01:02:03Z",
                    "model": "m",
                    "usage": {"input_tokens": 10 + i, "output_tokens": 1, "cache_read_input_tokens": 0},
                }
            )
        )
    fake_file = tmp_path / "resume.jsonl"
    file_mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    fake_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake_file.touch()
    __import__("os").utime(fake_file, (file_mtime, file_mtime))

    base_payload = {
        "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
        "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
        "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
        "max_files": 100,
        "max_total_bytes": 1024 * 1024,
        "stdout_page_budget_bytes": 0,
    }

    payload_full = base64.b64encode(json.dumps({**base_payload, "cursor": None}).encode("utf-8")).decode("ascii")
    stdout_full = io.StringIO()
    with redirect_stdout(stdout_full):
        exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload_full, "__name__": "__main__"})
    parsed_full, _, err_full = remote_file._decode_chunked_stdout_payload(stdout_full.getvalue())
    assert err_full is None
    assert len(parsed_full["events"]) == 4
    assert [e["input_tokens"] for e in parsed_full["events"]] == [10, 11, 12, 13]

    cursor = {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 2}
    payload_resume = base64.b64encode(json.dumps({**base_payload, "cursor": cursor}).encode("utf-8")).decode("ascii")
    stdout_resume = io.StringIO()
    with redirect_stdout(stdout_resume):
        exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload_resume, "__name__": "__main__"})
    parsed_resume, _, err_resume = remote_file._decode_chunked_stdout_payload(stdout_resume.getvalue())
    assert err_resume is None
    assert [e["input_tokens"] for e in parsed_resume["events"]] == [12, 13]


def test_remote_collect_script_next_cursor_reflects_file_index_after_sorted_glob(tmp_path):
    """Deterministic sorted glob: second file gets file_index=1 when resume leaves the first file."""
    row = json.dumps(
        {
            "created_at": "2026-03-08T01:02:03Z",
            "model": "m",
            "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 0},
        }
    )

    (tmp_path / "a.jsonl").write_text(row + "\n", encoding="utf-8")
    (tmp_path / "z.jsonl").write_text(row + "\n", encoding="utf-8")
    mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    for path in (tmp_path / "a.jsonl", tmp_path / "z.jsonl"):
        __import__("os").utime(path, (mtime, mtime))

    parsed = None
    budget = None
    for candidate_budget in (700, 800, 900, 1000, 1100, 1200):
        payload = base64.b64encode(
            json.dumps(
                {
                    "jobs": [{"tool": "claude_code", "patterns": [str(tmp_path / "*.jsonl")]}],
                    "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                    "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                    "max_files": 100,
                    "max_total_bytes": 1024 * 1024,
                    "stdout_page_budget_bytes": candidate_budget,
                    "cursor": None,
                }
            ).encode("utf-8")
        ).decode("ascii")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

        parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
        assert error is None
        assert parsed is not None
        if len(parsed["events"]) == 1 and parsed["next_cursor"] is not None:
            budget = candidate_budget
            break

    assert parsed is not None
    assert budget is not None
    assert len(parsed["events"]) == 1
    assert parsed["next_cursor"] is not None
    assert parsed["next_cursor"]["file_index"] == 1
    assert parsed["next_cursor"]["line_index"] == 0
    assert _chunked_wire_stdout_bytes(parsed) <= budget


def test_build_uploaded_remote_script_includes_cursor_in_payload(tmp_path):
    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
    )
    start = datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)
    collector._active_start_value = start
    collector._active_end_value = end

    cursor = {"job_index": 0, "pattern_index": 1, "file_index": 2, "line_index": 40}
    combined = collector._build_uploaded_remote_script(remote_file._COLLECT_SCRIPT, cursor=cursor)
    assert combined.startswith("PAYLOAD_B64 = ")
    line0, _rest = combined.split("\n", 1)
    rhs = line0.split("=", 1)[1].strip()
    decoded = json.loads(base64.b64decode(ast.literal_eval(rhs)).decode("utf-8"))
    assert decoded["cursor"] == cursor
    assert decoded["stdout_page_budget_bytes"] == remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES
    assert collector._remote_collect_payload_b64(cursor) == ast.literal_eval(rhs)


def test_remote_collect_script_next_cursor_reflects_pattern_index_across_patterns(tmp_path):
    """Two patterns in one job: resume point on the second pattern uses pattern_index=1 (not a default 0)."""
    row = json.dumps(
        {
            "created_at": "2026-03-08T01:02:03Z",
            "model": "m",
            "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 0},
        }
    )
    (tmp_path / "first.jsonl").write_text(row + "\n", encoding="utf-8")
    many = "\n".join([row] * 24) + "\n"
    (tmp_path / "second.jsonl").write_text(many, encoding="utf-8")
    mtime = datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc).timestamp()
    for path in (tmp_path / "first.jsonl", tmp_path / "second.jsonl"):
        __import__("os").utime(path, (mtime, mtime))

    # Pick the smallest budget in a tight range that still allows one event from first.jsonl
    # plus a resume cursor into the second pattern when counted as chunked stdout.
    parsed = None
    budget = None
    for candidate_budget in (700, 800, 900, 1000, 1100, 1200):
        payload = base64.b64encode(
            json.dumps(
                {
                    "jobs": [
                        {
                            "tool": "claude_code",
                            "patterns": [str(tmp_path / "first.jsonl"), str(tmp_path / "second.jsonl")],
                        }
                    ],
                    "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
                    "end_ts": datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc).timestamp(),
                    "max_files": 100,
                    "max_total_bytes": 1024 * 1024,
                    "stdout_page_budget_bytes": candidate_budget,
                    "cursor": None,
                }
            ).encode("utf-8")
        ).decode("ascii")

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec(remote_file._COLLECT_SCRIPT, {"PAYLOAD_B64": payload, "__name__": "__main__"})

        parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
        assert error is None
        assert parsed is not None
        next_cursor = parsed["next_cursor"]
        if (
            len(parsed["events"]) >= 1
            and isinstance(next_cursor, dict)
            and next_cursor["pattern_index"] == 1
            and next_cursor["file_index"] == 0
            and next_cursor["line_index"] == 0
        ):
            budget = candidate_budget
            break

    assert parsed is not None
    assert budget is not None
    assert parsed["next_cursor"] is not None
    assert parsed["next_cursor"]["pattern_index"] == 1
    assert parsed["next_cursor"]["file_index"] == 0
    assert parsed["next_cursor"]["line_index"] == 0
    assert _serialized_remote_page_bytes(parsed) <= budget
    assert _chunked_wire_stdout_bytes(parsed) <= budget


def test_remote_file_collector_skips_python2_candidate_and_uses_later_python3():
    """Skip a python2.7 path, then accept a later python3 from common-path discovery."""
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append((cmd, input))
        if cmd[:1] != ["ssh"]:
            return _Completed()
        rc = _remote_command(cmd)
        # Forward-compatible: version probe before accepting a candidate (implementation TBD).
        if "version_info" in rc or "sys.version_info" in rc:
            if "/usr/bin/python3" in rc:
                return _Completed(stdout="3.12.0\n")
            if "/usr/bin/python" in rc and "/usr/bin/python3" not in rc:
                return _Completed(stdout="2.7.18\n")
        if "command -v python3" in rc and "for candidate in" not in rc:
            return _Completed(stdout="/usr/bin/python\n")
        if "for candidate in /usr/bin/python3" in rc:
            return _Completed(stdout="/usr/bin/python3\n")
        if input is not None and "import base64, glob, json, os, sys" in input:
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    ok, msg = collector.probe()
    assert ok
    assert "remote files detected" in msg
    probe_ssh = [
        cmd
        for cmd, input_text in calls
        if cmd[:1] == ["ssh"] and input_text and "import base64, glob, json, os, sys" in input_text
    ]
    assert len(probe_ssh) == 1
    assert "/usr/bin/python3" in _remote_command(probe_ssh[0])


def test_remote_file_collector_errors_when_all_remote_python_candidates_are_too_old(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append((cmd, input))
        if cmd[:1] != ["ssh"]:
            return _Completed()
        rc = _remote_command(cmd)
        if "version_info" in rc or "sys.version_info" in rc:
            return _Completed(stdout="2.7.18\n")
        if "command -v python3" in rc and "for candidate in" not in rc:
            return _Completed(stdout="/usr/bin/python3\n")
        if "for candidate in /usr/bin/python3" in rc:
            return _Completed(stdout="")
        if input is not None and "import base64, glob, json, os, sys" in input:
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    ok, msg = collector.probe()
    assert ok is False
    assert ">=3.6" in msg


def test_remote_python_minimum_version_reads_bundled_config():
    assert remote_file._remote_python_minimum_version() == (3, 6)


def test_remote_file_collector_reports_chunked_stdout_corruption(monkeypatch):
    printed: list[str] = []
    prefix = remote_file._CHUNKED_STDOUT_PREFIX
    broken = (
        f"{prefix} BEGIN total_chunks=1 total_bytes=10 sha256={'a' * 64}\n"
        f"{prefix} CHUNK index=0 data=xxxx\n"
        # missing END and invalid framing — must not collapse to generic non-JSON message
    )

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=broken)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert len(out.warnings) == 1
    assert out.warnings[0].startswith("server_a/codex: remote chunked stdout")
    assert "non-JSON" not in out.warnings[0]
    assert not any("remote stdout noise:" in line for line in printed)
    assert any("remote stdout debug:" in line for line in printed)
    assert any("remote stdout preview:" in line for line in printed)


def test_ssh_auth_failure_raises_ssh_authentication_error():
    """Permission denied from SSH should raise SshAuthenticationError."""
    import pytest

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        return _Completed(returncode=255, stderr="user@host: Permission denied (publickey,password).")

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    with pytest.raises(SshAuthenticationError) as exc_info:
        collector.probe()
    assert exc_info.value.source_name == "server_a"


def test_password_auth_uses_paramiko_instead_of_system_ssh(monkeypatch):
    calls = []
    call_count = {"n": 0}

    def _fake_paramiko(**kwargs):  # noqa: ANN001, ANN201
        call_count["n"] += 1
        calls.append(kwargs["remote_args"])
        if call_count["n"] == 1:
            return _Completed(returncode=0, stdout="python3")
        if call_count["n"] == 2:
            return _Completed(returncode=0, stdout="3.9.0\n")
        return _Completed(returncode=0, stdout=json.dumps({"matches": 1}))

    monkeypatch.setattr(remote_file, "_run_remote_command_with_paramiko", _fake_paramiko)

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        ssh_password="fallback_password",
    )
    ok, _msg = collector.probe()
    assert ok
    assert calls


def test_is_ssh_auth_failure_helper():
    from llm_usage.collectors.remote_file import _is_ssh_auth_failure

    assert _is_ssh_auth_failure("user@host: Permission denied (publickey,password).")
    assert _is_ssh_auth_failure("Permission denied (publickey).")
    assert _is_ssh_auth_failure("PERMISSION DENIED (publickey).")
    assert not _is_ssh_auth_failure("Connection timed out")
    assert not _is_ssh_auth_failure("No route to host")
    assert not _is_ssh_auth_failure("")


# --- _ssh_base_command jump host tests ---

def test_ssh_base_command_no_jump_host():
    from llm_usage.collectors.remote_file import _ssh_base_command

    cmd = _ssh_base_command("deploy@10.0.0.5", 22, use_connection_sharing=False, batch_mode=False)
    assert cmd == ["ssh", "-o", "ConnectTimeout=10", "-p", "22", "deploy@10.0.0.5"]


def test_ssh_base_command_windows_direct_disables_connection_sharing(monkeypatch):
    monkeypatch.setattr(remote_file, "_is_windows_platform", lambda: True, raising=False)

    cmd = remote_file._ssh_base_command(
        "deploy@10.0.0.5", 22,
        use_connection_sharing=True, batch_mode=True,
    )

    assert "ControlMaster=auto" not in cmd
    assert "ControlPersist=5m" not in cmd
    assert "ControlPath=/tmp/llm-usage-ssh-%C" not in cmd
    assert "deploy@10.0.0.5" in cmd


def test_ssh_base_command_with_jump_host():
    from llm_usage.collectors.remote_file import _ssh_base_command

    cmd = _ssh_base_command(
        "deploy@10.0.0.5", 22,
        use_connection_sharing=False, batch_mode=False,
        jump_host="bastion.example.com", jump_port=2222,
    )
    assert cmd == [
        "ssh", "-o", "ConnectTimeout=10",
        "-p", "2222",
        "deploy@deploy@10.0.0.5@bastion.example.com",
    ]


def test_ssh_base_command_jump_host_with_batch_mode():
    from llm_usage.collectors.remote_file import _ssh_base_command

    cmd = _ssh_base_command(
        "alice@server", 22,
        use_connection_sharing=False, batch_mode=True,
        jump_host="jump.host", jump_port=3333,
    )
    assert "-p" in cmd
    port_idx = cmd.index("-p")
    assert cmd[port_idx + 1] == "3333"
    assert "alice@alice@server@jump.host" in cmd
    assert cmd[3:5] == ["-o", "BatchMode=yes"]


def test_ssh_base_command_jump_host_with_connection_sharing(monkeypatch):
    from llm_usage.collectors.remote_file import _ssh_base_command

    monkeypatch.setattr(remote_file, "_is_windows_platform", lambda: False)
    cmd = _ssh_base_command(
        "bob@host-b", 22,
        use_connection_sharing=True, batch_mode=False,
        jump_host="bastion", jump_port=2222,
    )
    assert "ControlMaster=auto" in cmd
    assert "bob@bob@host-b@bastion" in cmd
    assert cmd[cmd.index("-p") + 1] == "2222"


def test_ssh_base_command_windows_jump_host_disables_connection_sharing(monkeypatch):
    monkeypatch.setattr(remote_file, "_is_windows_platform", lambda: True, raising=False)

    cmd = remote_file._ssh_base_command(
        "bob@host-b", 22,
        use_connection_sharing=True, batch_mode=False,
        jump_host="bastion", jump_port=2222,
    )

    assert "ControlMaster=auto" not in cmd
    assert "ControlPersist=5m" not in cmd
    assert "ControlPath=/tmp/llm-usage-ssh-%C" not in cmd
    assert "bob@bob@host-b@bastion" in cmd
    assert cmd[cmd.index("-p") + 1] == "2222"


def test_ssh_base_command_jump_host_custom_port_overrides_target_port():
    from llm_usage.collectors.remote_file import _ssh_base_command

    cmd = _ssh_base_command(
        "user@target", 8022,
        use_connection_sharing=False, batch_mode=False,
        jump_host="jump", jump_port=9999,
    )
    # Target port 8022 should be replaced by jump port 9999
    assert cmd[cmd.index("-p") + 1] == "9999"
