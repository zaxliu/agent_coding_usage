from __future__ import annotations

import argparse
import builtins
import pytest

import llm_usage.main as main
from llm_usage.models import AggregateRecord


def _row(*, row_key: str = "rk") -> AggregateRecord:
    return AggregateRecord(
        date_local="2026-03-31",
        user_hash="u",
        source_host_hash="s",
        tool="codex",
        model="m",
        input_tokens_sum=1,
        cache_tokens_sum=0,
        output_tokens_sum=1,
        row_key=row_key,
        updated_at="2026-03-31T12:00:00+08:00",
    )


def test_parser_doctor_accepts_feishu_and_target_flags():
    parser = main.build_parser()
    args = parser.parse_args(["doctor", "--feishu", "--feishu-target", "team_a", "--all-feishu-targets"])
    assert args.command == "doctor"
    assert args.feishu is True
    assert args.feishu_target == ["team_a"]
    assert args.all_feishu_targets is True


def test_parser_sync_accepts_feishu_target_flags():
    parser = main.build_parser()
    args = parser.parse_args(["sync", "--feishu-target", "beta", "--feishu-target", "alpha"])
    assert args.command == "sync"
    assert args.feishu_target == ["beta", "alpha"]
    assert args.all_feishu_targets is False


def test_parser_sync_accepts_all_feishu_targets():
    parser = main.build_parser()
    args = parser.parse_args(["sync", "--all-feishu-targets", "--dry-run"])
    assert args.all_feishu_targets is True
    assert args.dry_run is True


def test_parser_init_accepts_feishu_bitable_schema_and_dry_run():
    parser = main.build_parser()
    args = parser.parse_args(["init", "--feishu-bitable-schema", "--dry-run"])
    assert args.command == "init"
    assert args.feishu_bitable_schema is True
    assert args.dry_run is True


def test_resolve_feishu_sync_selection_default_only(monkeypatch):
    monkeypatch.setenv(
        "FEISHU_APP_TOKEN",
        "app",
    )
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")
    args = argparse.Namespace(feishu_target=[], all_feishu_targets=False)
    picked = main._resolve_feishu_sync_selection(args)
    assert len(picked) == 1
    assert picked[0].name == "default"


def test_cmd_sync_from_bundle_passes_target_selection_to_upload(monkeypatch):
    """sync --from-bundle should use target-aware upload with default-only selection."""
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: ([_row()], [], {"row_count": 1}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: None)
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    def _track(rows, *, dry_run, feishu_target, all_feishu_targets):  # noqa: ANN001
        calls.append((dry_run, tuple(feishu_target or ()), all_feishu_targets))
        return 0

    monkeypatch.setattr(main, "_sync_rows_to_feishu_targets", _track)

    rc = main.cmd_sync(
        argparse.Namespace(
            from_bundle="/tmp/b.zip",
            dry_run=False,
            lookback_days=None,
            ui="auto",
            cursor_login_timeout_sec=600,
            cursor_login_browser="default",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
            feishu_target=[],
            all_feishu_targets=False,
        )
    )
    assert rc == 0
    assert calls == [(False, (), False)]


def test_cmd_sync_all_feishu_targets_calls_upload_with_select_all(monkeypatch):
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(main, "read_offline_bundle", lambda path: ([_row()], [], {"row_count": 1}))
    monkeypatch.setattr(main, "print_terminal_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: None)
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    captured = {}

    def _track(rows, *, dry_run, feishu_target, all_feishu_targets):  # noqa: ANN001
        captured["all"] = all_feishu_targets
        return 0

    monkeypatch.setattr(main, "_sync_rows_to_feishu_targets", _track)

    main.cmd_sync(
        argparse.Namespace(
            from_bundle="/tmp/b.zip",
            dry_run=False,
            lookback_days=None,
            ui="auto",
            cursor_login_timeout_sec=600,
            cursor_login_browser="default",
            cursor_login_user_data_dir="",
            cursor_login_mode="auto",
            feishu_target=[],
            all_feishu_targets=True,
        )
    )
    assert captured["all"] is True


def test_sync_rows_to_feishu_targets_keeps_nonzero_if_any_target_fails(monkeypatch):
    targets = [
        main.FeishuTargetConfig(name="default", app_token="app"),
        main.FeishuTargetConfig(name="team_b", app_token="app-b"),
    ]
    seen: list[str] = []

    monkeypatch.setattr(main, "_resolve_feishu_sync_selection", lambda args: targets)

    def _sync_one(rows, target):  # noqa: ANN001, ANN201
        seen.append(target.name)
        return 2 if target.name == "default" else 0

    monkeypatch.setattr(main, "_sync_rows_to_single_feishu_target", _sync_one)

    rc = main._sync_rows_to_feishu_targets([_row()], dry_run=False, feishu_target=[], all_feishu_targets=True)

    assert rc == 2
    assert seen == ["default", "team_b"]


def test_sync_rows_to_feishu_targets_preserves_first_failure_code(monkeypatch):
    targets = [
        main.FeishuTargetConfig(name="default", app_token="app"),
        main.FeishuTargetConfig(name="team_b", app_token="app-b"),
    ]

    monkeypatch.setattr(main, "_resolve_feishu_sync_selection", lambda args: targets)

    def _sync_one(rows, target):  # noqa: ANN001, ANN201
        return 2 if target.name == "default" else 1

    monkeypatch.setattr(main, "_sync_rows_to_single_feishu_target", _sync_one)

    rc = main._sync_rows_to_feishu_targets([_row()], dry_run=False, feishu_target=[], all_feishu_targets=True)

    assert rc == 2


def test_doctor_feishu_missing_fields_returns_exit_0(monkeypatch, capsys):
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    def _ok_doctor(_args):  # noqa: ANN001, ANN201
        print("warn: missing field cache_tokens_sum")
        return 0

    monkeypatch.setattr(main, "run_feishu_doctor", _ok_doctor)

    rc = main.cmd_doctor(argparse.Namespace(feishu=True, feishu_target=[], all_feishu_targets=False, lookback_days=None))
    assert rc == 0
    assert "warn: missing field" in capsys.readouterr().out


def test_doctor_feishu_auth_failure_returns_nonzero(monkeypatch):
    monkeypatch.setattr(main, "_load_runtime_env", lambda: None)
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.delenv("FEISHU_BOT_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)

    def _fail(_args):  # noqa: ANN001, ANN201
        raise RuntimeError("feishu auth error")

    monkeypatch.setattr(main, "run_feishu_doctor", _fail)

    rc = main.cmd_doctor(argparse.Namespace(feishu=True, feishu_target=[], all_feishu_targets=False, lookback_days=None))
    assert rc != 0


def test_run_feishu_doctor_wraps_auth_errors_with_target_name(monkeypatch):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", app_id="id", app_secret="secret"),
        ],
    )
    monkeypatch.setattr(main, "fetch_tenant_access_token", lambda app_id, app_secret: (_ for _ in ()).throw(RuntimeError("bad auth")))

    with pytest.raises(RuntimeError, match="team_a"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))


