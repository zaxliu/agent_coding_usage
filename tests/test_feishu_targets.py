import pytest

from llm_usage.feishu_schema import REQUIRED_FEISHU_FIELDS, field_names
from llm_usage.feishu_targets import (
    FeishuTargetConfig,
    normalize_feishu_target_name,
    resolve_feishu_targets_from_env,
    select_feishu_targets,
)
from llm_usage.privacy import UPLOAD_FIELDS


def test_resolve_feishu_targets_keeps_legacy_default_only():
    env = {
        "FEISHU_APP_TOKEN": "app-default",
        "FEISHU_TABLE_ID": "tbl-default",
        "FEISHU_APP_ID": "cli-default",
        "FEISHU_APP_SECRET": "sec-default",
    }

    targets = resolve_feishu_targets_from_env(env)

    assert [item.name for item in targets] == ["default"]
    assert targets[0].app_token == "app-default"
    assert targets[0].table_id == "tbl-default"


def test_resolve_feishu_targets_supports_named_targets_with_auth_inheritance():
    env = {
        "FEISHU_APP_TOKEN": "app-default",
        "FEISHU_APP_ID": "cli-default",
        "FEISHU_APP_SECRET": "sec-default",
        "FEISHU_TARGETS": "team_b,finance",
        "FEISHU_TEAM_B_APP_TOKEN": "app-team-b",
        "FEISHU_TEAM_B_TABLE_ID": "tbl-team-b",
        "FEISHU_FINANCE_APP_TOKEN": "app-finance",
    }

    targets = resolve_feishu_targets_from_env(env)

    assert [item.name for item in targets] == ["default", "team_b", "finance"]
    assert targets[1].app_id == "cli-default"
    assert targets[2].app_secret == "sec-default"
    assert targets[1].inherited_auth is True
    assert targets[2].inherited_auth is True


def test_select_feishu_targets_requires_explicit_multi_target_opt_in():
    targets = [
        FeishuTargetConfig(name="default", app_token="app-default"),
        FeishuTargetConfig(name="team_b", app_token="app-team-b"),
    ]

    selected = select_feishu_targets(
        targets, selected_names=[], select_all=False, default_only=True
    )

    assert [item.name for item in selected] == ["default"]


def test_feishu_schema_stays_in_sync_with_exported_field_names():
    assert field_names(REQUIRED_FEISHU_FIELDS) == [
        "date_local",
        "user_hash",
        "source_host_hash",
        "tool",
        "model",
        "input_tokens_sum",
        "cache_tokens_sum",
        "output_tokens_sum",
        "row_key",
        "updated_at",
    ]


def test_legacy_env_full_default_target():
    env = {
        "FEISHU_APP_TOKEN": "app-1",
        "FEISHU_TABLE_ID": "tbl-1",
        "FEISHU_APP_ID": "id-1",
        "FEISHU_APP_SECRET": "sec-1",
        "FEISHU_BOT_TOKEN": "bot-1",
    }
    targets = resolve_feishu_targets_from_env(env)
    assert targets[0] == FeishuTargetConfig(
        name="default",
        app_token="app-1",
        table_id="tbl-1",
        app_id="id-1",
        app_secret="sec-1",
        bot_token="bot-1",
        inherited_auth=False,
    )


def test_legacy_any_nonempty_key_creates_default():
    env = {"FEISHU_APP_ID": "only-id"}
    targets = resolve_feishu_targets_from_env(env)
    assert [t.name for t in targets] == ["default"]
    assert targets[0].app_id == "only-id"
    assert targets[0].app_token == ""


def test_named_target_inherits_top_level_auth():
    env = {
        "FEISHU_APP_TOKEN": "legacy-app",
        "FEISHU_BOT_TOKEN": "legacy-bot",
        "FEISHU_APP_ID": "legacy-id",
        "FEISHU_APP_SECRET": "legacy-sec",
        "FEISHU_TARGETS": "team_b",
        "FEISHU_TEAM_B_APP_TOKEN": "team-app",
        "FEISHU_TEAM_B_TABLE_ID": "team-tbl",
    }
    targets = resolve_feishu_targets_from_env(env)
    assert [t.name for t in targets] == ["default", "team_b"]
    team = targets[1]
    assert team.app_token == "team-app"
    assert team.table_id == "team-tbl"
    assert team.app_id == "legacy-id"
    assert team.app_secret == "legacy-sec"
    assert team.bot_token == "legacy-bot"


