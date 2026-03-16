from __future__ import annotations

import os

from llm_usage.env import split_csv_env

from .base import BaseCollector
from .cursor_dashboard import CursorDashboardCollector
from .file_collector import FileCollector


def build_cursor_collector(source_name: str = "local", source_host_hash: str = "") -> BaseCollector:
    session_token = os.getenv("CURSOR_WEB_SESSION_TOKEN", "").strip()
    if session_token:
        collector = CursorDashboardCollector(
            session_token=session_token,
            workos_id=os.getenv("CURSOR_WEB_WORKOS_ID", "").strip(),
            team_id=_env_int("CURSOR_DASHBOARD_TEAM_ID", 0),
            page_size=_env_int("CURSOR_DASHBOARD_PAGE_SIZE", 300),
            base_url=os.getenv("CURSOR_DASHBOARD_BASE_URL", "https://cursor.com").strip()
            or "https://cursor.com",
            timeout_sec=_env_float("CURSOR_DASHBOARD_TIMEOUT_SEC", 15.0),
        )
        collector.source_name = source_name
        collector.source_host_hash = source_host_hash
        return collector

    defaults = [
        "~/.cursor/logs/**/*.jsonl",
        "~/.cursor/logs/**/*.json",
        "~/.config/Cursor/User/workspaceStorage/**/*.json",
        "~/.config/Cursor/User/globalStorage/**/*.json",
        "~/Library/Application Support/Cursor/User/workspaceStorage/**/*.json",
        "~/Library/Application Support/Cursor/User/globalStorage/**/*.json",
    ]
    return FileCollector(
        "cursor",
        split_csv_env("CURSOR_LOG_PATHS", defaults),
        source_name=source_name,
        source_host_hash=source_host_hash,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
