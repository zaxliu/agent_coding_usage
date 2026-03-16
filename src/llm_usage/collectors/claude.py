from __future__ import annotations

from llm_usage.env import split_csv_env

from .file_collector import FileCollector


def build_claude_collector(source_name: str = "local", source_host_hash: str = "") -> FileCollector:
    defaults = [
        "~/.claude/**/*.jsonl",
        "~/.claude/**/*.json",
        "~/.config/claude/**/*.jsonl",
    ]
    return FileCollector(
        "claude_code",
        split_csv_env("CLAUDE_LOG_PATHS", defaults),
        source_name=source_name,
        source_host_hash=source_host_hash,
    )
