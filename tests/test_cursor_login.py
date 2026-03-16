import argparse
import builtins

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

    def _fake_capture(timeout_sec, browser, user_data_dir):  # noqa: ANN001, ANN201
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

    def _fake_capture(timeout_sec, browser, user_data_dir):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 61
        assert browser == "default"
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

    def _fake_capture(timeout_sec, browser, user_data_dir):  # noqa: ANN001, ANN201
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

    def _fake_capture(timeout_sec, browser, user_data_dir):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 66
        assert browser == "default"
        assert user_data_dir == "/tmp/profile"
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

    def _fake_capture(timeout_sec, browser, user_data_dir):  # noqa: ANN001, ANN201
        called["capture"] += 1
        assert timeout_sec == 77
        assert browser == "chromium"
        assert user_data_dir == ""
        return "browser-token"

    monkeypatch.setattr(main, "_capture_and_save_cursor_token", _fake_capture)
    main._maybe_capture_cursor_token(timeout_sec=77, browser="chromium", user_data_dir="")
    assert called["capture"] == 1


def test_capture_and_save_cursor_token(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr(
        main,
        "fetch_cursor_session_token_via_browser",
        lambda timeout_sec, browser, user_data_dir: "token-from-browser",
    )

    token = main._capture_and_save_cursor_token(
        timeout_sec=60,
        browser="chrome",
        user_data_dir="",
    )
    assert token == "token-from-browser"
    text = env_path.read_text(encoding="utf-8")
    assert "CURSOR_WEB_SESSION_TOKEN=token-from-browser" in text


def test_clear_saved_cursor_token(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("CURSOR_WEB_SESSION_TOKEN=abc\n", encoding="utf-8")
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "abc")

    main._clear_saved_cursor_token()

    text = env_path.read_text(encoding="utf-8")
    assert "CURSOR_WEB_SESSION_TOKEN=\n" in text
    assert "CURSOR_WEB_SESSION_TOKEN" not in main.os.environ


def test_cmd_collect_triggers_maybe_capture(monkeypatch):
    called = {"timeout": None, "browser": None, "user_data_dir": None}
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir: (
            called.__setitem__("timeout", timeout_sec),
            called.__setitem__("browser", browser),
            called.__setitem__("user_data_dir", user_data_dir),
            None,
        ),
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], []))
    monkeypatch.setattr(main, "print_terminal_report", lambda rows: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
        )
    )
    assert exit_code == 0
    assert called["timeout"] == 88
    assert called["browser"] == "default"
    assert called["user_data_dir"] == "/tmp/p1"


def test_cmd_sync_triggers_maybe_capture(monkeypatch):
    called = {"timeout": None, "browser": None, "user_data_dir": None}
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir: (
            called.__setitem__("timeout", timeout_sec),
            called.__setitem__("browser", browser),
            called.__setitem__("user_data_dir", user_data_dir),
            None,
        ),
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], []))
    monkeypatch.setattr(main, "print_terminal_report", lambda rows: None)
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
        )
    )
    assert exit_code == 0
    assert called["timeout"] == 99
    assert called["browser"] == "chromium"


def test_cmd_collect_suppresses_cursor_probe_warning_when_cursor_rows_exist(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir: "cursor warning",
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
        ),
    )
    monkeypatch.setattr(main, "print_terminal_report", lambda rows: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
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
        lambda timeout_sec, browser, user_data_dir: "cursor warning",
    )
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], []))
    monkeypatch.setattr(main, "print_terminal_report", lambda rows: None)
    monkeypatch.setattr(main, "write_csv_report", lambda rows, path: path / "usage_report.csv")
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    exit_code = main.cmd_collect(
        argparse.Namespace(
            cursor_login_timeout_sec=88,
            cursor_login_browser="default",
            cursor_login_user_data_dir="/tmp/p1",
            ui="none",
        )
    )
    assert exit_code == 0
    assert any("cursor warning" in line for line in printed)
