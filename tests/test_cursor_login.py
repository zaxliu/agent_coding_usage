import argparse
import builtins
from types import SimpleNamespace

from llm_usage.env import load_env_document
import llm_usage.main as main
from llm_usage.env import upsert_env_var
from llm_usage.models import AggregateRecord


def test_upsert_env_var_appends_when_missing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n", encoding="utf-8")
    upsert_env_var(env_path, "CURSOR_WEB_SESSION_TOKEN", "abc")
    text = env_path.read_text(encoding="utf-8")
    assert "A=1\n" in text
    assert "CURSOR_WEB_SESSION_TOKEN=abc\n" in text


def test_upsert_env_var_replaces_existing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("CURSOR_WEB_SESSION_TOKEN=old\nB=2\n", encoding="utf-8")
    upsert_env_var(env_path, "CURSOR_WEB_SESSION_TOKEN", "new")
    text = env_path.read_text(encoding="utf-8")
    assert "CURSOR_WEB_SESSION_TOKEN=new\n" in text
    assert "CURSOR_WEB_SESSION_TOKEN=old" not in text


def test_maybe_capture_skips_when_token_exists(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "already-there")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return True, "dashboard token valid"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    called = {"capture": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        called["capture"] += 1
        return "x"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    main._maybe_capture_cursor_token(timeout_sec=60, browser="default", user_data_dir="")
    assert called["capture"] == 0


def test_maybe_capture_refreshes_expired_token(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "expired")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "authentication failed (session cookie may be expired)"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    called = {"clear": 0, "capture": 0}

    def _fake_clear():  # noqa: ANN201
        called["clear"] += 1

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 61
        assert browser == "default"
        assert login_mode == "auto"
        return "token-new"

    monkeypatch.setattr(main, "_clear_saved_cursor_token", _fake_clear)
    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)

    main._maybe_capture_cursor_token(timeout_sec=61, browser="default", user_data_dir="")
    assert called["clear"] == 1
    assert called["capture"] == 1


