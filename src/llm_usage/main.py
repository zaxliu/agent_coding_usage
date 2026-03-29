from __future__ import annotations

import argparse
import os
import shutil
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
    open_cursor_dashboard_login_page,
)
from llm_usage.env import load_dotenv, upsert_env_var
from llm_usage.identity import hash_source_host, hash_user
from llm_usage.interaction import confirm_save_temporary_remote, select_remotes
from llm_usage.paths import read_bootstrap_env_text, resolve_active_runtime_paths, resolve_runtime_paths
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
    return resolve_runtime_paths(_repo_root()).env_path


def _runtime_state_path() -> Path:
    return resolve_runtime_paths(_repo_root()).runtime_state_path


def _reports_dir() -> Path:
    return resolve_runtime_paths(_repo_root()).reports_dir


def _load_runtime_env() -> None:
    _ensure_env_file_exists()
    load_dotenv(_env_path())


def _ensure_env_file_exists() -> Path:
    env_file = _env_path()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.write_text(read_bootstrap_env_text(), encoding="utf-8")
    return env_file


def _save_cursor_web_credentials(token: str, workos_id: str = "") -> None:
    env_file = _ensure_env_file_exists()
    upsert_env_var(env_file, "CURSOR_WEB_SESSION_TOKEN", token)
    upsert_env_var(env_file, "CURSOR_WEB_WORKOS_ID", workos_id)
    os.environ["CURSOR_WEB_SESSION_TOKEN"] = token
    if workos_id:
        os.environ["CURSOR_WEB_WORKOS_ID"] = workos_id
    else:
        os.environ.pop("CURSOR_WEB_WORKOS_ID", None)


def _should_require_manual_cursor_token_prompt(browser: str) -> bool:
    normalized = (browser or "").strip().lower()
    return os.name == "nt" and normalized in {"default", "chrome", "chromium", "edge", "msedge"}


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

    env_file = _ensure_env_file_exists()
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
                    "# If token is empty and local logs are unavailable, collect/sync may open the",
                    "# login page. On Windows Chromium browsers, it will prompt for manual token paste.",
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

    env_path = _env_path()
    reports_dir = _reports_dir()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.write_text(read_bootstrap_env_text(), encoding="utf-8")

    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"初始化完成：{env_path}")
    print(f"报告目录：{reports_dir}")
    print("下一步：补全配置后运行 `llm-usage doctor` 和 `llm-usage sync`")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    _load_runtime_env()
    print(f"env: {_env_path()}")
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
    token = fetch_cursor_session_token_via_browser(
        timeout_sec=timeout_sec,
        browser=browser,
        user_data_dir=user_data_dir,
    )
    _save_cursor_web_credentials(token)
    return token


def _prompt_for_manual_cursor_token(browser: str, *, automatic_capture_failed: bool) -> str | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None

    if automatic_capture_failed:
        print("warn: automatic Cursor token capture failed.")
    if _should_require_manual_cursor_token_prompt(browser):
        print("info: Windows detected; automatic Cursor browser cookie scanning is disabled.")
    try:
        open_cursor_dashboard_login_page(browser=browser)
        print("info: opened https://cursor.com/dashboard/usage in your browser.")
    except RuntimeError as exc:
        print(f"warn: failed to open browser automatically: {exc}")
        print("info: open https://cursor.com/dashboard/usage in your signed-in browser.")
    print("info: after login, open DevTools > Application > Cookies and copy WorkosCursorSessionToken.")
    token = input("CURSOR_WEB_SESSION_TOKEN (press Enter to skip): ").strip()
    if not token:
        return None
    _save_cursor_web_credentials(token)
    print("info: saved CURSOR_WEB_SESSION_TOKEN to .env")
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
                "clearing saved token and requesting a fresh login..."
            )
            _clear_saved_cursor_token()
            if _should_require_manual_cursor_token_prompt(browser):
                if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=False):
                    return None
                print("warn: continuing with local cursor sources")
                return None
            try:
                _capture_and_save_cursor_token(
                    timeout_sec=timeout_sec,
                    browser=browser,
                    user_data_dir=user_data_dir,
                )
            except RuntimeError as exc:
                print(f"warn: cursor token refresh failed: {exc}")
                if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=True):
                    return None
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
    if _should_require_manual_cursor_token_prompt(browser):
        print(
            "info: Windows detected with a Chromium-based browser. "
            "Automatic Cursor cookie scanning is disabled; manual token input is required."
        )
        if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=False):
            return None
        print("warn: continuing without dashboard token (cursor may have no data)")
        return None
    try:
        _capture_and_save_cursor_token(
            timeout_sec=timeout_sec,
            browser=browser,
            user_data_dir=user_data_dir,
        )
    except RuntimeError as exc:
        print(f"warn: automatic cursor login failed: {exc}")
        if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=True):
            return None
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
    print(f"env: {_env_path()}")
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")

    print_terminal_report(rows)
    path = write_csv_report(rows, _reports_dir())
    print(f"csv: {path}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    cursor_probe_warning = _maybe_capture_cursor_token(
        timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
        browser=getattr(args, "cursor_login_browser", "default"),
        user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
    )
    rows, warnings = _build_aggregates(args)
    print(f"env: {_env_path()}")
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    if warnings:
        for warning in warnings:
            print(f"warn: {warning}")

    print_terminal_report(rows)
    csv_path = write_csv_report(rows, _reports_dir())
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


