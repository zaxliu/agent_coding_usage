from __future__ import annotations

import pytest

import llm_usage.main as main


def test_top_level_help_shows_examples_and_commands(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "Examples:" in help_text
    assert "llm-usage collect --ui auto" in help_text
    assert "llm-usage sync --ui cli" in help_text
    assert "llm-usage whoami" in help_text
    assert "collect" in help_text
    assert "sync" in help_text
    assert "whoami" in help_text
    assert "config" in help_text
    assert "export-bundle" in help_text
    assert "web" in help_text


def test_collect_help_describes_terminal_grouping_and_csv_behavior(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--help"])

    help_text = capsys.readouterr().out
    assert "date + host + tool + model" in help_text.lower()
    assert "reports/usage_report.csv" in help_text
    assert "original aggregated rows" in help_text.lower()
    assert "--ui {auto,tui,cli,none}" in help_text
    assert "--lookback-days LOOKBACK_DAYS" in help_text
    assert "none disables remotes" in help_text.lower()


def test_sync_help_describes_terminal_grouping_and_feishu_behavior(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--help"])

    help_text = capsys.readouterr().out
    assert "date + host + tool + model" in help_text.lower()
    assert "upsert" in help_text.lower()
    assert "feishu" in help_text.lower()
    assert "original aggregated rows" in help_text.lower()
    assert "--lookback-days LOOKBACK_DAYS" in help_text
    assert "none disables remotes" in help_text.lower()
    assert "--from-bundle FROM_BUNDLE" in help_text
    assert "--feishu-target" in help_text
    assert "--all-feishu-targets" in help_text


def test_init_help_describes_bootstrap_behavior(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["init", "--help"])

    help_text = capsys.readouterr().out
    assert "active runtime .env" in help_text.lower()
    assert "reports directory" in help_text.lower()
    assert "30" in help_text
    assert "--feishu-bitable-schema" in help_text
    assert "--dry-run" in help_text


def test_doctor_help_describes_validation_behavior(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["doctor", "--help"])

    help_text = capsys.readouterr().out
    assert "validate identity settings" in help_text.lower()
    assert "probe local collectors" in help_text.lower()
    assert "remote collectors" in help_text.lower()
    assert "--lookback-days LOOKBACK_DAYS" in help_text
    assert "--feishu" in help_text
    assert "--feishu-target" in help_text
    assert "--all-feishu-targets" in help_text


def test_whoami_help_describes_hash_output(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["whoami", "--help"])

    help_text = capsys.readouterr().out
    assert "user_hash" in help_text
    assert "source_host_hash" in help_text
    assert "configured remotes" in help_text.lower()


def test_parser_rejects_removed_bundle_command(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["bundle"])

    err_text = capsys.readouterr().err
    assert "invalid choice" in err_text.lower()
    assert "bundle" in err_text


def test_export_bundle_help_describes_offline_handoff(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["export-bundle", "--help"])

    help_text = capsys.readouterr().out
    assert "offline bundle" in help_text.lower()
    assert "--output OUTPUT" in help_text
    assert "--no-csv" in help_text


def test_import_config_help_describes_examples_and_force_flag(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["import-config", "--help"])

    help_text = capsys.readouterr().out
    assert "one-time migration helper" in help_text.lower()
    assert "llm-usage import-config --from /path/to/legacy/repo" in help_text
    assert "--force" in help_text


def test_config_help_describes_menu_based_runtime_env_editing(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["config", "--help"])

    help_text = capsys.readouterr().out
    assert "interactive menu editor" in help_text.lower()
    assert "active runtime .env" in help_text.lower()
    assert "preferred" in help_text.lower()
    assert "--list-feishu-targets" in help_text
    assert "--show-feishu-target" in help_text
    assert "--add-feishu-target" in help_text
    assert "--delete-feishu-target" in help_text
    assert "--set-feishu-target" in help_text
