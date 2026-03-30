import llm_usage.main as main
import builtins
from argparse import Namespace


def test_required_org_username_uses_env(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "alice")
    username = main._required_org_username()
    assert username == "alice"


def test_required_org_username_raises_when_missing(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "")
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: False)
    try:
        main._required_org_username()
    except RuntimeError as exc:
        assert "ORG_USERNAME" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_required_org_username_prompts_and_persists(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=\n", encoding="utf-8")
    monkeypatch.setenv("ORG_USERNAME", "")
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "alice")

    username = main._required_org_username()

    assert username == "alice"
    assert "ORG_USERNAME=alice\n" in env_path.read_text(encoding="utf-8")


def test_required_org_username_empty_input_exits(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "")
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    try:
        main._required_org_username()
    except RuntimeError as exc:
        assert "必填" in str(exc) or "required" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_load_runtime_env_bootstraps_user_env(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr(main, "read_bootstrap_env_text", lambda: "HASH_SALT=team-salt\n")
    monkeypatch.delenv("HASH_SALT", raising=False)

    main._load_runtime_env()

    assert env_path.read_text(encoding="utf-8") == "HASH_SALT=team-salt\n"
    assert main.os.environ["HASH_SALT"] == "team-salt"


def test_build_aggregates_passes_runtime_passwords_to_remote_collectors(monkeypatch):
    config = main.parse_remote_configs_from_env(
        {
            "REMOTE_HOSTS": "server_a",
            "REMOTE_SERVER_A_SSH_HOST": "host-a",
            "REMOTE_SERVER_A_SSH_USER": "alice",
            "REMOTE_SERVER_A_USE_SSHPASS": "1",
        }
    )[0]
    captured = {}

    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "_required_org_username", lambda: "alice")
    monkeypatch.setattr(main, "_required_env", lambda name: "salt")
    monkeypatch.setenv("TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("LOOKBACK_DAYS", "7")
    monkeypatch.setattr(main, "parse_remote_configs_from_env", lambda: [config])
    monkeypatch.setattr(
        main,
        "_resolve_remote_selection",
        lambda args, configured_remotes: ([config.alias], [], {config.alias: "run-secret"}),
    )
    monkeypatch.setattr(main, "_collectors", lambda local_hash: [])

    def _fake_build_remote_collectors(configs, username, salt, runtime_passwords=None):  # noqa: ANN001, ANN201
        captured["configs"] = configs
        captured["runtime_passwords"] = runtime_passwords
        return []

    monkeypatch.setattr(main, "build_remote_collectors", _fake_build_remote_collectors)
    monkeypatch.setattr(main, "_collect_all", lambda lookback_days, collectors: ([], []))
    monkeypatch.setattr(main, "aggregate_events", lambda events, user_hash, timezone_name: [])

    rows, warnings = main._build_aggregates(Namespace(ui="cli"))

    assert rows == []
    assert warnings == []
    assert captured["configs"] == [config]
    assert captured["runtime_passwords"] == {config.alias: "run-secret"}


def test_cmd_whoami_prints_user_and_per_host_hashes(monkeypatch, capsys):
    config_a = main.parse_remote_configs_from_env(
        {
            "REMOTE_HOSTS": "server_a,server_b",
            "REMOTE_SERVER_A_SSH_HOST": "host-a",
            "REMOTE_SERVER_A_SSH_USER": "alice",
            "REMOTE_SERVER_B_SSH_HOST": "host-b",
            "REMOTE_SERVER_B_SSH_USER": "alice",
        }
    )
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "_required_org_username", lambda: "alice")
    monkeypatch.setattr(main, "_required_env", lambda name: "team-salt")
    monkeypatch.setattr(main, "parse_remote_configs_from_env", lambda: config_a)

    rc = main.cmd_whoami(Namespace())

    out = capsys.readouterr().out
    assert rc == 0
    assert "ORG_USERNAME: alice" in out
    assert "user_hash:" in out
    assert "source_host_hash(local):" in out
    assert "source_host_hash(server_a):" in out
    assert "source_host_hash(server_b):" in out
