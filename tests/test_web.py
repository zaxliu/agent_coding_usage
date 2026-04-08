from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_usage.collectors.remote_file import SshAuthenticationError

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
            "basic": {},
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
                "REMOTE_SERVER_A_USE_SSHPASS=1",
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
    assert queued["status"] == "needs_input"
    assert queued["input_request"] == {
        "kind": "ssh_password",
        "remote_alias": "SERVER_A",
        "message": "Provide the SSH password for SERVER_A. It will be cached in memory for this session only.",
        "cache_scope": "session",
    }

    job_id = queued["id"]
    captured_response: dict[str, object] = {}
    fake_handler = object.__new__(web._Handler)
    fake_handler.path = f"/api/jobs/{job_id}/input"
    fake_handler.server = SimpleNamespace(service=service)
    fake_handler.headers = {"Content-Length": str(len(json.dumps({"value": "top-secret"}).encode("utf-8")))}
    fake_handler.rfile = SimpleNamespace(read=lambda _n: json.dumps({"value": "top-secret"}).encode("utf-8"))

    def fake_read_json(self):
        return {"value": "top-secret"}

    def fake_write_json(self, status, payload):
        captured_response["status"] = status
        captured_response["payload"] = payload

    fake_handler._read_json = fake_read_json.__get__(fake_handler, type(fake_handler))
    fake_handler._write_json = fake_write_json.__get__(fake_handler, type(fake_handler))
    web._Handler.do_POST(fake_handler)
    assert captured_response["status"].name == "ACCEPTED"
    resumed = captured_response["payload"]
    assert resumed["status"] in {"running", "queued"}

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
    assert captured["runtime_passwords"] == {"SERVER_A": "top-secret"}
    assert "SSHPASS=top-secret" not in env_path.read_text(encoding="utf-8")

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
    assert captured["runtime_passwords"] == {"SERVER_A": "top-secret"}


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
                "REMOTE_SERVER_A_USE_SSHPASS=1",
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
    assert queued["status"] == "needs_input"
    assert queued["input_request"] == {
        "kind": "ssh_password",
        "remote_alias": "SERVER_A",
        "message": "Provide the SSH password for SERVER_A. It will be cached in memory for this session only.",
        "cache_scope": "session",
    }

    resumed = service.jobs.submit_input(queued["id"], "top-secret")
    assert resumed["status"] in {"running", "queued"}

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
    assert captured["runtime_passwords"] == {"SERVER_A": "top-secret"}


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
    assert current["input_request"]["kind"] == "use_sshpass"

    finished = service.jobs.submit_input(job_id, "n")
    assert finished["status"] in {"queued", "running", "succeeded"}

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
            "use_sshpass": False,
        }
    }


def test_web_collect_fallback_on_ssh_auth_failure(tmp_path: Path, monkeypatch):
    """When SSH key auth fails (use_sshpass=False), the job should pause for password input."""
    # Ensure no leftover USE_SSHPASS from prior tests (dotenv pollution)
    monkeypatch.delenv("REMOTE_SERVER_A_USE_SSHPASS", raising=False)
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
                "REMOTE_SERVER_A_USE_SSHPASS=0",
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
    assert result["status"] in {"queued", "running"}

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
