from __future__ import annotations

from pathlib import Path

from llm_usage import paths


def test_resolve_runtime_paths_uses_linux_xdg_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-home"))
    monkeypatch.delenv("LLM_USAGE_ENV_FILE", raising=False)
    monkeypatch.delenv("LLM_USAGE_DATA_DIR", raising=False)
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(tmp_path / "repo")

    assert resolved.env_path == tmp_path / "config-home" / "llm-usage" / ".env"
    assert resolved.runtime_state_path == tmp_path / "data-home" / "llm-usage" / "runtime_state.json"
    assert resolved.reports_dir == tmp_path / "data-home" / "llm-usage" / "reports"


def test_resolve_runtime_paths_uses_macos_native_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LLM_USAGE_ENV_FILE", raising=False)
    monkeypatch.delenv("LLM_USAGE_DATA_DIR", raising=False)
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(tmp_path / "repo")

    expected = tmp_path / "Library" / "Application Support" / "llm-usage"
    assert resolved.env_path == expected / ".env"
    assert resolved.runtime_state_path == expected / "runtime_state.json"


def test_resolve_runtime_paths_uses_windows_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("LLM_USAGE_ENV_FILE", raising=False)
    monkeypatch.delenv("LLM_USAGE_DATA_DIR", raising=False)
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(tmp_path / "repo")

    expected = tmp_path / "Roaming" / "llm-usage"
    assert resolved.env_path == expected / ".env"
    assert resolved.runtime_state_path == expected / "runtime_state.json"


def test_runtime_path_overrides_take_precedence(monkeypatch, tmp_path):
    env_file = tmp_path / "custom" / "settings.env"
    data_dir = tmp_path / "custom-data"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_file))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(data_dir))
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(tmp_path / "repo")

    assert resolved.env_path == env_file
    assert resolved.runtime_state_path == data_dir / "runtime_state.json"
    assert resolved.reports_dir == data_dir / "reports"


def test_resolve_runtime_paths_migrates_legacy_env_when_confirmed(monkeypatch, tmp_path):
    legacy_root = tmp_path / "repo"
    legacy_root.mkdir()
    legacy_env = legacy_root / ".env"
    legacy_env.write_text("ORG_USERNAME=alice\n", encoding="utf-8")
    target_env = tmp_path / "config" / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(target_env))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(paths.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(legacy_root)

    assert resolved.env_path == target_env
    assert target_env.read_text(encoding="utf-8") == "ORG_USERNAME=alice\n"


def test_resolve_runtime_paths_uses_legacy_env_for_noninteractive_run(monkeypatch, tmp_path, capsys):
    legacy_root = tmp_path / "repo"
    (legacy_root / "reports").mkdir(parents=True)
    legacy_env = legacy_root / ".env"
    legacy_env.write_text("ORG_USERNAME=alice\n", encoding="utf-8")
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(tmp_path / "config" / ".env"))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(paths.sys.stdout, "isatty", lambda: False)
    paths.reset_runtime_paths_cache()

    resolved = paths.resolve_runtime_paths(legacy_root)

    assert resolved.env_path == legacy_env
    assert "Using legacy file for this run" in capsys.readouterr().out


def test_read_bootstrap_env_text_contains_required_keys():
    text = paths.read_bootstrap_env_text()
    assert "ORG_USERNAME=" in text
    assert "HASH_SALT=" in text
