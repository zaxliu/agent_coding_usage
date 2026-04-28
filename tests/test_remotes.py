import subprocess
from types import MethodType

from llm_usage.env import EnvDocument, EnvLine
from llm_usage.remotes import (
    ClineRemoteCollector,
    append_remote_to_env,
    apply_remote_drafts_to_document,
    build_remote_collectors,
    build_temporary_remote,
    default_source_label,
    drafts_from_env_document,
    normalize_alias,
    RemoteDraft,
    parse_remote_configs_from_env,
    probe_remote_ssh,
    unique_alias,
)


def test_parse_remote_configs_from_env():
    env = {
        "REMOTE_HOSTS": "server_a,server-b",
        "REMOTE_SERVER_A_SSH_HOST": "host-a",
        "REMOTE_SERVER_A_SSH_USER": "alice",
        "REMOTE_SERVER_A_LABEL": "prod-a",
        "REMOTE_SERVER_A_COPILOT_CLI_LOG_PATHS": "/tmp/copilot-cli.jsonl",
        "REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS": "/tmp/cline-history.json",
        "REMOTE_SERVER_B_SSH_HOST": "host-b",
        "REMOTE_SERVER_B_SSH_USER": "bob",
    }
    configs = parse_remote_configs_from_env(env)
    assert [config.alias for config in configs] == ["SERVER_A", "SERVER_B"]
    assert configs[0].ssh_port == 22
    assert configs[0].source_label == "prod-a"
    assert configs[0].copilot_cli_log_paths == ["/tmp/copilot-cli.jsonl"]
    assert configs[0].cline_vscode_session_paths == ["/tmp/cline-history.json"]


def test_parse_remote_configs_from_env_defaults_source_label_to_user_and_host():
    env = {
        "REMOTE_HOSTS": "server_a",
        "REMOTE_SERVER_A_SSH_HOST": "10.0.0.8",
        "REMOTE_SERVER_A_SSH_USER": "alice",
    }
    configs = parse_remote_configs_from_env(env)
    assert configs[0].source_label == "alice@10.0.0.8"


def test_parse_remote_configs_from_env_ignores_legacy_use_sshpass_flag():
    env = {
        "REMOTE_HOSTS": "server_a",
        "REMOTE_SERVER_A_SSH_HOST": "host-a",
        "REMOTE_SERVER_A_SSH_USER": "alice",
        "REMOTE_SERVER_A_USE_SSHPASS": "1",
    }

    configs = parse_remote_configs_from_env(env)

    assert not hasattr(configs[0], "use_sshpass")


def test_parse_remote_configs_from_env_honors_explicit_empty_env_when_process_env_has_remotes(monkeypatch):
    monkeypatch.setenv("REMOTE_HOSTS", "server_a")
    monkeypatch.setenv("REMOTE_SERVER_A_SSH_HOST", "host-a")
    monkeypatch.setenv("REMOTE_SERVER_A_SSH_USER", "alice")

    env = {}

    configs = parse_remote_configs_from_env(env)

    assert configs == []


def test_drafts_from_env_document_reads_structured_remote_fields():
    document = EnvDocument(
        lines=[
            EnvLine(kind="entry", key="REMOTE_HOSTS", value="SERVER_A"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_HOST", value="host-a"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_USER", value="alice"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_PORT", value="2200"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_LABEL", value="prod-a"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_CLAUDE_LOG_PATHS", value="/a,/b"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_USE_SSHPASS", value="1"),
        ]
    )

    drafts = drafts_from_env_document(document)

    assert len(drafts) == 1
    assert drafts[0] == RemoteDraft(
        alias="SERVER_A",
        ssh_host="host-a",
        ssh_user="alice",
        ssh_port=2200,
        source_label="prod-a",
        claude_log_paths=["/a", "/b"],
        codex_log_paths=list(drafts[0].codex_log_paths),
        copilot_cli_log_paths=list(drafts[0].copilot_cli_log_paths),
        copilot_vscode_session_paths=list(drafts[0].copilot_vscode_session_paths),
        cline_vscode_session_paths=list(drafts[0].cline_vscode_session_paths),
    )


