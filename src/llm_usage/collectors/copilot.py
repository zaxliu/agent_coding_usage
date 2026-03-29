from __future__ import annotations

import os
import sys

from llm_usage.env import split_csv_env

from .file_collector import FileCollector


def build_copilot_cli_collector(
    source_name: str = "local",
    source_host_hash: str = "",
    patterns: list[str] | None = None,
) -> FileCollector:
    return FileCollector(
        "copilot_cli",
        patterns or split_csv_env("COPILOT_CLI_LOG_PATHS", _default_copilot_cli_paths()),
        source_name=source_name,
        source_host_hash=source_host_hash,
    )


def build_copilot_vscode_collector(
    source_name: str = "local",
    source_host_hash: str = "",
    patterns: list[str] | None = None,
) -> FileCollector:
    return FileCollector(
        "copilot_vscode",
        patterns or split_csv_env("COPILOT_VSCODE_SESSION_PATHS", _default_copilot_vscode_paths()),
        source_name=source_name,
        source_host_hash=source_host_hash,
    )


def _default_copilot_cli_paths() -> list[str]:
    home = os.path.expanduser("~")
    if os.name == "nt":
        return [
            os.path.join(home, ".copilot", "session-state", "*.json"),
            os.path.join(home, ".copilot", "session-state", "*.jsonl"),
            os.path.join(home, ".copilot", "session-state", "**", "*.jsonl"),
        ]
    return [
        "~/.copilot/session-state/*.json",
        "~/.copilot/session-state/*.jsonl",
        "~/.copilot/session-state/**/*.jsonl",
    ]


def _default_copilot_vscode_paths() -> list[str]:
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
            "~/.vscode-remote/data/User",
            "/tmp/.vscode-server/data/User",
            "/workspace/.vscode-server/data/User",
        ]

    patterns: list[str] = []
    for root in roots:
        patterns.extend(
            [
                os.path.join(root, "workspaceStorage", "**", "chatSessions", "*.json"),
                os.path.join(root, "workspaceStorage", "**", "chatSessions", "*.jsonl"),
                os.path.join(root, "globalStorage", "emptyWindowChatSessions", "*.json"),
                os.path.join(root, "globalStorage", "emptyWindowChatSessions", "*.jsonl"),
                os.path.join(root, "globalStorage", "github.copilot-chat", "**", "*.json"),
                os.path.join(root, "globalStorage", "github.copilot-chat", "**", "*.jsonl"),
            ]
        )
    return patterns


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
