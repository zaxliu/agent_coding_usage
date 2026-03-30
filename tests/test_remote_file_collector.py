import json
import subprocess
from datetime import datetime, timezone

from llm_usage.collectors.remote_file import RemoteCollectJob, RemoteFileCollector, SshTarget


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


def test_remote_file_collector_supports_python_fallback(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python")
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
    assert len(calls) == 2
    assert "BatchMode=yes" not in calls[1]


def test_remote_file_collector_uses_sshpass_env_for_collect(tmp_path):
    captured = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None, env=None):  # noqa: ANN001, ANN201
        captured.append((cmd, env))
        if cmd[:3] == ["sshpass", "-e", "ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:3] == ["sshpass", "-e", "ssh"] and input is not None:
            assert env is not None
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        runner=_runner,
        use_sshpass=True,
        ssh_password="  secret  ",
    )

    collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    probe_call = next((item for item in captured if "command -v python3" in _remote_command(item[0])), None)
    collect_call = next(
        (item for item in captured if item[1] is not None and "command -v python3" not in _remote_command(item[0])),
        None,
    )

    assert probe_call is not None
    assert collect_call is not None
    assert probe_call[0][:2] == ["sshpass", "-e"]
    assert probe_call[1]["SSHPASS"] == "  secret  "
    assert collect_call[0][:2] == ["sshpass", "-e"]
    assert collect_call[1]["SSHPASS"] == "  secret  "


def test_remote_file_collector_requires_password_for_sshpass(tmp_path, monkeypatch):
    captured = []
    monkeypatch.delenv("SSHPASS", raising=False)

    def _runner(cmd, check, capture_output, text, input=None, timeout=None, env=None):  # noqa: ANN001, ANN201
        captured.append((cmd, env))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        runner=_runner,
        use_sshpass=True,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.events == []
    assert out.warnings == ["server_a/codex: SSH 密码模式需要提供密码"]
    assert captured == []


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
                    + json.dumps({"events": [], "warnings": []})
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
                stdout="audit prefix >>> " + json.dumps({"events": [], "warnings": []}) + " <<< audit suffix"
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

    assert out.warnings == ["server_a/codex: remote command returned non-JSON output"]
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
    payload = _extract_stdin_payload(inputs[1])
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
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
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

    payload = _extract_stdin_payload(inputs[1])
    assert payload["start_ts"] == start.timestamp()
    assert payload["end_ts"] == end.timestamp()


def test_remote_file_collector_logs_remote_stderr_progress(tmp_path, monkeypatch):
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(
                stdout=json.dumps({"events": [], "warnings": []}),
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


def test_remote_file_collector_retries_without_connection_sharing_after_timeout(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        calls.append(cmd)
        has_sharing = "ControlMaster=auto" in cmd
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None and has_sharing:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
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
    execution_calls = [cmd for cmd in commands if cmd[:1] == ["ssh"] and "command -v python3" not in _remote_command(cmd)]
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
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
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
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
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
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
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

    def _fallback(python_cmd, script):  # noqa: ANN001, ANN201
        called["fallback"] = True
        return {"events": [], "warnings": []}, None

    collector._run_python_script_via_uploaded_file = _fallback  # type: ignore[method-assign]

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert called["fallback"] is True
    assert out.warnings == ["server_a/remote: no usage events in selected time range"]
