import llm_usage.main as main


class _DummyStdin:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_resolve_username_uses_env(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "alice")
    username, warning = main._resolve_username()
    assert username == "alice"
    assert warning is None


def test_resolve_username_uses_interactive_input(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "")
    monkeypatch.setattr(main.sys, "stdin", _DummyStdin(is_tty=True))
    monkeypatch.setattr("builtins.input", lambda _: "alice")
    username, warning = main._resolve_username()
    assert username == "alice"
    assert warning is None


def test_resolve_username_falls_back_to_anonymous(monkeypatch):
    monkeypatch.setenv("ORG_USERNAME", "")
    monkeypatch.setattr(main.sys, "stdin", _DummyStdin(is_tty=False))
    username, warning = main._resolve_username()
    assert username == "anonymous"
    assert warning == "ORG_USERNAME is empty; using anonymous identifier"