def test_named_target_overrides_inherited_auth():
    env = {
        "FEISHU_APP_ID": "legacy-id",
        "FEISHU_APP_SECRET": "legacy-sec",
        "FEISHU_BOT_TOKEN": "legacy-bot",
        "FEISHU_TARGETS": "x",
        "FEISHU_X_APP_TOKEN": "x-app",
        "FEISHU_X_APP_ID": "x-id",
    }
    targets = resolve_feishu_targets_from_env(env)
    x = next(t for t in targets if t.name == "x")
    assert x.name == "x"
    assert x.app_id == "x-id"
    assert x.app_secret == "legacy-sec"
    assert x.bot_token == "legacy-bot"
    assert x.inherited_auth is True


def test_select_with_no_names_defaults_to_default_only():
    env = {
        "FEISHU_APP_TOKEN": "a",
        "FEISHU_BOT_TOKEN": "b",
        "FEISHU_TARGETS": "t2",
        "FEISHU_T2_APP_TOKEN": "t2-app",
    }
    targets = resolve_feishu_targets_from_env(env)
    assert [t.name for t in select_feishu_targets(targets, selected_names=None)] == ["default"]
    assert [t.name for t in select_feishu_targets(targets, selected_names=[])] == ["default"]


def test_select_explicit_names():
    env = {
        "FEISHU_APP_TOKEN": "a",
        "FEISHU_BOT_TOKEN": "b",
        "FEISHU_TARGETS": "alpha, beta",
        "FEISHU_ALPHA_APP_TOKEN": "aa",
        "FEISHU_BETA_APP_TOKEN": "bb",
    }
    targets = resolve_feishu_targets_from_env(env)
    picked = select_feishu_targets(targets, selected_names=["beta", "alpha"])
    assert [c.name for c in picked] == ["beta", "alpha"]


def test_select_all_resolved_orders_default_then_named():
    env = {
        "FEISHU_APP_TOKEN": "d",
        "FEISHU_BOT_TOKEN": "b",
        "FEISHU_TARGETS": "z, y",
        "FEISHU_Z_APP_TOKEN": "z",
        "FEISHU_Y_APP_TOKEN": "y",
    }
    targets = resolve_feishu_targets_from_env(env)
    picked = select_feishu_targets(targets, select_all=True)
    assert [c.name for c in picked] == ["default", "z", "y"]


def test_feishu_targets_rejects_reserved_default_name():
    env = {"FEISHU_TARGETS": "default", "FEISHU_DEFAULT_APP_TOKEN": "x"}
    with pytest.raises(RuntimeError, match="default"):
        resolve_feishu_targets_from_env(env)


def test_feishu_targets_rejects_duplicate_names():
    env = {"FEISHU_TARGETS": "a, A", "FEISHU_A_APP_TOKEN": "t"}
    with pytest.raises(RuntimeError, match="duplicate"):
        resolve_feishu_targets_from_env(env)


def test_upload_fields_match_schema_field_names():
    schema_names = {spec.name for spec in REQUIRED_FEISHU_FIELDS}
    assert UPLOAD_FIELDS == schema_names


def test_select_all_and_names_mutually_exclusive():
    targets = resolve_feishu_targets_from_env(
        {
            "FEISHU_APP_TOKEN": "a",
            "FEISHU_BOT_TOKEN": "b",
        }
    )
    with pytest.raises(ValueError):
        select_feishu_targets(targets, selected_names=["default"], select_all=True)


def test_normalize_feishu_target_name_valid():
    assert normalize_feishu_target_name("Team_B") == "team_b"


def test_normalize_feishu_target_name_rejects_default():
    with pytest.raises(RuntimeError, match="reserved"):
        normalize_feishu_target_name("default")
