from __future__ import annotations

from llm_usage.env import split_csv_env

from .file_collector import FileCollector


def build_codex_collector() -> FileCollector:
    defaults = [
        "~/.codex/**/*.jsonl",
        "~/.codex/**/*.json",
    ]
    return FileCollector("codex", split_csv_env("CODEX_LOG_PATHS", defaults))
