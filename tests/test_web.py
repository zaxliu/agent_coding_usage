from __future__ import annotations

from pathlib import Path

import llm_usage.main as main
import llm_usage.web as web


def test_web_help_describes_local_console(capsys):
    parser = main.build_parser()

    try:
        parser.parse_args(["web", "--help"])
    except SystemExit:
        pass

    help_text = capsys.readouterr().out
    assert "local web console" in help_text.lower()
    assert "--host HOST" in help_text
    assert "--port PORT" in help_text
    assert "--no-open" in help_text


def test_web_server_serves_runtime_config_and_results(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
                "LOOKBACK_DAYS=30",
                "FEISHU_APP_TOKEN=app-token",
                "FEISHU_TARGETS=team_b",
                "FEISHU_TEAM_B_APP_TOKEN=team-token",
                "REMOTE_HOSTS=server_a",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "",
            ]
        ),
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "usage_report.csv").write_text(
        "\n".join(
            [
                "date_local,user_hash,source_host_hash,tool,model,input_tokens_sum,cache_tokens_sum,output_tokens_sum,row_key,updated_at",
                "2026-04-06,user-a,host-a,codex,gpt-5,10,2,3,row-1,2026-04-06T10:00:00+08:00",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    runtime_payload = web.WebService().runtime_payload()
    assert runtime_payload["backend"] == "python"
    assert runtime_payload["env_path"] == str(env_path)

    config_payload = web.load_config_payload()
    assert config_payload["basic"]["ORG_USERNAME"] == "san.zhang"
    assert config_payload["feishu_targets"][0]["name"] == "team_b"
    assert config_payload["remotes"][0]["alias"] == "SERVER_A"

    results_payload = web.load_latest_results()
    assert results_payload["rows"][0]["tool"] == "codex"

    validate_payload = web.validate_config_payload({"feishu_targets": [{"name": "bad-name"}]})
    assert validate_payload["ok"] is False
    assert validate_payload["errors"]

    save_payload = web.save_config_payload(
        {
            "basic": {
                "ORG_USERNAME": "san.zhang",
                "HASH_SALT": "test-salt",
                "TIMEZONE": "Asia/Shanghai",
                "LOOKBACK_DAYS": "14",
            },
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token"},
            "feishu_targets": [{"name": "team_b", "app_token": "team-token"}],
            "remotes": [],
            "raw_env": [],
        }
    )
    assert save_payload["ok"] is True
    assert "LOOKBACK_DAYS=14" in env_path.read_text(encoding="utf-8")
