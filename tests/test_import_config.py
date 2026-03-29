from __future__ import annotations

from argparse import Namespace
import shlex
from pathlib import Path

import pytest

import llm_usage.main as main
from llm_usage import paths
from llm_usage.paths import RuntimePaths, reset_runtime_paths_cache


def _runtime_paths(tmp_path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    return RuntimePaths(
        env_path=config_dir / ".env",
        config_dir=config_dir,
        data_dir=data_dir,
        reports_dir=data_dir / "reports",
        runtime_state_path=data_dir / "runtime_state.json",
    )


def _write_legacy_config(repo_root, env_text, state_text):
    (repo_root / "reports").mkdir(parents=True, exist_ok=True)
    (repo_root / ".env").write_text(env_text, encoding="utf-8")
    (repo_root / "reports" / "runtime_state.json").write_text(state_text, encoding="utf-8")


class _ResolveRuntimePathsRecorder:
    def __init__(self, runtime_paths):
        self.runtime_paths = runtime_paths
        self.calls = []

    def __call__(self):
        self.calls.append(None)
        return self.runtime_paths


def test_build_parser_includes_import_config_command():
    parser = main.build_parser()

    args = parser.parse_args(["import-config", "--from", "/legacy/repo"])

    assert args.command == "import-config"
    assert args.source_root == "/legacy/repo"
    assert args.dry_run is False
    assert args.force is False


def test_import_config_help_shows_migration_flags(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["import-config", "--help"])

    help_text = capsys.readouterr().out
    assert "One-time migration helper" in help_text
    assert "--from SOURCE_ROOT" in help_text
    assert "--dry-run" in help_text
    assert "--force" in help_text


def test_top_level_help_shows_import_config_command(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "import-config" in help_text


def test_resolve_file_path_prompt_quotes_migration_command(monkeypatch, tmp_path):
    legacy_root = tmp_path / "repo with space" / "odd'chars"
    legacy_root.mkdir(parents=True)
    legacy_env = legacy_root / ".env"
    legacy_env.write_text("ORG_USERNAME=alice\n", encoding="utf-8")
    preferred = tmp_path / "config" / ".env"
    prompts = []

    monkeypatch.setattr(paths.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(paths.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "n")

    resolved = paths._resolve_file_path(label=".env", preferred=preferred, legacy=legacy_env)

    assert resolved == legacy_env
    assert len(prompts) == 1
    expected_command = f"llm-usage import-config --from {shlex.quote(str(legacy_root))}"
    assert "按 `y` 会把这个旧文件复制到" in prompts[0]
    assert expected_command in prompts[0]


def test_resolve_file_path_prompt_quotes_migration_command_on_windows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths.sys, "platform", "win32")

    legacy_root = Path(r"C:\repo with space\odd'chars")
    legacy_root.mkdir(parents=True)
    legacy_env = legacy_root / ".env"
    legacy_env.write_text("ORG_USERNAME=alice\n", encoding="utf-8")
    preferred = tmp_path / "config" / ".env"
    prompts = []

    monkeypatch.setattr(paths.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(paths.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "n")

    resolved = paths._resolve_file_path(label=".env", preferred=preferred, legacy=legacy_env)

    assert resolved == legacy_env
    assert len(prompts) == 1
    assert f'llm-usage import-config --from "{legacy_root}"' in prompts[0]


def test_cmd_import_config_copies_legacy_config_and_state(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_legacy_config(
        repo_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n",
        state_text='{"selected_remote_aliases":["alpha"]}\n',
    )
    runtime_paths = _runtime_paths(tmp_path)
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=False, source_root=repo_root))

    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n"
    assert runtime_paths.runtime_state_path.read_text(encoding="utf-8") == '{"selected_remote_aliases":["alpha"]}\n'


def test_cmd_import_config_dry_run_does_not_write_targets(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_legacy_config(
        repo_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n",
        state_text='{"selected_remote_aliases":["alpha"]}\n',
    )
    runtime_paths = _runtime_paths(tmp_path)
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=True, force=False, source_root=repo_root))

    assert exit_code == 0
    assert resolver.calls == [None]
    assert not runtime_paths.env_path.exists()
    assert not runtime_paths.runtime_state_path.exists()


def test_cmd_import_config_force_overwrites_existing_targets(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_legacy_config(
        repo_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=fresh-salt\n",
        state_text='{"selected_remote_aliases":["alpha","beta"]}\n',
    )
    runtime_paths = _runtime_paths(tmp_path)
    runtime_paths.env_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_paths.data_dir.mkdir(parents=True, exist_ok=True)
    runtime_paths.env_path.write_text("ORG_USERNAME=bob\nHASH_SALT=stale\n", encoding="utf-8")
    runtime_paths.runtime_state_path.write_text('{"selected_remote_aliases":["old"]}\n', encoding="utf-8")
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=True, source_root=repo_root))

    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=fresh-salt\n"
    assert runtime_paths.runtime_state_path.read_text(encoding="utf-8") == '{"selected_remote_aliases":["alpha","beta"]}\n'


