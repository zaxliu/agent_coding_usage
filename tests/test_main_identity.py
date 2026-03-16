import llm_usage.main as main
import builtins


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