def test_apply_remote_drafts_to_document_normalizes_and_dedupes_aliases():
    document = EnvDocument(
        lines=[
            EnvLine(kind="entry", key="REMOTE_HOSTS", value="OLD"),
            EnvLine(kind="entry", key="REMOTE_OLD_SSH_HOST", value="old-host"),
            EnvLine(kind="entry", key="REMOTE_OLD_SSH_USER", value="old-user"),
        ]
    )
    drafts = [
        RemoteDraft(
            alias="prod-a",
            ssh_host="host-a",
            ssh_user="alice",
            ssh_port=22,
            source_label="alice@host-a",
            claude_log_paths=[],
            codex_log_paths=[],
            copilot_cli_log_paths=[],
            copilot_vscode_session_paths=[],
            cline_vscode_session_paths=[],
        ),
        RemoteDraft(
            alias="PROD_A",
            ssh_host="host-b",
            ssh_user="bob",
            ssh_port=2200,
            source_label="bob@host-b",
            claude_log_paths=[],
            codex_log_paths=[],
            copilot_cli_log_paths=[],
            copilot_vscode_session_paths=[],
            cline_vscode_session_paths=[],
        ),
    ]

    apply_remote_drafts_to_document(document, drafts)

    assert document.get("REMOTE_HOSTS") == "PROD_A,PROD_A_2"
    assert document.get("REMOTE_PROD_A_SSH_HOST") == "host-a"
    assert document.get("REMOTE_PROD_A_2_SSH_HOST") == "host-b"
    assert document.get("REMOTE_PROD_A_2_USE_SSHPASS") is None
    assert document.get("REMOTE_OLD_SSH_HOST") is None


def test_apply_remote_drafts_to_document_rewrites_remote_section():
    document = EnvDocument(
        lines=[
            EnvLine(kind="entry", key="ORG_USERNAME", value="alice"),
            EnvLine(kind="entry", key="REMOTE_HOSTS", value="OLD"),
            EnvLine(kind="entry", key="REMOTE_OLD_SSH_HOST", value="old-host"),
            EnvLine(kind="entry", key="REMOTE_OLD_SSH_USER", value="old-user"),
        ]
    )
    drafts = [
        RemoteDraft(
            alias="SERVER_A",
            ssh_host="host-a",
            ssh_user="alice",
            ssh_port=22,
            source_label="alice@host-a",
            claude_log_paths=["/a"],
            codex_log_paths=["/b"],
            copilot_cli_log_paths=["/c"],
            copilot_vscode_session_paths=["/d"],
            cline_vscode_session_paths=["/e"],
        )
    ]

    apply_remote_drafts_to_document(document, drafts)

    assert document.get("ORG_USERNAME") == "alice"
    assert document.get("REMOTE_HOSTS") == "SERVER_A"
    assert document.get("REMOTE_SERVER_A_SSH_HOST") == "host-a"
    assert document.get("REMOTE_SERVER_A_SSH_USER") == "alice"
    assert document.get("REMOTE_SERVER_A_SSH_PORT") == "22"
    assert document.get("REMOTE_SERVER_A_LABEL") == "alice@host-a"
    assert document.get("REMOTE_SERVER_A_CLAUDE_LOG_PATHS") == "/a"
    assert document.get("REMOTE_SERVER_A_CODEX_LOG_PATHS") == "/b"
    assert document.get("REMOTE_SERVER_A_COPILOT_CLI_LOG_PATHS") == "/c"
    assert document.get("REMOTE_SERVER_A_COPILOT_VSCODE_SESSION_PATHS") == "/d"
    assert document.get("REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS") == "/e"
    assert document.get("REMOTE_SERVER_A_USE_SSHPASS") is None
    assert document.get("REMOTE_OLD_SSH_HOST") is None
    assert document.get("REMOTE_OLD_SSH_USER") is None


def test_build_remote_collectors_sets_per_user_source_hash():
    config = build_temporary_remote("host-a", "alice")
    collectors_a = build_remote_collectors([config], username="alice", salt="salt")
    collectors_b = build_remote_collectors([config], username="bob", salt="salt")
    assert collectors_a[0].source_host_hash != collectors_b[0].source_host_hash
    assert len(collectors_a) == 1
    assert collectors_a[0].name == "remote"


def test_build_remote_collectors_uses_runtime_password_for_remote():
    config = build_temporary_remote("host-a", "alice")

    collectors = build_remote_collectors(
        [config],
        username="alice",
        salt="salt",
        runtime_passwords={config.alias: "run-secret"},
    )

    assert collectors[0].ssh_password == "run-secret"


