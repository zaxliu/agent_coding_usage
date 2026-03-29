from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import zipfile

from llm_usage.env import upsert_env_var
from llm_usage.paths import resolve_runtime_paths


INTERNAL_PROFILE = "internal"
EXTERNAL_PROFILE = "external"
DEFAULT_EXCLUDES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".DS_Store",
    "dist",
    "reports",
    "src/llm_usage.egg-info",
}

_CLEAR_FOR_ALL = {
    "ORG_USERNAME",
    "CURSOR_WEB_SESSION_TOKEN",
    "CURSOR_WEB_WORKOS_ID",
    "CLAUDE_LOG_PATHS",
    "CODEX_LOG_PATHS",
    "COPILOT_CLI_LOG_PATHS",
    "COPILOT_VSCODE_SESSION_PATHS",
    "CURSOR_LOG_PATHS",
}
_CLEAR_FOR_EXTERNAL = {
    "HASH_SALT",
    "FEISHU_APP_TOKEN",
    "FEISHU_TABLE_ID",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BOT_TOKEN",
}
_RESET_TO_DEFAULTS = {
    "CURSOR_DASHBOARD_BASE_URL": "https://cursor.com",
    "CURSOR_DASHBOARD_TEAM_ID": "0",
    "CURSOR_DASHBOARD_PAGE_SIZE": "300",
    "CURSOR_DASHBOARD_TIMEOUT_SEC": "15",
}


@dataclass(frozen=True)
class BundleArtifact:
    profile: str
    zip_path: Path


def _should_ignore(rel_path: str) -> bool:
    first_part = rel_path.split("/", 1)[0]
    if first_part in DEFAULT_EXCLUDES:
        return True
    return any(rel_path == entry or rel_path.startswith(f"{entry}/") for entry in DEFAULT_EXCLUDES)


def _copy_repo_tree(src_root: Path, dst_root: Path) -> None:
    for path in src_root.rglob("*"):
        rel_path = path.relative_to(src_root).as_posix()
        if _should_ignore(rel_path):
            continue
        target = dst_root / rel_path
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _sanitize_env(path: Path, profile: str) -> None:
    if not path.exists():
        return

    keys_to_clear = set(_CLEAR_FOR_ALL)
    if profile == EXTERNAL_PROFILE:
        keys_to_clear.update(_CLEAR_FOR_EXTERNAL)

    for key in sorted(keys_to_clear):
        upsert_env_var(path, key, "")

    for key, value in _RESET_TO_DEFAULTS.items():
        upsert_env_var(path, key, value)

    text = path.read_text(encoding="utf-8")
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("REMOTE_") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            lines.append(f"{key}=")
        else:
            lines.append(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bundle_basename(profile: str, timestamp: str) -> str:
    return f"agent_coding_usage_{profile}_{timestamp}"


def build_bundles(
    repo_root: Path,
    output_dir: Path,
    keep_staging: bool = False,
    timestamp: str | None = None,
) -> list[BundleArtifact]:
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[BundleArtifact] = []
    tmp_root = Path(tempfile.mkdtemp(prefix="llm_usage_bundle_"))
    try:
        for profile in (INTERNAL_PROFILE, EXTERNAL_PROFILE):
            bundle_root = tmp_root / _bundle_basename(profile, stamp)
            bundle_root.mkdir(parents=True, exist_ok=True)
            _copy_repo_tree(repo_root, bundle_root)
            bundle_env = bundle_root / ".env"
            runtime_env = resolve_runtime_paths(repo_root).env_path
            if runtime_env.exists():
                bundle_env.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(runtime_env, bundle_env)
            elif not bundle_env.exists():
                env_example = repo_root / ".env.example"
                if env_example.exists():
                    bundle_env.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
            _sanitize_env(bundle_env, profile)
            bootstrap_env = bundle_root / "src" / "llm_usage" / "resources" / "bootstrap.env"
            bootstrap_env.parent.mkdir(parents=True, exist_ok=True)
            bootstrap_env.write_text(bundle_env.read_text(encoding="utf-8"), encoding="utf-8")

            zip_path = output_dir / f"{bundle_root.name}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in bundle_root.rglob("*"):
                    if path.is_dir():
                        continue
                    zf.write(path, path.relative_to(tmp_root))

            artifacts.append(BundleArtifact(profile=profile, zip_path=zip_path))

        if keep_staging:
            kept_root = output_dir / f"bundle_staging_{stamp}"
            if kept_root.exists():
                shutil.rmtree(kept_root)
            shutil.copytree(tmp_root, kept_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return artifacts
