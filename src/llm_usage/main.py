from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from llm_usage.aggregation import aggregate_events
from llm_usage.collectors import (
    BaseCollector,
    build_claude_collector,
    build_codex_collector,
    build_cursor_collector,
)
from llm_usage.env import load_dotenv
from llm_usage.identity import hash_user
from llm_usage.reporting import print_terminal_report, write_csv_report
from llm_usage.sinks.feishu_bitable import (
    FeishuBitableClient,
    fetch_first_table_id,
    fetch_tenant_access_token,
)


def _repo_root() -> Path:
    return Path.cwd()


def _env_path() -> Path:
    return _repo_root() / ".env"


def _load_runtime_env() -> None:
    load_dotenv(_env_path())


def _collectors() -> list[BaseCollector]:
    return [
        build_claude_collector(),
        build_codex_collector(),
        build_cursor_collector(),
    ]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing env var: {name}")
    return value


def _required_org_username() -> str:
    username = os.getenv("ORG_USERNAME", "").strip()
    if username:
        return username
    raise RuntimeError("missing env var: ORG_USERNAME (required, e.g. san.zhang)")


def _collect_all(lookback_days: int) -> tuple[list, list[str]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, lookback_days))

    events = []
    warnings: list[str] = []
    for collector in _collectors():
        out = collector.collect(start=start, end=end)
        events.extend(out.events)
        warnings.extend(out.warnings)
    return events, warnings


def cmd_init(_: argparse.Namespace) -> int:
    root = _repo_root()
    env_example = root / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "\n".join(
                [
                    "# Identity",
                    "# Required: group username, e.g. san.zhang",
                    "ORG_USERNAME=",
                    "HASH_SALT=",
                    "TIMEZONE=Asia/Shanghai",
                    "LOOKBACK_DAYS=7",
                    "",
                    "# Feishu Bitable",
                    "FEISHU_APP_TOKEN=",
                    "# Optional. If empty, sync uses the first table under FEISHU_APP_TOKEN.",
                    "FEISHU_TABLE_ID=",
                    "FEISHU_APP_ID=",
                    "FEISHU_APP_SECRET=",
                    "FEISHU_BOT_TOKEN=",
                    "",
                    "# Optional source path overrides (comma-separated globs)",
                    "CLAUDE_LOG_PATHS=",
                    "CODEX_LOG_PATHS=",
                    "CURSOR_LOG_PATHS=",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    env_file = root / ".env"
    if not env_file.exists():
        env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")

    reports_dir = root / "reports"
    reports_dir.mkdir(exist_ok=True)

    print(f"initialized: {env_file}")
    print("next: fill env values then run `llm-usage doctor` and `llm-usage sync`")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    _load_runtime_env()
    missing = not os.getenv("ORG_USERNAME", "").strip()
    print(f"ORG_USERNAME: {'MISSING' if missing else 'OK'}")

    for var in ("HASH_SALT", "TIMEZONE"):
        missing = not os.getenv(var, "").strip()
        print(f"{var}: {'MISSING' if missing else 'OK'}")

    for collector in _collectors():
        ok, msg = collector.probe()
        print(f"collector {collector.name}: {'OK' if ok else 'WARN'} - {msg}")
    return 0


def _build_aggregates() -> tuple[list, list[str]]:
    _load_runtime_env()
    username = _required_org_username()
    salt = _required_env("HASH_SALT")
    timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "7"))

    events, warnings = _collect_all(lookback_days)
    user_hash = hash_user(username, salt)
    rows = aggregate_events(events, user_hash=user_hash, timezone_name=timezone_name)
    return rows, warnings


def cmd_collect(_: argparse.Namespace) -> int:
    rows, warnings = _build_aggregates()
    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")

    print_terminal_report(rows)
    path = write_csv_report(rows, _repo_root() / "reports")
    print(f"csv: {path}")
    return 0


def cmd_sync(_: argparse.Namespace) -> int:
    rows, warnings = _build_aggregates()
    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")

    print_terminal_report(rows)
    csv_path = write_csv_report(rows, _repo_root() / "reports")
    print(f"csv: {csv_path}")

    app_token = _required_env("FEISHU_APP_TOKEN")
    table_id = os.getenv("FEISHU_TABLE_ID", "").strip()
    bot_token = os.getenv("FEISHU_BOT_TOKEN", "").strip()
    if not bot_token:
        app_id = _required_env("FEISHU_APP_ID")
        app_secret = _required_env("FEISHU_APP_SECRET")
        bot_token = fetch_tenant_access_token(app_id=app_id, app_secret=app_secret)
    if not table_id:
        table_id = fetch_first_table_id(app_token=app_token, bot_token=bot_token)
        print(f"info: FEISHU_TABLE_ID empty, auto-selected first table: {table_id}")

    client = FeishuBitableClient(app_token=app_token, table_id=table_id, bot_token=bot_token)
    result = client.upsert(rows)
    print(f"sync created={result.created} updated={result.updated} failed={result.failed}")
    return 0 if result.failed == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Team LLM usage collector and Feishu sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize .env and folders")
    sub.add_parser("doctor", help="Check data sources and required config")
    sub.add_parser("collect", help="Collect usage locally and output local report")
    sub.add_parser("sync", help="Collect locally then upsert aggregates to Feishu")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cmd_map = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "collect": cmd_collect,
        "sync": cmd_sync,
    }
    return cmd_map[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
