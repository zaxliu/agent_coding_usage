from __future__ import annotations

from argparse import Namespace
import builtins
import sys

import pytest

import llm_usage.main as main
from llm_usage.models import AggregateRecord
from llm_usage.offline_bundle import OfflineBundleError


def _set_basic_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("ORG_USERNAME", "alice")
    monkeypatch.setenv("HASH_SALT", "team-salt")


def _row(*, tool: str = "codex", row_key: str = "row-key") -> AggregateRecord:
    return AggregateRecord(
        date_local="2026-03-31",
        user_hash="user-hash",
        source_host_hash="source-a",
        tool=tool,
        model="gpt-5",
        input_tokens_sum=10,
        cache_tokens_sum=2,
        output_tokens_sum=3,
        row_key=row_key,
        updated_at="2026-03-31T12:00:00+08:00",
    )


def test_build_parser_parses_export_bundle_and_sync_from_bundle():
    parser = main.build_parser()

    export_args = parser.parse_args(["export-bundle", "--output", "/tmp/offline.zip", "--no-csv"])
    sync_args = parser.parse_args(["sync", "--from-bundle", "/tmp/offline.zip", "--dry-run"])

    assert export_args.command == "export-bundle"
    assert export_args.output == "/tmp/offline.zip"
    assert export_args.include_csv is False
    assert sync_args.command == "sync"
    assert sync_args.from_bundle == "/tmp/offline.zip"
    assert sync_args.dry_run is True


def test_cmd_export_bundle_writes_bundle_and_prints_path(monkeypatch, tmp_path):
    calls: dict[str, object] = {}
    output_lines: list[str] = []

    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([_row()], ["warn-a"], {}))
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(
        main,
        "_maybe_capture_cursor_token",
        lambda timeout_sec, browser, user_data_dir, login_mode="auto", lookback_days=None: None,
    )
    monkeypatch.setenv("TIMEZONE", "UTC")

    def _fake_write(rows, output_path, *, warnings, timezone_name, lookback_days, tool_version, include_csv):  # noqa: ANN001, ANN201
        calls["rows"] = rows
        calls["output_path"] = output_path
        calls["warnings"] = warnings
        calls["timezone_name"] = timezone_name
        calls["lookback_days"] = lookback_days
        calls["tool_version"] = tool_version
        calls["include_csv"] = include_csv
        output_path.write_text("bundle", encoding="utf-8")
        return output_path

    monkeypatch.setattr(main, "write_offline_bundle", _fake_write)
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: output_lines.append(" ".join(str(v) for v in args)))

    exit_code = main.cmd_export_bundle(
        Namespace(output=str(tmp_path / "offline.zip"), lookback_days=14, include_csv=False, ui="none")
    )

    assert exit_code == 0
    assert calls["rows"] == [_row()]
    assert calls["warnings"] == ["warn-a"]
    assert calls["timezone_name"] == "UTC"
    assert calls["lookback_days"] == 14
    assert calls["include_csv"] is False
    assert any("bundle:" in line for line in output_lines)


def test_cmd_sync_from_bundle_rejects_online_collection_flags(monkeypatch):
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)

    with pytest.raises(RuntimeError, match="--from-bundle"):
        main.cmd_sync(
            Namespace(
                from_bundle="/tmp/offline.zip",
                dry_run=False,
                lookback_days=7,
                ui="none",
                cursor_login_timeout_sec=600,
                cursor_login_browser="default",
                cursor_login_user_data_dir="",
                cursor_login_mode="auto",
            )
        )


def test_cmd_sync_from_bundle_dry_run_without_identity_uses_empty_host_labels(monkeypatch):
    captured: dict[str, object] = {}

    def _capture_print(rows, **kwargs):  # noqa: ANN001, ANN201
        captured["rows"] = rows
        captured["kwargs"] = kwargs

    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "_sync_execution_preflight", lambda **kwargs: 0)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: ([_row()], [], {"row_count": 1}))
    monkeypatch.setattr(main, "print_terminal_report", _capture_print)
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: None)
    monkeypatch.delenv("ORG_USERNAME", raising=False)
    monkeypatch.delenv("HASH_SALT", raising=False)

    exit_code = main.cmd_sync(
        Namespace(
            from_bundle="/tmp/offline.zip",
            dry_run=True,
            lookback_days=None,
            ui="auto",
            cursor_login_timeout_sec=600,
            cursor_login_browser="default",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
        )
    )

    assert exit_code == 0
    assert len(captured["rows"]) == 1
    assert captured["kwargs"].get("host_labels") == {}


def test_cmd_sync_from_bundle_dry_run_skips_feishu_credentials(monkeypatch):
    output_lines: list[str] = []
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: ([_row(tool="cursor")], ["warn-a"], {"row_count": 1}))
    monkeypatch.setattr(
        main,
        "print_terminal_report",
        lambda rows, **kwargs: output_lines.append(f"rows:{len(rows)}"),
    )
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: output_lines.append(" ".join(str(v) for v in args)))
    _set_basic_runtime_env(monkeypatch)
    feishu_calls: list[str] = []

    def _track_required_env(name: str) -> str:
        if name.startswith("FEISHU_"):
            feishu_calls.append(name)
        return main.os.environ[name]

    monkeypatch.setattr(main, "_required_env", _track_required_env)

    exit_code = main.cmd_sync(
        Namespace(
            from_bundle="/tmp/offline.zip",
            dry_run=True,
            lookback_days=None,
            ui="auto",
            cursor_login_timeout_sec=600,
            cursor_login_browser="default",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
        )
    )

    assert exit_code == 0
    assert "rows:1" in output_lines
    assert any("warn: warn-a" == line for line in output_lines)
    assert feishu_calls == []


def test_cmd_sync_from_bundle_upserts_original_rows(monkeypatch):
    rows = [_row(row_key="row-a"), _row(tool="cursor", row_key="row-b")]
    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: (rows, [], {"row_count": 2}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    _set_basic_runtime_env(monkeypatch)
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    class _Client:
        def __init__(self, app_token, table_id, bot_token):  # noqa: ANN001
            captured["client_init"] = (app_token, table_id, bot_token)

        def upsert(self, incoming_rows):  # noqa: ANN001, ANN201
            captured["rows"] = incoming_rows
            return type(
                "Result",
                (),
                {"created": 1, "updated": 1, "failed": 0, "error_samples": [], "warning_samples": []},
            )()

    monkeypatch.setattr("llm_usage.sinks.feishu_bitable.FeishuBitableClient", _Client)

    exit_code = main.cmd_sync(
        Namespace(
            from_bundle="/tmp/offline.zip",
            dry_run=False,
            lookback_days=None,
            ui="auto",
            cursor_login_timeout_sec=600,
            cursor_login_browser="default",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
        )
    )

    assert exit_code == 0
    assert captured["client_init"] == ("app", "tbl", "bot")
    assert captured["rows"] == rows


def test_main_returns_exit_1_for_invalid_bundle(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["llm-usage", "sync", "--from-bundle", "/tmp/bad.zip"])
    _set_basic_runtime_env(monkeypatch)
    monkeypatch.setattr(main, "_sync_execution_preflight", lambda **kwargs: 0)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: (_ for _ in ()).throw(OfflineBundleError("bad bundle")))

    exit_code = main.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error: bad bundle" in captured.out
