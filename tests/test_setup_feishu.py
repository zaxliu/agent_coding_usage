from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

import llm_usage.main as main
from llm_usage.interaction import (
    feishu_config_setup_target,
    run_feishu_setup_wizard,
)


def _env_map(path: Path) -> dict[str, str]:
    from llm_usage.env import load_env_document

    doc = load_env_document(path)
    return {line.key: line.value or "" for line in doc.lines if line.kind == "entry" and line.key}


# ---------------------------------------------------------------------------
# feishu_config_setup_target — non-interactive
# ---------------------------------------------------------------------------


class TestSetupTargetNonInteractive:
    def test_setup_default_target(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        out = StringIO()
        rc = feishu_config_setup_target(
            env_path, None, out, app_token="tok1", app_id="id1", app_secret="sec1"
        )
        assert rc == 0
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"
        assert m["FEISHU_APP_ID"] == "id1"
        assert m["FEISHU_APP_SECRET"] == "sec1"
        assert "configured default" in out.getvalue()

    def test_setup_default_target_with_optional_fields(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        out = StringIO()
        rc = feishu_config_setup_target(
            env_path, "default", out, app_token="tok1", table_id="tbl1", bot_token="bot1"
        )
        assert rc == 0
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"
        assert m["FEISHU_TABLE_ID"] == "tbl1"
        assert m["FEISHU_BOT_TOKEN"] == "bot1"

    def test_setup_named_target_creates_new(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        out = StringIO()
        rc = feishu_config_setup_target(
            env_path, "team_a", out, app_token="tok-a", app_id="id-a", app_secret="sec-a"
        )
        assert rc == 0
        m = _env_map(env_path)
        assert "team_a" in m.get("FEISHU_TARGETS", "")
        assert m["FEISHU_TEAM_A_APP_TOKEN"] == "tok-a"
        assert m["FEISHU_TEAM_A_APP_ID"] == "id-a"
        assert m["FEISHU_TEAM_A_APP_SECRET"] == "sec-a"

    def test_setup_named_target_overwrites_existing(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "FEISHU_TARGETS=team_a\nFEISHU_TEAM_A_APP_TOKEN=old\n", encoding="utf-8"
        )
        out = StringIO()
        rc = feishu_config_setup_target(
            env_path, "team_a", out, app_token="new-tok", app_id="new-id", app_secret="new-sec"
        )
        assert rc == 0
        m = _env_map(env_path)
        assert m["FEISHU_TEAM_A_APP_TOKEN"] == "new-tok"
        assert m["FEISHU_TEAM_A_APP_ID"] == "new-id"

    def test_setup_named_target_invalid_name(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        out = StringIO()
        rc = feishu_config_setup_target(env_path, "bad name!", out, app_token="tok")
        assert rc == 1
        assert "error" in out.getvalue()


# ---------------------------------------------------------------------------
# CLI integration via argparse
# ---------------------------------------------------------------------------


class TestSetupFeishuCLI:
    def test_cli_setup_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        parser = main.build_parser()
        args = parser.parse_args([
            "config", "--setup-feishu",
            "--app-token", "tok1", "--app-id", "id1", "--app-secret", "sec1",
        ])
        monkeypatch.setattr(main, "_env_path", lambda: env_path)
        assert main.cmd_config(args) == 0
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"

    def test_cli_setup_named(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        parser = main.build_parser()
        args = parser.parse_args([
            "config", "--setup-feishu", "--name", "ops",
            "--app-token", "tok-ops", "--app-id", "id-ops", "--app-secret", "sec-ops",
        ])
        monkeypatch.setattr(main, "_env_path", lambda: env_path)
        assert main.cmd_config(args) == 0
        m = _env_map(env_path)
        assert "ops" in m.get("FEISHU_TARGETS", "")
        assert m["FEISHU_OPS_APP_TOKEN"] == "tok-ops"

    def test_cli_setup_conflicts_with_other_shortcuts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        parser = main.build_parser()
        args = parser.parse_args([
            "config", "--setup-feishu", "--list-feishu-targets",
        ])
        monkeypatch.setattr(main, "_env_path", lambda: env_path)
        assert main.cmd_config(args) != 0


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


class TestSetupWizard:
    def test_wizard_single_default_target(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        stdin = StringIO("\n".join([
            "",         # name -> default
            "tok1",     # app_token
            "id1",      # app_id
            "sec1",     # app_secret
            "",         # table_id -> skip
            "n",        # add another? no
        ]) + "\n")
        out = StringIO()
        rc = run_feishu_setup_wizard(env_path, out, stdin=stdin)
        assert rc == 0
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"
        assert m["FEISHU_APP_ID"] == "id1"
        assert "1 target(s)" in out.getvalue()

    def test_wizard_two_targets(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        stdin = StringIO("\n".join([
            "",         # default
            "tok1",     # app_token
            "id1",      # app_id
            "sec1",     # app_secret
            "",         # table_id
            "y",        # add another
            "team_b",   # named target
            "tok-b",    # app_token
            "",         # app_id (inherit)
            "",         # app_secret (inherit)
            "tbl-b",    # table_id
            "n",        # done
        ]) + "\n")
        out = StringIO()
        rc = run_feishu_setup_wizard(env_path, out, stdin=stdin)
        assert rc == 0
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"
        assert m["FEISHU_TEAM_B_APP_TOKEN"] == "tok-b"
        assert m["FEISHU_TEAM_B_TABLE_ID"] == "tbl-b"
        assert "2 target(s)" in out.getvalue()

    def test_wizard_empty_app_token_retries(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        stdin = StringIO("\n".join([
            "",         # default
            "",         # empty app_token -> retry
            "",         # default again
            "tok1",     # app_token
            "id1",
            "sec1",
            "",
            "n",
        ]) + "\n")
        out = StringIO()
        rc = run_feishu_setup_wizard(env_path, out, stdin=stdin)
        assert rc == 0
        assert "APP_TOKEN is required" in out.getvalue()
        m = _env_map(env_path)
        assert m["FEISHU_APP_TOKEN"] == "tok1"

    def test_wizard_eof_exits_gracefully(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
        stdin = StringIO("")  # immediate EOF
        out = StringIO()
        rc = run_feishu_setup_wizard(env_path, out, stdin=stdin)
        assert rc == 0
