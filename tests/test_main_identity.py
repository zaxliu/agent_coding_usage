import llm_usage.main as main


def test_required_org_username_uses_env(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "alice")
    username = main._required_org_username()
    assert username == "alice"


def test_required_org_username_raises_when_missing(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "")
    try:
        main._required_org_username()
    except RuntimeError as exc:
        assert "ORG_USERNAME" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
