from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_usage.collectors.remote_file import SshAuthenticationError, SshTarget

import llm_usage.main as main
import llm_usage.paths as paths
import llm_usage.web as web


@pytest.fixture(autouse=True)
def _isolate_runtime_paths(tmp_path: Path, monkeypatch):
    paths.reset_runtime_paths_cache()
    monkeypatch.chdir(tmp_path)
    yield
    paths.reset_runtime_paths_cache()


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
                "REMOTE_SERVER_A_CLINE_VSCODE_SESSION_PATHS=/remote/cline/api_conversation_history.json",
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
    assert config_payload["remotes"][0]["cline_vscode_session_paths"] == ["/remote/cline/api_conversation_history.json"]
    assert config_payload["bootstrap_applied"] is False
    assert config_payload["auto_fixes"] == []

    results_payload = web.load_latest_results()
    assert results_payload["summary"]["totals"]["input_tokens_sum"] == 10
    assert results_payload["summary"]["active_days"] == 1
    assert results_payload["summary"]["top_tool"]["name"] == "codex"
    assert results_payload["summary"]["top_model"]["name"] == "gpt-5"
    assert results_payload["timeseries"][0]["date_local"] == "2026-04-06"
    assert results_payload["breakdowns"]["tools"][0]["name"] == "codex"
    assert results_payload["table_rows"][0]["date_local"] == "2026-04-06"

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
    assert save_payload["ok"] is False
    assert save_payload["saved"] is False
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in save_payload["errors"]
    assert "LOOKBACK_DAYS=14" not in env_path.read_text(encoding="utf-8")


