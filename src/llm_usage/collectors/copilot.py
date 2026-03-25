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
            os.path.join(home, ".copilot", "session-state", "**", "*.jsonl"),
        ]
    return ["~/.copilot/session-state/**/*.jsonl"]


def _default_copilot_vscode_paths() -> list[str]:
    if os.name == "nt":
        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            return [
                os.path.join(appdata, "Code", "User", "globalStorage", "emptyWindowChatSessions", "*.jsonl"),
                os.path.join(
                    appdata,
                    "Code",
                    "User",
                    "workspaceStorage",
                    "**",
                    "chatEditingSessions",
                    "*",
                    "state.json",
                ),
            ]
        home = os.path.expanduser("~")
        return [
            os.path.join(home, "AppData", "Roaming", "Code", "User", "globalStorage", "emptyWindowChatSessions", "*.jsonl"),
            os.path.join(
                home,
                "AppData",
                "Roaming",
                "Code",
                "User",
                "workspaceStorage",
                "**",
                "chatEditingSessions",
                "*",
                "state.json",
            ),
        ]

    if sys.platform == "darwin":
        return [
            "~/Library/Application Support/Code/User/globalStorage/emptyWindowChatSessions/*.jsonl",
            "~/Library/Application Support/Code/User/workspaceStorage/**/chatEditingSessions/*/state.json",
        ]

    return [
        "~/.config/Code/User/globalStorage/emptyWindowChatSessions/*.jsonl",
        "~/.config/Code/User/workspaceStorage/**/chatEditingSessions/*/state.json",
    ]
