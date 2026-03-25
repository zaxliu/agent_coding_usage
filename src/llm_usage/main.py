from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from llm_usage.aggregation import aggregate_events
from llm_usage.bundle import build_bundles
from llm_usage.collectors import (
    BaseCollector,
    build_claude_collector,
    build_copilot_cli_collector,
    build_copilot_vscode_collector,
    build_codex_collector,
    build_cursor_collector,
    build_opencode_collector,
)
from llm_usage.cursor_login import (
    fetch_cursor_session_token_via_browser,
    fetch_cursor_workos_id_from_local_browsers,
)
from llm_usage.env import load_dotenv, upsert_env_var
from llm_usage.identity import hash_source_host, hash_user
from llm_usage.interaction import confirm_save_temporary_remote, select_remotes
from llm_usage.remotes import append_remote_to_env, build_remote_collectors, parse_remote_configs_from_env
from llm_usage.reporting import print_terminal_report, write_csv_report
from llm_usage.runtime_state import load_selected_remote_aliases, save_selected_remote_aliases
from llm_usage.sinks.feishu_bitable import (
    FeishuBitableClient,
    fetch_first_table_id,
    fetch_tenant_access_token,
)


def _repo_root() -> Path:
    return Path.cwd()


def _env_path() -> Path:
    return _repo_root() / ".env"


def _runtime_state_path() -> Path:
    return _repo_root() / "reports" / "runtime_state.json"


def _load_runtime_env() -> None:
    load_dotenv(_env_path())


def _collectors(local_source_host_hash: str) -> list[BaseCollector]:
    return [
        build_claude_collector(source_host_hash=local_source_host_hash),
        build_codex_collector(source_host_hash=local_source_host_hash),
        build_copilot_cli_collector(source_host_hash=local_source_host_hash),
        build_copilot_vscode_collector(source_host_hash=local_source_host_hash),
        build_cursor_collector(source_host_hash=local_source_host_hash),
        build_opencode_collector(source_host_hash=local_source_host_hash),
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
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("缺少 ORG_USERNAME，请在交互终端中运行并输入")

    print("`.env` 中缺少 ORG_USERNAME，这是必填项。")
    print("请输入你的组内用户名，例如 san.zhang。直接回车则退出。")
    username = input("ORG_USERNAME：").strip()
    if not username:
        raise RuntimeError("ORG_USERNAME 为必填项")

    env_file = _env_path()
    if not env_file.exists():
        cmd_init(argparse.Namespace())
    upsert_env_var(env_file, "ORG_USERNAME", username)
    os.environ["ORG_USERNAME"] = username
    print("info: 已将 ORG_USERNAME 写入 .env")
    return username


def _collect_all(lookback_days: int, collectors: list[BaseCollector]) -> tuple[list, list[str]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, lookback_days))

    events = []
    warnings: list[str] = []
    for collector in collectors:
        out = collector.collect(start=start, end=end)
        for event in out.events:
            events.append(
                event
                if event.source_host_hash
                else replace(event, source_host_hash=getattr(collector, "source_host_hash", ""))
            )
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
                    "COPILOT_CLI_LOG_PATHS=",
                    "COPILOT_VSCODE_SESSION_PATHS=",
                    "CURSOR_LOG_PATHS=",
                    "",
                    "# Optional: Cursor Pro+ web dashboard collector.",
                    "# If CURSOR_WEB_SESSION_TOKEN is set, cursor collector uses dashboard API",
                    "# instead of local log files.",
                    "# collect/sync auto-open browser login when token is empty and local logs are unavailable.",
                    "CURSOR_WEB_SESSION_TOKEN=",
                    "CURSOR_WEB_WORKOS_ID=",
                    "CURSOR_DASHBOARD_BASE_URL=https://cursor.com",
                    "CURSOR_DASHBOARD_TEAM_ID=0",
                    "CURSOR_DASHBOARD_PAGE_SIZE=300",
                    "CURSOR_DASHBOARD_TIMEOUT_SEC=15",
                    "",
                    "# Optional remote SSH sources",
                    "REMOTE_HOSTS=",
                    "REMOTE_SAMPLE_SSH_HOST=",
                    "REMOTE_SAMPLE_SSH_USER=",
                    "REMOTE_SAMPLE_SSH_PORT=22",
                    "REMOTE_SAMPLE_LABEL=",
                    "REMOTE_SAMPLE_CLAUDE_LOG_PATHS=",
                    "REMOTE_SAMPLE_CODEX_LOG_PATHS=",
                    "REMOTE_SAMPLE_COPILOT_CLI_LOG_PATHS=",
                    "REMOTE_SAMPLE_COPILOT_VSCODE_SESSION_PATHS=",
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

    print(f"初始化完成：{env_file}")
    print("下一步：补全配置后运行 `llm-usage doctor` 和 `llm-usage sync`")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    _load_runtime_env()
    missing = not os.getenv("ORG_USERNAME", "").strip()
    print(f"ORG_USERNAME: {'MISSING' if missing else 'OK'}")

    for var in ("HASH_SALT", "TIMEZONE"):
        missing = not os.getenv(var, "").strip()
        print(f"{var}: {'MISSING' if missing else 'OK'}")

    local_hash = ""
    username = os.getenv("ORG_USERNAME", "").strip()
    salt = os.getenv("HASH_SALT", "").strip()
    if username and salt:
        local_hash = hash_source_host(username, "local", salt)

    for collector in _collectors(local_hash):
        ok, msg = collector.probe()
        print(f"collector {collector.name}[{collector.source_name}]: {'OK' if ok else 'WARN'} - {msg}")

    for collector in build_remote_collectors(parse_remote_configs_from_env(), username=username, salt=salt):
        ok, msg = collector.probe()
        print(f"collector {collector.name}[{collector.source_name}]: {'OK' if ok else 'WARN'} - {msg}")
    return 0