def test_maybe_capture_uses_local_files_without_browser(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return True, "local cursor files found"

        def collect(self, start, end):  # noqa: ANN001, ANN201
            return type("Out", (), {"events": [object()]})()

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())

    called = {"capture": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        called["capture"] += 1
        return "x"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    main._maybe_capture_cursor_token(timeout_sec=60, browser="chrome", user_data_dir="")
    assert called["capture"] == 0


def test_maybe_capture_triggers_browser_when_local_logs_have_no_events(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return True, "local cursor files found"

        def collect(self, start, end):  # noqa: ANN001, ANN201
            return type("Out", (), {"events": []})()

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    called = {"capture": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 66
        assert browser == "default"
        assert user_data_dir == "/tmp/profile"
        assert login_mode == "auto"
        return "browser-token"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    main._maybe_capture_cursor_token(
        timeout_sec=66,
        browser="default",
        user_data_dir="/tmp/profile",
    )
    assert called["capture"] == 1


def test_maybe_capture_triggers_browser_when_needed(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "no local cursor files"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    called = {"capture": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 77
        assert browser == "chromium"
        assert user_data_dir == ""
        assert login_mode == "auto"
        return "browser-token"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    main._maybe_capture_cursor_token(timeout_sec=77, browser="chromium", user_data_dir="")
    assert called["capture"] == 1


def test_maybe_capture_windows_chromium_auto_uses_managed_profile(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(
        main,
        "os",
        SimpleNamespace(
            name="nt",
            getenv=main.os.getenv,
            environ=main.os.environ,
            popen=main.os.popen,
        ),
    )

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "no local cursor files"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    capture_called = {"count": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        capture_called["count"] += 1
        assert browser == "chrome"
        assert login_mode == "managed-profile"
        return "browser-token"

    prompt_called = {"count": 0}

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    monkeypatch.setattr(
        main,
        "_prompt_for_manual_cursor_token",
        lambda browser, automatic_capture_failed: prompt_called.__setitem__("count", prompt_called["count"] + 1),
    )

    assert main._maybe_capture_cursor_token(timeout_sec=77, browser="chrome", user_data_dir="") is None
    assert prompt_called["count"] == 0
    assert capture_called["count"] == 1


def test_maybe_capture_windows_chromium_managed_profile_falls_back_to_manual_prompt(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(
        main,
        "os",
        SimpleNamespace(
            name="nt",
            getenv=main.os.getenv,
            environ=main.os.environ,
            popen=main.os.popen,
        ),
    )

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "no local cursor files"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    capture_called = {"count": 0}

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        capture_called["count"] += 1
        assert browser == "msedge"
        assert login_mode == "managed-profile"
        raise RuntimeError("cookie timeout")

    prompt_called = {"count": 0}

    def _fake_prompt(browser, automatic_capture_failed):  # noqa: ANN001, ANN201
        prompt_called["count"] += 1
        assert browser == "msedge"
        assert automatic_capture_failed is True
        return None

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    monkeypatch.setattr(main, "_prompt_for_manual_cursor_token", _fake_prompt)

    assert main._maybe_capture_cursor_token(timeout_sec=77, browser="edge", user_data_dir="") is None
    assert prompt_called["count"] == 1
    assert capture_called["count"] == 1


def test_maybe_capture_windows_expired_token_retries_managed_profile_before_manual(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "expired")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(
        main,
        "os",
        SimpleNamespace(
            name="nt",
            getenv=main.os.getenv,
            environ=main.os.environ,
            popen=main.os.popen,
        ),
    )

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "authentication failed (session cookie may be expired)"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    state = {"clear": 0, "capture": 0, "prompt": 0}

    def _fake_clear():  # noqa: ANN201
        state["clear"] += 1

    def _fake_capture(timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN001, ANN201
        state["capture"] += 1
        assert browser == "chrome"
        assert login_mode == "managed-profile"
        raise RuntimeError("cookie timeout")

    def _fake_prompt(browser, automatic_capture_failed):  # noqa: ANN001, ANN201
        state["prompt"] += 1
        assert browser == "chrome"
        assert automatic_capture_failed is True
        return "manual-token"

    monkeypatch.setattr(main, "_clear_saved_cursor_token", _fake_clear)
    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    monkeypatch.setattr(main, "_prompt_for_manual_cursor_token", _fake_prompt)

    assert main._maybe_capture_cursor_token(timeout_sec=61, browser="chrome", user_data_dir="") is None
    assert state["clear"] == 1
    assert state["prompt"] == 1
    assert state["capture"] == 1


def test_maybe_capture_cursor_token_windows_chromium_auto_uses_managed_profile(monkeypatch):
    monkeypatch.setattr(
        main,
        "os",
        SimpleNamespace(
            name="nt",
            getenv=main.os.getenv,
            environ=main.os.environ,
            popen=main.os.popen,
        ),
    )
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "_clear_saved_cursor_token", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "cursor dashboard unavailable"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())

    calls = []

    def _fake_fetch(*, timeout_sec, browser, user_data_dir, login_mode="auto"):  # noqa: ANN003
        calls.append(
            {
                "timeout_sec": timeout_sec,
                "browser": browser,
                "user_data_dir": user_data_dir,
                "login_mode": login_mode,
            }
        )
        return "token-from-browser"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_fetch)
    monkeypatch.setattr(main, "_prompt_for_manual_cursor_token", lambda *args, **kwargs: None)

    warning = main._maybe_capture_cursor_token(
        timeout_sec=60,
        browser="chrome",
        user_data_dir="",
        login_mode="auto",
    )

    assert warning is None
    assert calls == [
        {
            "timeout_sec": 60,
            "browser": "chrome",
            "user_data_dir": "",
            "login_mode": "managed-profile",
        }
    ]


def test_resolve_cursor_login_mode_windows_default_uses_managed_profile(monkeypatch):
    monkeypatch.setattr(
        main,
        "os",
        SimpleNamespace(
            name="nt",
            getenv=main.os.getenv,
            environ=main.os.environ,
            popen=main.os.popen,
        ),
    )
    monkeypatch.setattr(main, "resolve_cursor_login_browser_choice", lambda browser: "chrome")

    assert main._resolve_cursor_login_mode("auto", "default") == "managed-profile"


def test_collect_parser_accepts_cursor_login_mode():
    parser = main.build_parser()
    args = parser.parse_args(["collect", "--cursor-login-mode", "managed-profile"])
    assert args.cursor_login_mode == "managed-profile"


def test_maybe_capture_uses_manual_token_when_auto_login_fails(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "")
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    class _Collector:
        def probe(self):  # noqa: ANN201
            return False, "no local cursor files"

    monkeypatch.setattr(main, "build_cursor_collector", lambda: _Collector())
    monkeypatch.setattr(
        main,
        "_capture_and_save_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto": (_ for _ in ()).throw(
            RuntimeError("cookie timeout")
        ),
    )
    prompted = {"count": 0}

    def _fake_prompt(browser, automatic_capture_failed):  # noqa: ANN001, ANN201
        prompted["count"] += 1
        assert browser == "chrome"
        assert automatic_capture_failed is True
        return "manual-token"

    monkeypatch.setattr(main, "_prompt_for_manual_cursor_token", _fake_prompt)

    assert main._maybe_capture_cursor_token(timeout_sec=77, browser="chrome", user_data_dir="") is None
    assert prompted["count"] == 1


def test_capture_and_save_cursor_token(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr(
        main,
        "fetch_cursor_session_token_via_browser",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto": "token-from-browser",
    )
    monkeypatch.setattr(main, "fetch_cursor_workos_id_from_local_browsers", lambda browser="default": "workos-1")

    token = main._capture_and_save_cursor_token(
        timeout_sec=60,
        browser="chrome",
        user_data_dir="",
    )
    assert token == "token-from-browser"
    text = env_path.read_text(encoding="utf-8")
    assert "CURSOR_WEB_SESSION_TOKEN=token-from-browser" in text
    assert "CURSOR_WEB_WORKOS_ID=workos-1" in text


def test_prompt_for_manual_cursor_token_saves_value(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    answers = iter(["manual-token"])

    class _Stdout:
        def isatty(self):  # noqa: ANN201
            return True

        def write(self, text):  # noqa: ANN001, ANN201
            return len(text)

        def flush(self):  # noqa: ANN201
            return None

    class _Stdin:
        def isatty(self):  # noqa: ANN201
            return True

    stdout = _Stdout()
    stdin = _Stdin()
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr(main, "open_cursor_dashboard_login_page", lambda browser="default": None)
    monkeypatch.setattr(main.sys, "stdin", stdin)
    monkeypatch.setattr(main.sys, "stdout", stdout)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))

    token = main._prompt_for_manual_cursor_token("chrome", automatic_capture_failed=True)
    assert token == "manual-token"
    text = env_path.read_text(encoding="utf-8")
    assert "CURSOR_WEB_SESSION_TOKEN=manual-token" in text


def test_prompt_for_manual_cursor_token_skips_without_tty(monkeypatch):
    class _Stdout:
        def isatty(self):  # noqa: ANN201
            return False

        def write(self, text):  # noqa: ANN001, ANN201
            return len(text)

        def flush(self):  # noqa: ANN201
            return None

    class _Stdin:
        def isatty(self):  # noqa: ANN201
            return False

    stdout = _Stdout()
    stdin = _Stdin()
    monkeypatch.setattr(main.sys, "stdin", stdin)
    monkeypatch.setattr(main.sys, "stdout", stdout)
    called = {"input": 0}
    monkeypatch.setattr(builtins, "input", lambda prompt="": called.__setitem__("input", called["input"] + 1))

    assert main._prompt_for_manual_cursor_token("chrome", automatic_capture_failed=True) is None
    assert called["input"] == 0


def test_clear_saved_cursor_token(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("CURSOR_WEB_SESSION_TOKEN=abc\n", encoding="utf-8")
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "abc")

    main._clear_saved_cursor_token()

    document = load_env_document(env_path)
    assert document.get("CURSOR_WEB_SESSION_TOKEN") == ""
    assert document.get("CURSOR_WEB_WORKOS_ID") == ""
    assert "CURSOR_WEB_SESSION_TOKEN" not in main.os.environ


def test_cmd_collect_triggers_maybe_capture(monkeypatch):
    called = {"timeout": None, "browser": None, "user_data_dir": None, "login_mode": None, "lookback_days": None}
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto", lookback_days=None: (
            called.__setitem__("timeout", timeout_sec),
            called.__setitem__("browser", browser),
            called.__setitem__("user_data_dir", user_data_dir),
            called.__setitem__("login_mode", login_mode),
            called.__setitem__("lookback_days", lookback_days),
            None,
        )[-1],
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], [], {}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
            cursor_login_mode="managed-profile",
            lookback_days=30,
        )
    )
    assert exit_code == 0
    assert called["timeout"] == 88
    assert called["browser"] == "default"
    assert called["user_data_dir"] == "/tmp/p1"
    assert called["login_mode"] == "managed-profile"
    assert called["lookback_days"] == 30


def test_cmd_sync_triggers_maybe_capture(monkeypatch):
    called = {"timeout": None, "browser": None, "user_data_dir": None, "login_mode": None, "lookback_days": None}
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto", lookback_days=None: (
            called.__setitem__("timeout", timeout_sec),
            called.__setitem__("browser", browser),
            called.__setitem__("user_data_dir", user_data_dir),
            called.__setitem__("login_mode", login_mode),
            called.__setitem__("lookback_days", lookback_days),
            None,
        )[-1],
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], [], {}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    class _Client:
        def __init__(self, app_token, table_id, bot_token):  # noqa: ANN001
            assert app_token == "app"
            assert table_id == "tbl"
            assert bot_token == "bot"

        def upsert(self, rows):  # noqa: ANN001, ANN201
            return type(
                "Result",
                (),
                {"created": 0, "updated": 0, "failed": 0, "error_samples": [], "warning_samples": []},
            )()

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    exit_code = main.cmd_sync(
        argparse.Namespace(
            cursor_login_timeout_sec=99,
            cursor_login_browser="chromium",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
            lookback_days=45,
        )
    )
    assert exit_code == 0
    assert called["timeout"] == 99
    assert called["browser"] == "chromium"
    assert called["login_mode"] == "auto"
    assert called["lookback_days"] == 45


def test_cmd_collect_suppresses_cursor_probe_warning_when_cursor_rows_exist(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto", lookback_days=None: "cursor warning",
    )
    monkeypatch.setattr(
        main,
        "_build_aggregates",
        lambda args: (
            [
                AggregateRecord(
                    date_local="2026-03-08",
                    user_hash="u",
                    source_host_hash="s",
                    tool="cursor",
                    model="m",
                    input_tokens_sum=1,
                    cache_tokens_sum=0,
                    output_tokens_sum=1,
                    row_key="k",
                    updated_at="2026-03-08T00:00:00+00:00",
                )
            ],
            [],
            {},
        ),
    )
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
            cursor_login_mode="auto",
            ui="none",
        )
    )
    assert exit_code == 0
    assert not any("cursor warning" in line for line in printed)


def test_cmd_collect_keeps_cursor_probe_warning_when_no_cursor_rows(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto", lookback_days=None: "cursor warning",
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], [], {}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
            cursor_login_mode="auto",
            ui="none",
        )
    )
    assert exit_code == 0
    assert any("cursor warning" in line for line in printed)
