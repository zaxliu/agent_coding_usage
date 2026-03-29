from __future__ import annotations

from pathlib import Path
import zipfile

import llm_usage.main as main
from llm_usage.bundle import EXTERNAL_PROFILE, INTERNAL_PROFILE, build_bundles


def _write_repo_fixture(repo_root: Path) -> None:
    (repo_root / "src/llm_usage").mkdir(parents=True)
    (repo_root / "tests").mkdir()
    (repo_root / ".git").mkdir()
    (repo_root / "reports").mkdir()
    (repo_root / "dist").mkdir()
    (repo_root / ".pytest_cache").mkdir()
    (repo_root / "src/llm_usage_sync.egg-info").mkdir(parents=True)

    (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
    (repo_root / ".env.example").write_text("ORG_USERNAME=\nHASH_SALT=\n", encoding="utf-8")
    (repo_root / "src/llm_usage/main.py").write_text("print('ok')\n", encoding="utf-8")
    (repo_root / "tests/test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo_root / ".git/config").write_text("[core]\n", encoding="utf-8")
    (repo_root / "reports/data.csv").write_text("x\n", encoding="utf-8")
    (repo_root / "dist/old.zip").write_text("x\n", encoding="utf-8")
    (repo_root / ".pytest_cache/README").write_text("cache\n", encoding="utf-8")
    (repo_root / "src/llm_usage_sync.egg-info/PKG-INFO").write_text("meta\n", encoding="utf-8")
    (repo_root / ".env").write_text(
        "\n".join(
            [
                "# Identity",
                "ORG_USERNAME=alice",
                "HASH_SALT=team-salt",
                "TIMEZONE=Asia/Shanghai",
                "LOOKBACK_DAYS=30",
                "",
                "# Feishu",
                "FEISHU_APP_TOKEN=app-token",
                "FEISHU_TABLE_ID=table-id",
                "FEISHU_APP_ID=app-id",
                "FEISHU_APP_SECRET=app-secret",
                "FEISHU_BOT_TOKEN=bot-token",
                "",
                "# Paths",
                "CLAUDE_LOG_PATHS=/tmp/claude",
                "CODEX_LOG_PATHS=/tmp/codex",
                "COPILOT_CLI_LOG_PATHS=/tmp/copilot-cli",
                "COPILOT_VSCODE_SESSION_PATHS=/tmp/copilot-vscode",
                "CURSOR_LOG_PATHS=/tmp/cursor",
                "",
                "# Cursor",
                "CURSOR_WEB_SESSION_TOKEN=session-token",
                "CURSOR_WEB_WORKOS_ID=workos-id",
                "CURSOR_DASHBOARD_BASE_URL=https://custom.example",
                "CURSOR_DASHBOARD_TEAM_ID=99",
                "CURSOR_DASHBOARD_PAGE_SIZE=999",
                "CURSOR_DASHBOARD_TIMEOUT_SEC=77",
                "",
                "# Remote",
                "REMOTE_HOSTS=SERVER_A",
                "REMOTE_SERVER_A_SSH_HOST=host-a",
                "REMOTE_SERVER_A_SSH_USER=alice",
                "REMOTE_SERVER_A_SSH_PORT=2200",
                "REMOTE_SERVER_A_LABEL=prod-a",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _read_bundle_env(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        env_names = [name for name in zf.namelist() if name.endswith("/.env")]
        assert len(env_names) == 1
        return zf.read(env_names[0]).decode("utf-8")


def _read_bundle_bootstrap(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        env_names = [name for name in zf.namelist() if name.endswith("/src/llm_usage/resources/bootstrap.env")]
        assert len(env_names) == 1
        return zf.read(env_names[0]).decode("utf-8")


def test_build_bundles_sanitizes_internal_and_external_env(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_fixture(repo_root)

    artifacts = build_bundles(
        repo_root=repo_root,
        output_dir=tmp_path / "out",
        timestamp="20260315_150000",
    )

    artifact_map = {artifact.profile: artifact.zip_path for artifact in artifacts}
    internal_env = _read_bundle_env(artifact_map[INTERNAL_PROFILE])
    external_env = _read_bundle_env(artifact_map[EXTERNAL_PROFILE])
    internal_bootstrap = _read_bundle_bootstrap(artifact_map[INTERNAL_PROFILE])
    external_bootstrap = _read_bundle_bootstrap(artifact_map[EXTERNAL_PROFILE])

    assert "ORG_USERNAME=\n" in internal_env
    assert "HASH_SALT=team-salt\n" in internal_env
    assert "FEISHU_APP_TOKEN=app-token\n" in internal_env
    assert "FEISHU_APP_SECRET=app-secret\n" in internal_env
    assert "CURSOR_WEB_SESSION_TOKEN=\n" in internal_env
    assert "CURSOR_WEB_WORKOS_ID=\n" in internal_env
    assert "CLAUDE_LOG_PATHS=\n" in internal_env
    assert "COPILOT_CLI_LOG_PATHS=\n" in internal_env
    assert "COPILOT_VSCODE_SESSION_PATHS=\n" in internal_env
    assert "CURSOR_DASHBOARD_BASE_URL=https://cursor.com\n" in internal_env
    assert "CURSOR_DASHBOARD_PAGE_SIZE=300\n" in internal_env
    assert "REMOTE_HOSTS=\n" in internal_env
    assert "REMOTE_SERVER_A_SSH_HOST=\n" in internal_env
    assert internal_bootstrap == internal_env

    assert "ORG_USERNAME=\n" in external_env
    assert "HASH_SALT=\n" in external_env
    assert "FEISHU_APP_TOKEN=\n" in external_env
    assert "FEISHU_APP_SECRET=\n" in external_env
    assert "FEISHU_BOT_TOKEN=\n" in external_env
    assert "CURSOR_WEB_SESSION_TOKEN=\n" in external_env
    assert "CURSOR_DASHBOARD_TEAM_ID=0\n" in external_env
    assert "CURSOR_DASHBOARD_TIMEOUT_SEC=15\n" in external_env
    assert "REMOTE_SERVER_A_LABEL=\n" in external_env
    assert external_bootstrap == external_env


def test_build_bundles_excludes_runtime_and_git_artifacts(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_fixture(repo_root)

    artifacts = build_bundles(
        repo_root=repo_root,
        output_dir=tmp_path / "out",
        timestamp="20260315_150001",
    )

    for artifact in artifacts:
        with zipfile.ZipFile(artifact.zip_path) as zf:
            names = set(zf.namelist())
            assert any(name.endswith("/README.md") for name in names)
            assert any(name.endswith("/src/llm_usage/main.py") for name in names)
            assert not any("/.git/" in name for name in names)
            assert not any("/reports/" in name for name in names)
            assert not any("/dist/" in name for name in names)
            assert not any("/.pytest_cache/" in name for name in names)
            assert not any("/src/llm_usage_sync.egg-info/" in name for name in names)


def test_cmd_bundle_writes_two_timestamped_archives(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_repo_fixture(repo_root)
    monkeypatch.chdir(repo_root)

    parser = main.build_parser()
    args = parser.parse_args(["bundle", "--output-dir", str(tmp_path / "bundles")])

    exit_code = main.cmd_bundle(args)

    assert exit_code == 0
    generated = sorted(path.name for path in (tmp_path / "bundles").glob("*.zip"))
    assert len(generated) == 2
    assert generated[0].startswith("agent_coding_usage_")
    assert generated[0].endswith(".zip")