def test_load_config_payload_bootstraps_missing_runtime_paths(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.load_config_payload()

    assert payload["bootstrap_applied"] is True
    assert payload["auto_fixes"]
    assert env_path.exists()
    assert (tmp_path / "reports").exists()


def test_save_config_payload_rejects_incomplete_default_feishu_auth(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.save_config_payload(
        {
            "basic": {},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-default"},
            "feishu_targets": [],
            "remotes": [],
            "raw_env": [],
        }
    )

    assert payload["ok"] is False
    assert payload["saved"] is False
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in payload["errors"]
    text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_APP_TOKEN=app-default" not in text
    assert "LOOKBACK_DAYS=30" in text


def test_save_config_payload_allows_named_target_to_inherit_default_auth(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "test", "HASH_SALT": "salt"},
            "cursor": {},
            "feishu_default": {
                "FEISHU_APP_TOKEN": "app-default",
                "FEISHU_APP_ID": "cli_a",
                "FEISHU_APP_SECRET": "secret_a",
            },
            "feishu_targets": [{"name": "finance", "app_token": "app-fin"}],
            "remotes": [],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_APP_TOKEN=app-default" in text
    assert "FEISHU_TARGETS=finance" in text


def test_save_config_payload_rejects_remote_when_ssh_validation_fails(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        web,
        "probe_remote_ssh",
        lambda config, ssh_password=None: (False, "Connection timed out"),
    )

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is False
    assert payload["saved"] is False
    assert "remote SERVER_A: SSH check failed: Connection timed out" in payload["errors"]
    assert "REMOTE_HOSTS=SERVER_A" not in env_path.read_text(encoding="utf-8")


def test_save_config_payload_uses_web_ssh_password_for_remote_validation(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        captured["ssh_password"] = ssh_password
        return True, "ok"

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                    "ssh_password": "top-secret",
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    assert captured == {"ssh_password": "top-secret"}
    text = env_path.read_text(encoding="utf-8")
    assert "USE_SSHPASS" not in text
    assert "top-secret" not in text


def test_web_remote_modal_exposes_jump_host_fields():
    html = (Path(web.__file__).resolve().parent / "web_static" / "index.html").read_text(encoding="utf-8")
    js = (Path(web.__file__).resolve().parent / "web_static" / "app.js").read_text(encoding="utf-8")

    assert 'id="remote-edit-ssh-jump-host"' in html
    assert 'id="remote-edit-ssh-jump-port"' in html
    assert 'id="remote-edit-cline-vscode-paths"' in html
    assert "remote-edit-ssh-jump-host" in js
    assert "ssh_jump_host:" in js
    assert "ssh_jump_port:" in js
    assert "remote-edit-cline-vscode-paths" in js
    assert "cline_vscode_session_paths:" in js


def test_save_config_payload_persists_web_remote_jump_host(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        captured["jump_host"] = config.ssh_jump_host
        captured["jump_port"] = config.ssh_jump_port
        return True, "ok"

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                    "ssh_jump_host": "jump-a",
                    "ssh_jump_port": 2201,
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert captured == {"jump_host": "jump-a", "jump_port": 2201}
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_SERVER_A_SSH_JUMP_HOST=jump-a" in text
    assert "REMOTE_SERVER_A_SSH_JUMP_PORT=2201" in text


def test_save_config_payload_requests_password_when_new_web_remote_key_auth_fails(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    monkeypatch.setattr(
        web,
        "probe_remote_ssh",
        lambda config, ssh_password=None: (False, "Permission denied (publickey)."),
    )

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is False
    assert payload["saved"] is False
    assert payload["errors"] == []
    assert payload["input_request"] == {
        "kind": "ssh_password",
        "remote_alias": "SERVER_A",
        "message": "SSH key 认证失败（SERVER_A）。请提供 SSH 密码重试，密码仅用于本次配置校验。",
        "cache_scope": "config_save",
    }
    assert "REMOTE_HOSTS=SERVER_A" not in env_path.read_text(encoding="utf-8")


def test_save_config_payload_retries_web_remote_validation_with_prompted_password(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        captured["ssh_password"] = ssh_password
        return True, "ok"

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                    "ssh_password": "top-secret",
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    assert captured == {"ssh_password": "top-secret"}
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=SERVER_A" in text
    assert "USE_SSHPASS" not in text
    assert "top-secret" not in text


def test_web_config_save_handles_ssh_password_input_request():
    html = (Path(web.__file__).resolve().parent / "web_static" / "index.html").read_text(encoding="utf-8")
    js = (Path(web.__file__).resolve().parent / "web_static" / "app.js").read_text(encoding="utf-8")

    assert 'id="credential-cancel"' in html
    assert 'type="button" value="cancel" class="button-subtle" id="credential-cancel"' in html
    assert 'type="submit" value="submit" class="button-primary" id="credential-submit"' in html
    assert "pendingCredentialMode" in js
    assert "openConfigCredentialPrompt" in js
    assert "input_request" in js
    assert "remote.ssh_password = value" in js
    assert "refs.credentialCancel.addEventListener(\"click\", cancelCredentialInput)" in js
    assert 'state.pendingCredentialMode === "config_save"' in js
    assert "const result = await saveCurrentConfig();" in js
    assert "if (result === true)" in js
    assert "lastConfigSaveError" in js
    assert "refs.credentialCopy.textContent = state.lastConfigSaveError" in js
    assert "if (!refs.credentialModal.open)" in js
    assert 'refs.credentialModal.close();\n      showFlash("SSH 密码已填写，正在重试保存。");' not in js


def test_save_config_payload_does_not_validate_existing_web_remote(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=san.zhang\n"
        "HASH_SALT=test-salt\n"
        "FEISHU_APP_TOKEN=app-token\n"
        "FEISHU_BOT_TOKEN=bot-token\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        raise AssertionError(f"existing remote should not be validated on web save: {config.alias}")

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    assert "REMOTE_HOSTS=SERVER_A" in env_path.read_text(encoding="utf-8")


def test_save_config_payload_validates_existing_web_remote_when_connection_changes(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=san.zhang\n"
        "HASH_SALT=test-salt\n"
        "FEISHU_APP_TOKEN=app-token\n"
        "FEISHU_BOT_TOKEN=bot-token\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        captured["alias"] = config.alias
        captured["jump_host"] = config.ssh_jump_host
        captured["jump_port"] = config.ssh_jump_port
        return True, "ok"

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [
                {
                    "alias": "SERVER_A",
                    "ssh_host": "host-a",
                    "ssh_user": "alice",
                    "ssh_port": 22,
                    "source_label": "alice@host-a",
                    "ssh_jump_host": "jump-a",
                    "ssh_jump_port": 2201,
                }
            ],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    assert captured == {"alias": "SERVER_A", "jump_host": "jump-a", "jump_port": 2201}


def test_save_config_payload_does_not_validate_deleted_web_remote(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=san.zhang\n"
        "HASH_SALT=test-salt\n"
        "FEISHU_APP_TOKEN=app-token\n"
        "FEISHU_BOT_TOKEN=bot-token\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    def fake_probe(config, ssh_password=None):  # noqa: ANN001
        raise AssertionError(f"deleted remote should not be validated on web save: {config.alias}")

    monkeypatch.setattr(web, "probe_remote_ssh", fake_probe)

    payload = web.save_config_payload(
        {
            "basic": {"ORG_USERNAME": "san.zhang", "HASH_SALT": "test-salt", "TIMEZONE": "Asia/Shanghai"},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-token", "FEISHU_BOT_TOKEN": "bot-token"},
            "feishu_targets": [],
            "remotes": [],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS" not in text
    assert "REMOTE_SERVER_A_SSH_HOST" not in text


def test_web_results_payload_is_dashboard_shaped(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
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
                "2026-04-07,user-a,host-a,claude,claude-3.7,5,1,4,row-2,2026-04-07T11:00:00+08:00",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.load_latest_results()
    assert payload["summary"] == {
        "totals": {
            "rows": 2,
            "input_tokens_sum": 15,
            "cache_tokens_sum": 3,
            "output_tokens_sum": 7,
            "total_tokens": 25,
        },
        "active_days": 2,
        "top_tool": {"name": "codex", "total_tokens": 15},
        "top_model": {"name": "gpt-5", "total_tokens": 15},
        "generated_at": payload["summary"]["generated_at"],
    }
    assert [item["date_local"] for item in payload["timeseries"]] == ["2026-04-06", "2026-04-07"]
    assert payload["timeseries"][0]["total_tokens"] == 15
    assert payload["breakdowns"]["tools"][0]["name"] == "codex"
    assert payload["breakdowns"]["models"][0]["name"] == "gpt-5"
    assert payload["table_rows"] == [
        {
            "date_local": "2026-04-06",
            "source_host_hash": "host-a",
            "tool": "codex",
            "model": "gpt-5",
            "input_tokens_sum": 10,
            "cache_tokens_sum": 2,
            "output_tokens_sum": 3,
            "total_tokens": 15,
            "row_count": 1,
        },
        {
            "date_local": "2026-04-07",
            "source_host_hash": "host-a",
            "tool": "claude",
            "model": "claude-3.7",
            "input_tokens_sum": 5,
            "cache_tokens_sum": 1,
            "output_tokens_sum": 4,
            "total_tokens": 10,
            "row_count": 1,
        },
    ]


def test_web_static_handler_serves_favicon_assets_with_image_types_and_404s_unknown_static_paths():
    def serve(path: str) -> tuple[int, dict[str, str], bytes]:
        captured: dict[str, object] = {"status": None, "headers": {}, "body": b""}
        handler = object.__new__(web._Handler)
        handler.wfile = SimpleNamespace(write=lambda data: captured.__setitem__("body", captured["body"] + data))
        handler.send_response = lambda status: captured.__setitem__("status", int(status))
        handler.send_header = lambda key, value: captured["headers"].__setitem__(key, value)
        handler.end_headers = lambda: None

        web._Handler._serve_static(handler, path)
        return captured["status"], captured["headers"], captured["body"]

    status, headers, body = serve("/favicon.svg")
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    assert b"<svg" in body

    status, headers, body = serve("/favicon.ico")
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    assert b"<svg" in body

    status, headers, body = serve("/favicon-does-not-exist.ico")
    assert status == 404
    assert body == b"not found"


def test_web_collect_pauses_for_ssh_password_and_resumes_from_memory_only(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
                "REMOTE_HOSTS=server_a",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    captured: dict[str, object] = {}

    def fake_build(payload, runtime_passwords=None):
        captured["runtime_passwords"] = dict(runtime_passwords or {})
        return [], [], {}

    monkeypatch.setattr(web, "_build_aggregates_for_web", fake_build)

    service = web.WebService()
    queued = service.start_collect({})
    job_id = queued["id"]
    assert queued["status"] in {"queued", "running"}

    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)
    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["result"]["row_count"] == 0
    assert current["result"]["warnings"] == []
    assert current["result"]["host_labels"] == {}
    assert current["result"]["csv_path"].endswith("usage_report.csv")
    assert captured["runtime_passwords"] == {}

    second = service.start_collect({})
    assert second["status"] == "running"
    for _ in range(100):
        current = service.jobs.get_job(second["id"])
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)
    current = service.jobs.get_job(second["id"])
    assert current is not None
    assert current["status"] == "succeeded"
    assert captured["runtime_passwords"] == {}


def test_web_sync_preview_pauses_for_ssh_password_and_resumes_from_memory_only(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
                "REMOTE_HOSTS=server_a",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    captured: dict[str, object] = {}

    def fake_build(payload, runtime_passwords=None):
        captured["runtime_passwords"] = dict(runtime_passwords or {})
        return [], [], {}

    monkeypatch.setattr(web, "_build_aggregates_for_web", fake_build)

    service = web.WebService()
    queued = service.start_sync_preview({})
    assert queued["status"] in {"queued", "running"}

    for _ in range(100):
        current = service.jobs.get_job(queued["id"])
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(queued["id"])
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["result"]["row_count"] == 0
    assert current["result"]["warnings"] == []
    assert isinstance(current["result"]["targets"], list)
    assert captured["runtime_passwords"] == {}


def test_web_sync_fails_preflight_before_collect(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    monkeypatch.setattr(web, "_sync_execution_preflight", lambda **kwargs: 1)

    def _fail_if_called(payload, runtime_passwords=None):  # noqa: ANN001, ANN201
        raise AssertionError("_build_aggregates_for_web should not be called when sync preflight fails")

    monkeypatch.setattr(web, "_build_aggregates_for_web", _fail_if_called)

    service = web.WebService()
    started = service.start_sync({"confirm_sync": True})

    for _ in range(100):
        current = service.jobs.get_job(started["id"])
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(started["id"])
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["result"]["exit_code"] == 1


def test_web_collect_fails_preflight_before_job(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ORG_USERNAME", raising=False)
    monkeypatch.delenv("HASH_SALT", raising=False)

    def _fail_if_called(payload, runtime_passwords=None):  # noqa: ANN001, ANN201
        raise AssertionError("_build_aggregates_for_web should not be called when collect preflight fails")

    monkeypatch.setattr(web, "_build_aggregates_for_web", _fail_if_called)

    service = web.WebService()
    started = service.start_collect({})

    for _ in range(100):
        current = service.jobs.get_job(started["id"])
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(started["id"])
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["result"]["exit_code"] == 1
    assert current["result"]["errors"]


def test_web_remote_setup_returns_structured_input_request_sequence():
    service = web.WebService()

    queued = service.start_doctor({"remote_setup": True})

    assert queued["type"] == "remote_setup"
    assert queued["status"] == "needs_input"
    assert queued["input_request"] == {
        "kind": "ssh_host",
        "message": "SSH 主机：",
        "field": "value",
        "remote_alias": "",
        "secret": False,
        "choices": None,
    }

    invalid = service.jobs.submit_input(queued["id"], "")
    assert invalid["status"] == "needs_input"
    assert invalid["input_request"]["kind"] == "ssh_host"

    resumed = service.jobs.submit_input(queued["id"], "host-a")
    assert resumed["status"] in {"queued", "running", "needs_input"}

    job_id = queued["id"]
    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] == "needs_input":
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "needs_input"
    assert current["input_request"]["kind"] == "ssh_user"

    resumed = service.jobs.submit_input(job_id, "bob")
    assert resumed["status"] in {"queued", "running", "needs_input"}

    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] == "needs_input":
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "needs_input"
    assert current["input_request"]["kind"] == "ssh_port"

    resumed = service.jobs.submit_input(job_id, "2200")
    assert resumed["status"] in {"queued", "running", "needs_input"}

    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] == "needs_input":
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "needs_input"
    assert current["input_request"]["kind"] == "ssh_jump_host"

    resumed = service.jobs.submit_input(job_id, "")
    assert resumed["status"] in {"queued", "running", "succeeded"}

    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "succeeded"
    assert current["result"] == {
        "remote_setup": {
            "alias": "BOB_HOST_A",
            "ssh_host": "host-a",
            "ssh_user": "bob",
            "ssh_port": 2200,
            "ssh_jump_host": "",
            "ssh_jump_port": 2222,
        }
    }


def test_web_collect_fallback_on_ssh_auth_failure(tmp_path: Path, monkeypatch):
    """When SSH key auth fails, the job should pause for password input."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
                "REMOTE_HOSTS=server_a",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    call_count = {"n": 0}
    captured: dict[str, object] = {}

    def fake_build(payload, runtime_passwords=None):
        call_count["n"] += 1
        captured["runtime_passwords"] = dict(runtime_passwords or {})
        if call_count["n"] == 1:
            raise SshAuthenticationError("server_a", "Permission denied (publickey).")
        return [], [], {}

    monkeypatch.setattr(web, "_build_aggregates_for_web", fake_build)

    service = web.WebService()
    started = service.start_collect({})
    job_id = started["id"]

    # Wait for the job to transition to needs_input after the auth failure
    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] in {"needs_input", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "needs_input"
    assert current["input_request"]["kind"] == "ssh_password"
    assert current["input_request"]["remote_alias"] == "SERVER_A"

    # Submit password
    result = service.jobs.submit_input(job_id, "my-password")
    assert result["status"] in {"queued", "running", "succeeded"}

    # Wait for completion
    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "succeeded"
    assert captured["runtime_passwords"] == {"SERVER_A": "my-password"}


def test_web_collect_propagates_remote_auth_failure_to_frontend_prompt(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "ORG_USERNAME=san.zhang",
                "HASH_SALT=test-salt",
                "TIMEZONE=Asia/Shanghai",
                "REMOTE_HOSTS=server_a",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(web, "_collectors", lambda local_hash: [])

    class AuthFailCollector(web.RemoteFileCollector):
        def collect(self, start, end):  # noqa: ANN001, ANN201
            raise SshAuthenticationError("server_a", "Permission denied (publickey).")

    def fake_build_remote_collectors(configs, username, salt, runtime_passwords=None):  # noqa: ANN001, ANN201
        return [
            AuthFailCollector(
                "remote",
                target=SshTarget(host="host-a", user="alice", port=22),
                source_name="server_a",
                source_host_hash="hash",
                jobs=[],
            )
        ]

    monkeypatch.setattr(web, "build_remote_collectors", fake_build_remote_collectors)
    monkeypatch.setattr("getpass.getpass", lambda prompt: (_ for _ in ()).throw(EOFError))

    service = web.WebService()
    started = service.start_collect({})
    job_id = started["id"]

    for _ in range(100):
        current = service.jobs.get_job(job_id)
        if current and current["status"] in {"needs_input", "failed", "succeeded"}:
            break
        time.sleep(0.01)

    current = service.jobs.get_job(job_id)
    assert current is not None
    assert current["status"] == "needs_input"
    assert current["input_request"]["kind"] == "ssh_password"
    assert current["input_request"]["remote_alias"] == "SERVER_A"
