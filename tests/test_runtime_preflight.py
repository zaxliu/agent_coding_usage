from __future__ import annotations

from pathlib import Path

import llm_usage.runtime_preflight as runtime_preflight


def test_ensure_runtime_bootstrap_creates_env_and_reports(tmp_path: Path):
    env_path = tmp_path / ".env"
    reports_dir = tmp_path / "reports"

    result = runtime_preflight.ensure_runtime_bootstrap(
        env_path=env_path,
        reports_dir=reports_dir,
        bootstrap_text='ORG_USERNAME=""\n',
    )

    assert result.bootstrap_applied is True
    assert result.created_env is True
    assert result.created_reports is True
    assert env_path.exists()
    assert reports_dir.exists()
    assert env_path.read_text(encoding="utf-8") == 'ORG_USERNAME=""\n'


def test_validate_feishu_config_requires_default_target():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={},
        feishu_targets=[{"name": "finance", "app_token": "app-fin"}],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: default target is required" in result.errors


def test_validate_feishu_config_rejects_default_with_only_app_token():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={"FEISHU_APP_TOKEN": "app-default"},
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in result.errors


def test_validate_feishu_config_allows_named_target_to_inherit_default_auth():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_APP_ID": "cli_a",
            "FEISHU_APP_SECRET": "secret_a",
        },
        feishu_targets=[{"name": "finance", "app_token": "app-fin"}],
        mode="config_save",
    )

    assert result.ok is True
    assert result.errors == []
    assert "finance" in [target.name for target in result.resolved_feishu_targets]


def test_validate_feishu_config_rejects_named_target_without_app_token():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
        },
        feishu_targets=[{"name": "finance", "app_token": ""}],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[finance]: missing APP_TOKEN" in result.errors


def test_validate_basic_config_missing_org_username_non_interactive():
    result = runtime_preflight.validate_basic_config(
        basic={"ORG_USERNAME": "", "HASH_SALT": "some-salt"},
        is_interactive_tty=False,
    )
    assert result.ok is False
    assert any("ORG_USERNAME" in e for e in result.errors)


def test_validate_basic_config_missing_hash_salt():
    result = runtime_preflight.validate_basic_config(
        basic={"ORG_USERNAME": "alice", "HASH_SALT": ""},
        is_interactive_tty=False,
    )
    assert result.ok is False
    assert any("HASH_SALT" in e for e in result.errors)


def test_validate_basic_config_missing_both():
    result = runtime_preflight.validate_basic_config(
        basic={"ORG_USERNAME": "", "HASH_SALT": ""},
        is_interactive_tty=False,
    )
    assert result.ok is False
    assert len(result.errors) == 2


def test_validate_basic_config_skips_org_username_in_interactive_tty():
    result = runtime_preflight.validate_basic_config(
        basic={"ORG_USERNAME": "", "HASH_SALT": "some-salt"},
        is_interactive_tty=True,
    )
    assert result.ok is True
    assert result.errors == []


def test_validate_basic_config_all_present():
    result = runtime_preflight.validate_basic_config(
        basic={"ORG_USERNAME": "alice", "HASH_SALT": "some-salt"},
        is_interactive_tty=False,
    )
    assert result.ok is True
    assert result.errors == []


def test_validate_runtime_config_reports_table_id_as_warning():
    result = runtime_preflight.validate_runtime_config(
        basic={"ORG_USERNAME": "test", "HASH_SALT": "salt"},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
            "FEISHU_TABLE_ID": "",
        },
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is True
    assert result.errors == []
    assert "feishu[default]: TABLE_ID is empty; first table will be auto-selected" in result.warnings


def test_validate_runtime_config_rejects_partial_default_app_credentials():
    result = runtime_preflight.validate_runtime_config(
        basic={"ORG_USERNAME": "test", "HASH_SALT": "salt"},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_APP_ID": "cli_a",
            "FEISHU_APP_SECRET": "",
        },
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: APP_ID and APP_SECRET must be set together" in result.errors


def test_validate_runtime_config_rejects_named_target_with_partial_own_auth():
    result = runtime_preflight.validate_runtime_config(
        basic={"ORG_USERNAME": "test", "HASH_SALT": "salt"},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
        },
        feishu_targets=[
            {
                "name": "finance",
                "app_token": "app-fin",
                "app_id": "cli_fin",
                "app_secret": "",
            }
        ],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[finance]: APP_ID and APP_SECRET must be set together" in result.errors


def test_validate_runtime_config_allows_execution_with_explicit_named_target_without_default():
    result = runtime_preflight.validate_runtime_config(
        basic={"ORG_USERNAME": "test", "HASH_SALT": "salt"},
        feishu_default={},
        feishu_targets=[
            {
                "name": "team_a",
                "app_token": "app-team-a",
                "table_id": "tbl-team-a",
                "bot_token": "bot-team-a",
            }
        ],
        mode="execution",
        selected_feishu_targets=["team_a"],
        all_feishu_targets=False,
    )

    assert result.ok is True
    assert result.errors == []
    assert [target.name for target in result.resolved_feishu_targets] == ["team_a"]