def _import_config_plan(source_root: Path, runtime_paths, force: bool) -> tuple[list[tuple[Path, Path, str]], list[str]]:
    plan: list[tuple[Path, Path, str]] = []
    messages: list[str] = []
    source_targets = [
        (source_root / ".env", runtime_paths.env_path, ".env"),
        (source_root / "reports" / "runtime_state.json", runtime_paths.runtime_state_path, "runtime state"),
    ]

    for source_path, target_path, label in source_targets:
        if not source_path.exists():
            messages.append(f"missing: {label} source not found at {source_path}")
            continue
        if target_path.exists() and source_path.samefile(target_path):
            messages.append(f"skip: {label} source and target are the same file at {target_path}")
            continue
        if target_path.exists() and not force:
            messages.append(f"skip: {label} target already exists at {target_path}")
            continue
        action = "overwrite" if target_path.exists() else "copy"
        plan.append((source_path, target_path, action))
    return plan, messages


def cmd_import_config(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).resolve() if getattr(args, "source_root", None) else _repo_root()
    runtime_paths = resolve_active_runtime_paths()
    plan, messages = _import_config_plan(source_root, runtime_paths, force=args.force)
    source_exists = any(
        path.exists()
        for path in (
            source_root / ".env",
            source_root / "reports" / "runtime_state.json",
        )
    )

    for message in messages:
        print(message)

    if not plan:
        if source_exists:
            print("info: nothing imported")
            return 0
        print("error: no importable legacy config files found")
        return 1

    for source_path, target_path, action in plan:
        print(f"plan: {action} {source_path} -> {target_path}")

    if args.dry_run:
        print("dry-run: no files were written")
        return 0

    for source_path, target_path, _ in plan:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        print(f"imported: {target_path}")

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
    import_parser = sub.add_parser(
        "import-config",
        help="One-time migration of legacy .env and runtime state into runtime paths",
        description=(
            "One-time migration helper for moving legacy .env and reports/runtime_state.json "
            "into the active runtime paths."
        ),
    )
    import_parser.add_argument(
        "--from",
        dest="source_root",
        type=str,
        default=None,
        metavar="SOURCE_ROOT",
        help=(
            "Legacy repo root to import from, containing .env and reports/runtime_state.json; "
            "defaults to the current working directory"
        ),
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing any files",
    )
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing target files in the active runtime paths",
    )
    collect_parser = sub.add_parser("collect", help="Collect usage locally and output local report")
    collect_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help="Max wait time when opening browser login or capturing Cursor session token",
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
        help="Compatibility option; ignored when using system-browser login flow",
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
        help="Max wait time when opening browser login or capturing Cursor session token",
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
        help="Compatibility option; ignored when using system-browser login flow",
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
        "import-config": cmd_import_config,
        "collect": cmd_collect,
        "sync": cmd_sync,
    }
    return cmd_map[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