def test_run_feishu_doctor_does_not_treat_link_share_mode_as_write_permission_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])
    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            return "rec_123"

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    rc = main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))

    assert rc == 0
    output = capsys.readouterr().out
    assert "feishu[team_a]: OK" in output
    assert "expected 'tenant_editable'" not in output
    assert "组织内获得链接的人可编辑" not in output
    assert "无法检查文档分享权限" not in output


def test_run_feishu_doctor_reports_write_probe_cleanup_failure(monkeypatch):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])
    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            raise RuntimeError("feishu doctor cleanup failed: rec_123")

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))


def test_run_feishu_doctor_reports_write_probe_create_failure(monkeypatch):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])
    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            raise RuntimeError("create forbidden")

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    with pytest.raises(RuntimeError, match="target team_a: create forbidden"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))


def test_run_feishu_doctor_prints_warn_summary_when_schema_has_warnings(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: ["missing cache_tokens_sum"])

    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            return "rec_123"

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    rc = main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))

    assert rc == 0
    output = capsys.readouterr().out
    assert "warn: missing cache_tokens_sum" in output
    assert "feishu[team_a]: WARN" in output


def test_cmd_doctor_rejects_target_flags_without_feishu(capsys):
    rc = main.cmd_doctor(
        argparse.Namespace(feishu=False, feishu_target=["team_a"], all_feishu_targets=False, lookback_days=None)
    )
    assert rc == 2
    assert "--feishu-target" in capsys.readouterr().out


def test_init_feishu_schema_dry_run_plans_schema_changes(monkeypatch):
    calls: list[tuple[bool, list[str]]] = []

    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(main, "_ensure_env_file_exists", lambda: main.Path("/tmp/.env"))
    monkeypatch.setattr(main, "_reports_dir", lambda: main.Path("/tmp/reports"))
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    def _plan(*, dry_run, targets):  # noqa: ANN001, ANN201
        calls.append((dry_run, [target.name for target in targets]))
        return None

    monkeypatch.setattr(main, "ensure_feishu_schema_for_targets", _plan)

    rc = main.cmd_init(
        argparse.Namespace(feishu_bitable_schema=True, dry_run=True, feishu_target=[], all_feishu_targets=False)
    )
    assert rc == 0
    assert calls == [(True, ["default"])]


def test_init_feishu_schema_calls_ensure_once_per_dry_run_false(monkeypatch):
    calls = {"n": 0}

    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(main, "_ensure_env_file_exists", lambda: main.Path("/tmp/.env"))
    monkeypatch.setattr(main, "_reports_dir", lambda: main.Path("/tmp/reports"))
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_BOT_TOKEN", "bot")

    def _ensure(*, dry_run, targets):  # noqa: ANN001, ANN201
        calls["n"] += 1
        assert dry_run is False
        return None

    monkeypatch.setattr(main, "ensure_feishu_schema_for_targets", _ensure)

    rc = main.cmd_init(
        argparse.Namespace(feishu_bitable_schema=True, dry_run=False, feishu_target=[], all_feishu_targets=False)
    )
    assert rc == 0
    assert calls["n"] == 1


def test_init_rejects_feishu_target_flags_without_schema_mode(monkeypatch, capsys):
    monkeypatch.setattr(main, "_repo_root", lambda: main.Path("/tmp"))
    monkeypatch.setattr(main, "_ensure_env_file_exists", lambda: main.Path("/tmp/.env"))
    monkeypatch.setattr(main, "_reports_dir", lambda: main.Path("/tmp/reports"))

    rc = main.cmd_init(
        argparse.Namespace(feishu_bitable_schema=False, dry_run=False, feishu_target=["team_a"], all_feishu_targets=False)
    )

    assert rc == 2
    assert "--feishu-target" in capsys.readouterr().out
