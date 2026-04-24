from __future__ import annotations

import json
import os
import sys
from glob import glob
from pathlib import Path
from typing import Optional

from llm_usage.env import split_csv_env

from .file_collector import FileCollector
from .remote_file import RemoteFileCollector

_REMOTE_CLINE_PROBE_SCRIPT = """
import base64, glob, json, os

payload = json.loads(base64.b64decode(PAYLOAD_B64).decode("utf-8"))
matches = []
for spec in payload.get("jobs", []):
    for pattern in spec.get("patterns", []):
        try:
            for path in glob.glob(os.path.expanduser(pattern), recursive=True):
                if os.path.isfile(path) and path.lower().endswith((".json", ".jsonl")):
                    matches.append(path)
        except Exception:
            pass

versions = set()
for pattern in payload.get("version_patterns", []):
    try:
        for path in glob.glob(os.path.expanduser(pattern), recursive=True):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if (
                data.get("name") == "claude-dev"
                and data.get("publisher") == "saoudrizwan"
                and isinstance(data.get("version"), str)
                and data["version"].strip()
            ):
                versions.add(data["version"].strip())
    except Exception:
        pass

print(json.dumps({"matches": len(sorted(set(matches))), "versions": sorted(versions)}))
"""


class ClineVscodeCollector(FileCollector):
    def __init__(
        self,
        patterns: list[str],
        source_name: str = "local",
        source_host_hash: str = "",
        version_patterns: Optional[list[str]] = None,
    ) -> None:
        super().__init__(
            "cline_vscode",
            patterns,
            source_name=source_name,
            source_host_hash=source_host_hash,
        )
        self.version_patterns = version_patterns or _default_cline_extension_package_paths()

    def probe(self) -> tuple[bool, str]:
        ok, msg = super().probe()
        versions = _detect_cline_versions(self.version_patterns)
        if not versions:
            return ok, msg
        label = "version" if len(versions) == 1 else "versions"
        return ok, f"{msg}, {label} {', '.join(versions)}"


class ClineRemoteCollector(RemoteFileCollector):
    def __init__(
        self,
        *args,
        version_patterns: Optional[list[str]] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.version_patterns = version_patterns or _default_remote_cline_extension_package_paths()

    def _build_remote_payload(self, cursor=None) -> dict[str, object]:
        payload = super()._build_remote_payload(cursor)
        payload["version_patterns"] = list(self.version_patterns)
        return payload

    def probe(self) -> tuple[bool, str]:
        self._log_progress("探测：查找远端 Python")
        python_cmd, error = self._discover_python()
        if error:
            return False, error
        if not python_cmd:
            return False, "no remote python interpreter found"
        self._log_progress(f"探测：使用远端解释器 {python_cmd}")
        payload, error = self._run_python_script(python_cmd, _REMOTE_CLINE_PROBE_SCRIPT)
        if error:
            return False, error
        matches = payload.get("matches")
        versions = payload.get("versions")
        if not isinstance(matches, int):
            return False, "remote probe returned invalid payload"
        versions_text = ""
        if isinstance(versions, list):
            normalized = [str(item).strip() for item in versions if str(item).strip()]
            if normalized:
                label = "version" if len(normalized) == 1 else "versions"
                versions_text = f", {label} {', '.join(normalized)}"
        if matches == 0:
            return False, f"no data files found for {self.name}{versions_text}"
        return True, f"{matches} remote files detected{versions_text}"


def build_cline_vscode_collector(
    source_name: str = "local",
    source_host_hash: str = "",
    patterns: Optional[list[str]] = None,
    version_patterns: Optional[list[str]] = None,
) -> ClineVscodeCollector:
    return ClineVscodeCollector(
        patterns or split_csv_env("CLINE_VSCODE_SESSION_PATHS", _default_cline_vscode_paths()),
        source_name=source_name,
        source_host_hash=source_host_hash,
        version_patterns=version_patterns,
    )


def default_remote_cline_vscode_paths() -> list[str]:
    return [
        "~/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/tasks/*/api_conversation_history.json",
        "~/.vscode-server-insiders/data/User/globalStorage/saoudrizwan.claude-dev/tasks/*/api_conversation_history.json",
        "~/.cursor-server/data/User/globalStorage/saoudrizwan.claude-dev/tasks/*/api_conversation_history.json",
    ]


def _detect_cline_versions(patterns: list[str]) -> list[str]:
    versions: set[str] = set()
    for pattern in patterns:
        for path in glob(str(Path(pattern).expanduser()), recursive=True):
            package_path = Path(path)
            if not package_path.is_file():
                continue
            try:
                data = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("name") != "claude-dev" or data.get("publisher") != "saoudrizwan":
                continue
            version = str(data.get("version") or "").strip()
            if version:
                versions.add(version)
    return sorted(versions)


def _default_cline_vscode_paths() -> list[str]:
    if os.name == "nt":
        roots = _windows_vscode_user_roots()
    elif sys.platform == "darwin":
        roots = [
            "~/Library/Application Support/Code/User",
            "~/Library/Application Support/Code - Insiders/User",
            "~/Library/Application Support/Code - Exploration/User",
            "~/Library/Application Support/Cursor/User",
            "~/Library/Application Support/VSCodium/User",
        ]
    else:
        roots = [
            "~/.config/Code/User",
            "~/.config/Code - Insiders/User",
            "~/.config/Code - Exploration/User",
            "~/.config/Cursor/User",
            "~/.config/VSCodium/User",
            "~/.vscode-server/data/User",
            "~/.vscode-server-insiders/data/User",
            "~/.cursor-server/data/User",
            "~/.vscode-remote/data/User",
            "/tmp/.vscode-server/data/User",
            "/workspace/.vscode-server/data/User",
        ]

    patterns: list[str] = []
    for root in roots:
        patterns.append(
            os.path.join(root, "globalStorage", "saoudrizwan.claude-dev", "tasks", "*", "api_conversation_history.json")
        )
    return patterns


def _default_cline_extension_package_paths() -> list[str]:
    return [
        "~/.vscode/extensions/saoudrizwan.claude-dev-*/package.json",
        "~/.cursor/extensions/saoudrizwan.claude-dev-*/package.json",
    ]


def _default_remote_cline_extension_package_paths() -> list[str]:
    return [
        "~/.vscode-server/extensions/saoudrizwan.claude-dev-*/package.json",
        "~/.vscode-server-insiders/extensions/saoudrizwan.claude-dev-*/package.json",
        "~/.cursor-server/extensions/saoudrizwan.claude-dev-*/package.json",
    ]


def _windows_vscode_user_roots() -> list[str]:
    appdata = os.getenv("APPDATA", "").strip()
    roots: list[str] = []
    if appdata:
        for variant in ("Code", "Code - Insiders", "Code - Exploration", "Cursor", "VSCodium"):
            roots.append(os.path.join(appdata, variant, "User"))
    else:
        home = os.path.expanduser("~")
        for variant in ("Code", "Code - Insiders", "Code - Exploration", "Cursor", "VSCodium"):
            roots.append(os.path.join(home, "AppData", "Roaming", variant, "User"))
    return roots