def test_cline_remote_probe_counts_non_cline_jobs_without_false_no_data():
    collector = ClineRemoteCollector(
        "remote",
        target=None,  # type: ignore[arg-type]
        source_name="server_a",
        source_host_hash="hash",
        jobs=[],
    )

    collector._discover_python = MethodType(lambda self: ("python3", None), collector)  # type: ignore[method-assign]
    collector._run_python_script = MethodType(  # type: ignore[method-assign]
        lambda self, python_cmd, script, cursor=None, use_page_payload=False: ({"matches": 2, "versions": []}, None),
        collector,
    )

    ok, msg = collector.probe()

    assert ok is True
    assert msg == "2 remote files detected"


def test_build_temporary_remote_preserves_explicit_empty_path_lists():
    config = build_temporary_remote("host-a", "alice", claude_log_paths=[], codex_log_paths=[])

    assert config.claude_log_paths == []
    assert config.codex_log_paths == []


def test_append_remote_to_env_writes_remote_fields(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("REMOTE_HOSTS=SERVER_A\n", encoding="utf-8")
    config = build_temporary_remote("host-b", "bob", 2200)
    alias = append_remote_to_env(env_path, config, ["SERVER_A"])
    text = env_path.read_text(encoding="utf-8")
    assert alias == "BOB_HOST_B"
    assert "REMOTE_HOSTS=SERVER_A,BOB_HOST_B" in text
    assert "REMOTE_BOB_HOST_B_SSH_HOST=host-b" in text
    assert "REMOTE_BOB_HOST_B_SSH_PORT=2200" in text
    assert "REMOTE_BOB_HOST_B_LABEL=bob@host-b" in text
    assert "REMOTE_BOB_HOST_B_COPILOT_CLI_LOG_PATHS=" in text
    assert "REMOTE_BOB_HOST_B_COPILOT_VSCODE_SESSION_PATHS=" in text
    assert "REMOTE_BOB_HOST_B_CLINE_VSCODE_SESSION_PATHS=" in text


def test_append_remote_to_env_removes_legacy_use_sshpass_flag(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("REMOTE_BOB_HOST_B_USE_SSHPASS=1\n", encoding="utf-8")
    config = build_temporary_remote("host-b", "bob", 2200)

    append_remote_to_env(env_path, config, [])

    text = env_path.read_text(encoding="utf-8")
    assert "USE_SSHPASS" not in text


def test_alias_helpers_normalize_and_dedupe():
    assert normalize_alias("prod-a") == "PROD_A"
    assert unique_alias("prod-a", ["PROD_A"]) == "PROD_A_2"


def test_default_source_label_uses_user_and_host():
    assert default_source_label("alice", "10.0.0.8") == "alice@10.0.0.8"


def test_probe_remote_ssh_uses_connection_sharing(monkeypatch):
    captured = {}
    monkeypatch.setattr("llm_usage.collectors.remote_file._is_windows_platform", lambda: False)

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001, ANN201
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200))

    assert ok
    assert msg == "SSH 连接正常"
    assert captured["cmd"][0] == "ssh"
    assert "ControlMaster=auto" in captured["cmd"]
    assert "ControlPersist=5m" in captured["cmd"]
    assert "ControlPath=/tmp/llm-usage-ssh-%C" in captured["cmd"]


def test_probe_remote_ssh_without_password_uses_system_ssh(monkeypatch):
    captured = {}

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001, ANN201
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200))

    assert ok
    assert msg == "SSH 连接正常"
    assert captured["cmd"][0] == "ssh"
    assert "BatchMode=yes" in captured["cmd"]


def test_probe_remote_ssh_with_password_uses_paramiko(monkeypatch):
    captured = {}
    def _fake_paramiko_probe(config, timeout_sec, ssh_password):  # noqa: ANN001, ANN201
        captured["alias"] = config.alias
        captured["timeout_sec"] = timeout_sec
        captured["ssh_password"] = ssh_password
        return True, "SSH 连接正常"

    monkeypatch.setattr("llm_usage.remotes._probe_remote_ssh_with_paramiko", _fake_paramiko_probe)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200), ssh_password="  secret  ")

    assert ok
    assert msg == "SSH 连接正常"
    assert captured == {"alias": "ALICE_HOST_A", "timeout_sec": 10, "ssh_password": "  secret  "}


def test_probe_remote_ssh_reports_missing_ssh_when_ssh_binary_is_unavailable(monkeypatch):
    def _fake_run(cmd, check, capture_output, text, timeout, env=None):  # noqa: ANN001, ANN201
        raise FileNotFoundError

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200))

    assert ok is False
    assert msg == "SSH 命令未找到"