def test_cmd_import_config_force_same_file_does_not_crash(monkeypatch, tmp_path):
    source_root = tmp_path / "legacy-repo"
    source_root.mkdir()
    _write_legacy_config(
        source_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n",
        state_text='{"selected_remote_aliases":["alpha"]}\n',
    )
    runtime_paths = RuntimePaths(
        env_path=source_root / ".env",
        config_dir=source_root,
        data_dir=source_root / "reports",
        reports_dir=source_root / "reports",
        runtime_state_path=source_root / "reports" / "runtime_state.json",
    )
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=True, source_root=source_root))

    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n"
    assert runtime_paths.runtime_state_path.read_text(encoding="utf-8") == '{"selected_remote_aliases":["alpha"]}\n'


def test_cmd_import_config_partial_import_succeeds_when_only_env_exists(monkeypatch, tmp_path, capsys):
    source_root = tmp_path / "legacy-repo"
    source_root.mkdir()
    (source_root / ".env").write_text("ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n", encoding="utf-8")
    runtime_paths = _runtime_paths(tmp_path)
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=False, source_root=source_root))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n"
    assert not runtime_paths.runtime_state_path.exists()
    assert "missing: runtime state source not found" in captured.out


def test_cmd_import_config_non_interactive_conflicts_skip_without_force(monkeypatch, tmp_path, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_legacy_config(
        repo_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=fresh-salt\n",
        state_text='{"selected_remote_aliases":["alpha","beta"]}\n',
    )
    runtime_paths = _runtime_paths(tmp_path)
    runtime_paths.env_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_paths.data_dir.mkdir(parents=True, exist_ok=True)
    runtime_paths.env_path.write_text("ORG_USERNAME=bob\nHASH_SALT=stale\n", encoding="utf-8")
    runtime_paths.runtime_state_path.write_text('{"selected_remote_aliases":["old"]}\n', encoding="utf-8")
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=False, source_root=repo_root))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=bob\nHASH_SALT=stale\n"
    assert runtime_paths.runtime_state_path.read_text(encoding="utf-8") == '{"selected_remote_aliases":["old"]}\n'
    assert "skip" in captured.out.lower()


def test_cmd_import_config_explicit_source_root_wins_over_cwd(monkeypatch, tmp_path):
    explicit_source_root = tmp_path / "explicit-source"
    explicit_source_root.mkdir()
    _write_legacy_config(
        explicit_source_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=explicit\n",
        state_text='{"selected_remote_aliases":["explicit"]}\n',
    )

    cwd_source_root = tmp_path / "cwd-source"
    cwd_source_root.mkdir()
    _write_legacy_config(
        cwd_source_root,
        env_text="ORG_USERNAME=bob\nHASH_SALT=cwd\n",
        state_text='{"selected_remote_aliases":["cwd"]}\n',
    )

    runtime_paths = _runtime_paths(tmp_path)
    resolver = _ResolveRuntimePathsRecorder(runtime_paths)
    monkeypatch.setattr(main, "resolve_active_runtime_paths", resolver)
    monkeypatch.chdir(cwd_source_root)

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=False, source_root=explicit_source_root))

    assert exit_code == 0
    assert resolver.calls == [None]
    assert runtime_paths.env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=explicit\n"
    assert runtime_paths.runtime_state_path.read_text(encoding="utf-8") == '{"selected_remote_aliases":["explicit"]}\n'


def test_cmd_import_config_uses_active_runtime_paths_not_legacy_fallback(monkeypatch, tmp_path):
    source_root = tmp_path / "legacy-repo"
    source_root.mkdir()
    _write_legacy_config(
        source_root,
        env_text="ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n",
        state_text='{"selected_remote_aliases":["alpha"]}\n',
    )

    active_env = tmp_path / "active-config" / ".env"
    active_data = tmp_path / "active-data"
    reset_runtime_paths_cache()
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(active_env))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(active_data))

    exit_code = main.cmd_import_config(Namespace(dry_run=False, force=False, source_root=source_root))

    assert exit_code == 0
    assert active_env.read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n"
    assert (active_data / "runtime_state.json").read_text(encoding="utf-8") == '{"selected_remote_aliases":["alpha"]}\n'
    assert (source_root / ".env").read_text(encoding="utf-8") == "ORG_USERNAME=alice\nHASH_SALT=legacy-salt\n"
    assert (source_root / "reports" / "runtime_state.json").read_text(encoding="utf-8") == '{"selected_remote_aliases":["alpha"]}\n'