def _capture_and_save_cursor_token(timeout_sec: int, browser: str, user_data_dir: str) -> str:
    env_file = _env_path()
    if not env_file.exists():
        cmd_init(argparse.Namespace())

    token = fetch_cursor_session_token_via_browser(
        timeout_sec=timeout_sec,
        browser=browser,
        user_data_dir=user_data_dir,
    )
    workos_id = fetch_cursor_workos_id_from_local_browsers(browser=browser) or ""
    upsert_env_var(env_file, "CURSOR_WEB_SESSION_TOKEN", token)
    upsert_env_var(env_file, "CURSOR_WEB_WORKOS_ID", workos_id)
    os.environ["CURSOR_WEB_SESSION_TOKEN"] = token
    if workos_id:
        os.environ["CURSOR_WEB_WORKOS_ID"] = workos_id
    else:
        os.environ.pop("CURSOR_WEB_WORKOS_ID", None)
    return token


def _clear_saved_cursor_token() -> None:
    env_file = _env_path()
    if env_file.exists():
        upsert_env_var(env_file, "CURSOR_WEB_SESSION_TOKEN", "")
        upsert_env_var(env_file, "CURSOR_WEB_WORKOS_ID", "")
    os.environ.pop("CURSOR_WEB_SESSION_TOKEN", None)
    os.environ.pop("CURSOR_WEB_WORKOS_ID", None)


def _maybe_capture_cursor_token(timeout_sec: int, browser: str, user_data_dir: str) -> str | None:
    _load_runtime_env()
    if os.getenv("CURSOR_WEB_SESSION_TOKEN", "").strip():
        cursor_collector = build_cursor_collector()
        ok, msg = cursor_collector.probe()
        if ok:
            return None
        if "authentication failed" in msg.lower():
            print(
                "warn: existing CURSOR_WEB_SESSION_TOKEN appears expired; "
                "attempting refresh from system browser cookies..."
            )
            _clear_saved_cursor_token()
            try:
                _capture_and_save_cursor_token(
                    timeout_sec=timeout_sec,
                    browser=browser,
                    user_data_dir=user_data_dir,
                )
            except RuntimeError as exc:
                print(f"warn: cursor token refresh failed: {exc}")
                print("warn: continuing with local cursor sources")
                return None
            print("info: refreshed CURSOR_WEB_SESSION_TOKEN and saved to .env")
            return None
        return f"cursor dashboard probe failed with existing token: {msg}"

    try:
        lookback_days = max(1, int(os.getenv("LOOKBACK_DAYS", "7") or "7"))
    except ValueError:
        lookback_days = 7
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    cursor_collector = build_cursor_collector()
    ok, _ = cursor_collector.probe()
    if ok:
        local_out = cursor_collector.collect(start=start, end=end)
        if local_out.events:
            return None
        print(
            "info: cursor local logs found but no events in selected lookback; "
            "opening browser login..."
        )
    else:
        print(
            "info: CURSOR_WEB_SESSION_TOKEN is empty and local cursor logs are unavailable; "
            "opening browser login..."
        )
    try:
        _capture_and_save_cursor_token(
            timeout_sec=timeout_sec,
            browser=browser,
            user_data_dir=user_data_dir,
        )
    except RuntimeError as exc:
        print(f"warn: automatic cursor login failed: {exc}")
        print("warn: continuing without dashboard token (cursor may have no data)")
        return None
    print("info: saved CURSOR_WEB_SESSION_TOKEN to .env")
    return None


def _resolve_remote_selection(
    args: argparse.Namespace,
    configured_remotes,
) -> tuple[list[str], list]:
    state_aliases = load_selected_remote_aliases(_runtime_state_path())
    configured_aliases = [config.alias for config in configured_remotes]
    if getattr(args, "ui", "auto") == "none":
        return state_aliases if state_aliases else [], []
    if state_aliases:
        defaults = [alias for alias in state_aliases if alias in configured_aliases]
    else:
        defaults = list(configured_aliases)
    result = select_remotes(configured_remotes, defaults, ui_mode=getattr(args, "ui", "auto"))
    save_selected_remote_aliases(_runtime_state_path(), result.selected_aliases)
    return result.selected_aliases, result.temporary_remotes


