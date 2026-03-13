from __future__ import annotations

from llm_usage.env import split_csv_env

from .file_collector import FileCollector


def build_claude_collector() -> FileCollector:
    defaults = [
        "~/.claude/**/*.jsonl",
        "~/.claude/**/*.json",
        "~/.config/claude/**/*.jsonl",
    ]
    return FileCollector("claude_code", split_csv_env("CLAUDE_LOG_PATHS", defaults))
