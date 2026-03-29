from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import os
import shlex
from pathlib import Path
import shutil
import sys


APP_NAME = "llm-usage"
_PROMPT_ACCEPT = {"y", "yes", "是", "确认"}
_RUNTIME_PATHS_CACHE: dict[tuple[str, str, str], "RuntimePaths"] = {}


@dataclass(frozen=True)
class RuntimePaths:
    env_path: Path
    config_dir: Path
    data_dir: Path
    reports_dir: Path
    runtime_state_path: Path


def reset_runtime_paths_cache() -> None:
    _RUNTIME_PATHS_CACHE.clear()


def resolve_runtime_paths(legacy_root: Path | None = None) -> RuntimePaths:
    root = (legacy_root or Path.cwd()).resolve()
    env_override = os.environ.get("LLM_USAGE_ENV_FILE", "").strip()
    data_override = os.environ.get("LLM_USAGE_DATA_DIR", "").strip()
    cache_key = (str(root), env_override, data_override)
    cached = _RUNTIME_PATHS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    preferred_paths = resolve_active_runtime_paths()
    preferred_env_path = preferred_paths.env_path
    preferred_state_path = preferred_paths.runtime_state_path

    legacy_env_path = root / ".env"
    legacy_state_path = root / "reports" / "runtime_state.json"

    env_path = _resolve_file_path(
        label=".env",
        preferred=preferred_env_path,
        legacy=legacy_env_path,
    )
    runtime_state_path = _resolve_file_path(
        label="runtime state",
        preferred=preferred_state_path,
        legacy=legacy_state_path,
    )

    paths = RuntimePaths(
        env_path=env_path,
        config_dir=preferred_paths.config_dir,
        data_dir=preferred_paths.data_dir,
        reports_dir=preferred_paths.reports_dir,
        runtime_state_path=runtime_state_path,
    )
    _RUNTIME_PATHS_CACHE[cache_key] = paths
    return paths


def resolve_active_runtime_paths() -> RuntimePaths:
    config_dir = _config_dir()
    data_dir = _data_dir()
    env_override = os.environ.get("LLM_USAGE_ENV_FILE", "").strip()
    preferred_env_path = Path(env_override).expanduser() if env_override else config_dir / ".env"
    preferred_state_path = data_dir / "runtime_state.json"
    return RuntimePaths(
        env_path=preferred_env_path,
        config_dir=preferred_env_path.parent,
        data_dir=data_dir,
        reports_dir=data_dir / "reports",
        runtime_state_path=preferred_state_path,
    )


def _config_dir() -> Path:
    env_file = os.environ.get("LLM_USAGE_ENV_FILE", "").strip()
    if env_file:
        return Path(env_file).expanduser().parent
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def _data_dir() -> Path:
    override = os.environ.get("LLM_USAGE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _resolve_file_path(label: str, preferred: Path, legacy: Path) -> Path:
    if preferred.exists():
        return preferred
    if not legacy.exists() or preferred == legacy:
        return preferred
    import_command = _legacy_import_command(legacy)
    if _is_interactive():
        answer = input(
            f"检测到旧版 {label} 在 {legacy}。按 `y` 会把这个旧文件复制到 {preferred}；"
            f"如果要手动执行一次性迁移，可运行 `{import_command}`。"
            f"是否复制到 {preferred}？[y/N]: "
        ).strip().lower()
        if answer in _PROMPT_ACCEPT:
            preferred.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, preferred)
            print(f"info: migrated {label} from {legacy} to {preferred}")
            return preferred
        print(f"warn: using legacy {label} for this run: {legacy}")
        return legacy
    print(
        f"warn: found legacy {label} at {legacy}; "
        f"new default is {preferred}. Run `{import_command}` once to migrate these files. "
        f"Using legacy file for this run."
    )
    return legacy


def _is_interactive() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _legacy_import_command(legacy: Path) -> str:
    legacy_root = legacy.parent if legacy.name == ".env" else legacy.parent.parent
    legacy_root_str = str(legacy_root)
    if sys.platform == "win32":
        legacy_root_str = legacy_root_str.replace('"', '\\"')
        legacy_root_str = f'"{legacy_root_str}"'
    else:
        legacy_root_str = shlex.quote(legacy_root_str)
    return f"{APP_NAME} import-config --from {legacy_root_str}"


def read_bootstrap_env_text() -> str:
    resource = resources.files("llm_usage.resources").joinpath("bootstrap.env")
    return resource.read_text(encoding="utf-8")