def _save_temporary_remotes(args: argparse.Namespace, remotes: list, configured_aliases: list[str]) -> None:
    for remote in remotes:
        if confirm_save_temporary_remote(remote, ui_mode=getattr(args, "ui", "auto")):
            alias = append_remote_to_env(_env_path(), remote, configured_aliases)
            configured_aliases.append(alias)
            print(f"info: 已将临时远端保存到 .env：{alias}")


def _build_aggregates(args: argparse.Namespace) -> tuple[list, list[str]]:
    _load_runtime_env()
    username = _required_org_username()
    salt = _required_env("HASH_SALT")
    timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "7"))
    local_source_host_hash = hash_source_host(username, "local", salt)

    configured_remotes = parse_remote_configs_from_env()
    selected_aliases, temporary_remotes = _resolve_remote_selection(args, configured_remotes)
    selected_configs = [config for config in configured_remotes if config.alias in selected_aliases]
    selected_configs.extend(temporary_remotes)

    collectors = _collectors(local_source_host_hash)
    collectors.extend(build_remote_collectors(selected_configs, username=username, salt=salt))
    events, warnings = _collect_all(lookback_days, collectors)
    user_hash = hash_user(username, salt)
    rows = aggregate_events(events, user_hash=user_hash, timezone_name=timezone_name)
    if temporary_remotes:
        _save_temporary_remotes(args, temporary_remotes, [config.alias for config in configured_remotes])
    return rows, warnings


def cmd_collect(args: argparse.Namespace) -> int:
    cursor_probe_warning = _maybe_capture_cursor_token(
        timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
        browser=getattr(args, "cursor_login_browser", "default"),
        user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
    )
    rows, warnings = _build_aggregates(args)
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")

    print_terminal_report(rows)
    path = write_csv_report(rows, _repo_root() / "reports")
    print(f"csv: {path}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    cursor_probe_warning = _maybe_capture_cursor_token(
        timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
        browser=getattr(args, "cursor_login_browser", "default"),
        user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
    )
    rows, warnings = _build_aggregates(args)
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
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
    print(f"飞书同步完成：新增={result.created} 更新={result.updated} 失败={result.failed}")
    if result.warning_samples:
        for item in result.warning_samples:
            print(f"warn: {item}")
    if result.error_samples:
        print("飞书失败示例：")
        for item in result.error_samples:
            print(f"warn: {item}")
    return 0 if result.failed == 0 else 2


def cmd_bundle(args: argparse.Namespace) -> int:
    artifacts = build_bundles(
        repo_root=_repo_root(),
        output_dir=Path(args.output_dir),
        keep_staging=args.keep_staging,
    )
    for artifact in artifacts:
        print(f"{artifact.profile}: {artifact.zip_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Team LLM usage collector and Feishu sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize .env and folders")
    sub.add_parser("doctor", help="Check data sources and required config")
    bundle_parser = sub.add_parser("bundle", help="Build internal and external distribution zip bundles")
    bundle_parser.add_argument(
        "--output-dir",
        type=str,
        default="dist",
        help="Directory for generated zip bundles",
    )
    bundle_parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="Keep copied staging directories under output-dir for inspection",
    )
    collect_parser = sub.add_parser("collect", help="Collect usage locally and output local report")
    collect_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help="Max wait time when auto-opening browser to capture Cursor session token",
    )
    collect_parser.add_argument(
        "--cursor-login-browser",
        choices=["default", "chrome", "edge", "safari", "firefox", "chromium", "msedge", "webkit"],
        default="default",
        help="Browser used for auto login when capturing Cursor session token",
    )
    collect_parser.add_argument(
        "--cursor-login-user-data-dir",
        type=str,
        default="",
        help="Compatibility option; ignored in system-browser cookie mode",
    )
    collect_parser.add_argument(
        "--ui",
        choices=["auto", "tui", "cli", "none"],
        default="auto",
        help="Remote selection UI mode",
    )
    sync_parser = sub.add_parser("sync", help="Collect locally then upsert aggregates to Feishu")
    sync_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help="Max wait time when auto-opening browser to capture Cursor session token",
    )
    sync_parser.add_argument(
        "--cursor-login-browser",
        choices=["default", "chrome", "edge", "safari", "firefox", "chromium", "msedge", "webkit"],
        default="default",
        help="Browser used for auto login when capturing Cursor session token",
    )
    sync_parser.add_argument(
        "--cursor-login-user-data-dir",
        type=str,
        default="",
        help="Compatibility option; ignored in system-browser cookie mode",
    )
    sync_parser.add_argument(
        "--ui",
        choices=["auto", "tui", "cli", "none"],
        default="auto",
        help="Remote selection UI mode",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cmd_map = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "bundle": cmd_bundle,
        "collect": cmd_collect,
        "sync": cmd_sync,
    }
    return cmd_map[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
