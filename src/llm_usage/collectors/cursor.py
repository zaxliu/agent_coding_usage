from __future__ import annotations

from llm_usage.env import split_csv_env

from .file_collector import FileCollector


def build_cursor_collector() -> FileCollector:
    defaults = [
        "~/.cursor/logs/**/*.jsonl",
        "~/.cursor/logs/**/*.json",
        "~/.config/Cursor/User/workspaceStorage/**/*.json",
        "~/.config/Cursor/User/globalStorage/**/*.json",
        "~/Library/Application Support/Cursor/User/workspaceStorage/**/*.json",
        "~/Library/Application Support/Cursor/User/globalStorage/**/*.json",
    ]
    return FileCollector("cursor", split_csv_env("CURSOR_LOG_PATHS", defaults))
