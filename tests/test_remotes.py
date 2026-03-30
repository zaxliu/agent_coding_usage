import subprocess

from llm_usage.remotes import (
    append_remote_to_env,
    build_remote_collectors,
    build_temporary_remote,
    default_source_label,
    normalize_alias,
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
        "REMOTE_SERVER_B_SSH_HOST": "host-b",
        "REMOTE_SERVER_B_SSH_USER": "bob",
    }
    configs = parse_remote_configs_from_env(env)
    assert [config.alias for config in configs] == ["SERVER_A", "SERVER_B"]
    assert configs[0].ssh_port == 22
    assert configs[0].source_label == "prod-a"
    assert configs[0].copilot_cli_log_paths == ["/tmp/copilot-cli.jsonl"]


def test_parse_remote_configs_from_env_defaults_source_label_to_user_and_host():
    env = {
        "REMOTE_HOSTS": "server_a",
        "REMOTE_SERVER_A_SSH_HOST": "10.0.0.8",
        "REMOTE_SERVER_A_SSH_USER": "alice",
    }
    configs = parse_remote_configs_from_env(env)
    assert configs[0].source_label == "alice@10.0.0.8"


def test_parse_remote_configs_from_env_reads_use_sshpass_flag():
    env = {
        "REMOTE_HOSTS": "server_a",
        "REMOTE_SERVER_A_SSH_HOST": "host-a",
        "REMOTE_SERVER_A_SSH_USER": "alice",
        "REMOTE_SERVER_A_USE_SSHPASS": "1",
    }

    configs = parse_remote_configs_from_env(env)

    assert configs[0].use_sshpass is True


def test_build_remote_collectors_sets_per_user_source_hash():
    config = build_temporary_remote("host-a", "alice")
    collectors_a = build_remote_collectors([config], username="alice", salt="salt")
    collectors_b = build_remote_collectors([config], username="bob", salt="salt")
    assert collectors_a[0].source_host_hash != collectors_b[0].source_host_hash
    assert len(collectors_a) == 1
    assert collectors_a[0].name == "remote"


def test_build_remote_collectors_uses_runtime_password_for_sshpass_remote():
    config = build_temporary_remote("host-a", "alice", use_sshpass=True)

    collectors = build_remote_collectors(
        [config],
        username="alice",
        salt="salt",
        runtime_passwords={config.alias: "run-secret"},
    )

    assert collectors[0].ssh_password == "run-secret"


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


def test_append_remote_to_env_writes_use_sshpass_flag(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    config = build_temporary_remote("host-b", "bob", 2200, use_sshpass=True)

    append_remote_to_env(env_path, config, [])

    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_BOB_HOST_B_USE_SSHPASS=1" in text


def test_alias_helpers_normalize_and_dedupe():
    assert normalize_alias("prod-a") == "PROD_A"
    assert unique_alias("prod-a", ["PROD_A"]) == "PROD_A_2"


def test_default_source_label_uses_user_and_host():
    assert default_source_label("alice", "10.0.0.8") == "alice@10.0.0.8"


def test_probe_remote_ssh_uses_connection_sharing(monkeypatch):
    captured = {}

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


def test_probe_remote_ssh_uses_sshpass_env(monkeypatch):
    captured = {}

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001, ANN201
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(
        build_temporary_remote("host-a", "alice", 2200, use_sshpass=True),
        ssh_password="  secret  ",
    )

    assert ok
    assert msg == "SSH 连接正常"
    assert captured["cmd"][:2] == ["sshpass", "-e"]
    assert captured["cmd"][2] == "ssh"
    assert captured["env"]["SSHPASS"] == "  secret  "


def test_probe_remote_ssh_uses_existing_sshpass_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("SSHPASS", "env-secret")

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001, ANN201
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200, use_sshpass=True))

    assert ok
    assert msg == "SSH 连接正常"
    assert captured["cmd"][:2] == ["sshpass", "-e"]
    assert captured["env"]["SSHPASS"] == "env-secret"


def test_probe_remote_ssh_requires_password_for_sshpass_remote(monkeypatch):
    monkeypatch.delenv("SSHPASS", raising=False)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200, use_sshpass=True))

    assert ok is False
    assert msg == "SSH 密码模式需要提供密码"


def test_probe_remote_ssh_treats_whitespace_password_as_missing(monkeypatch):
    monkeypatch.delenv("SSHPASS", raising=False)

    ok, msg = probe_remote_ssh(
        build_temporary_remote("host-a", "alice", 2200, use_sshpass=True),
        ssh_password="   ",
    )

    assert ok is False
    assert msg == "SSH 密码模式需要提供密码"


def test_probe_remote_ssh_reports_missing_sshpass(monkeypatch):
    def _fake_run(cmd, check, capture_output, text, timeout, env=None):  # noqa: ANN001, ANN201
        raise FileNotFoundError

    monkeypatch.setenv("SSHPASS", "env-secret")
    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(build_temporary_remote("host-a", "alice", 2200, use_sshpass=True))

    assert ok is False
    assert msg == "sshpass 未找到"
