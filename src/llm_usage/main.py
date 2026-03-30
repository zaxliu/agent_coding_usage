from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from llm_usage.aggregation import aggregate_events
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
    fetch_cursor_workos_id_from_local_browsers,
    fetch_cursor_session_token_via_browser,
    open_cursor_dashboard_login_page,
)
from llm_usage.env import load_dotenv, upsert_env_var
from llm_usage.identity import hash_source_host, hash_user
from llm_usage.interaction import confirm_save_temporary_remote, run_config_editor, select_remotes
from llm_usage.paths import read_bootstrap_env_text, resolve_active_runtime_paths, resolve_runtime_paths
from llm_usage.remotes import append_remote_to_env, build_remote_collectors, parse_remote_configs_from_env
from llm_usage.reporting import print_terminal_report, write_csv_report
from llm_usage.runtime_state import load_selected_remote_aliases, save_selected_remote_aliases
from llm_usage.sinks.feishu_bitable import (
    FeishuBitableClient,
    fetch_first_table_id,
    fetch_tenant_access_token,
)


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Preserve multi-line descriptions while showing argument defaults."""


DEFAULT_LOOKBACK_DAYS = 30


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


def _resolve_lookback_days(parsed_value: Optional[int]) -> int:
    if isinstance(parsed_value, int) and parsed_value > 0:
        return parsed_value
    try:
        return max(1, int(os.getenv("LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)) or str(DEFAULT_LOOKBACK_DAYS)))
    except ValueError:
        return DEFAULT_LOOKBACK_DAYS


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
                    f"LOOKBACK_DAYS={DEFAULT_LOOKBACK_DAYS}",
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


def cmd_whoami(_: argparse.Namespace) -> int:
    _load_runtime_env()
    username = _required_org_username()
    salt = _required_env("HASH_SALT")
    print(f"ORG_USERNAME: {username}")
    print(f"user_hash: {hash_user(username, salt)}")
    print(f"source_host_hash(local): {hash_source_host(username, 'local', salt)}")
    for config in parse_remote_configs_from_env():
        print(f"source_host_hash({config.alias.lower()}): {hash_source_host(username, config.source_label, salt)}")
    return 0


def cmd_config(_: argparse.Namespace) -> int:
    env_path = _ensure_env_file_exists()
    return run_config_editor(env_path)


def _capture_and_save_cursor_token(
    timeout_sec: int,
    browser: str,
    user_data_dir: str,
    *,
    login_mode: str = "auto",
) -> str:
    token = fetch_cursor_session_token_via_browser(
        timeout_sec=timeout_sec,
        browser=browser,
        user_data_dir=user_data_dir,
        login_mode=login_mode,
    )
    workos_id = fetch_cursor_workos_id_from_local_browsers(browser=browser) or ""
    _save_cursor_web_credentials(token, workos_id)
    return token


def _prompt_for_manual_cursor_token(browser: str, *, automatic_capture_failed: bool) -> Optional[str]:
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


def _resolve_cursor_login_mode(login_mode: str, browser: str) -> str:
    normalized_mode = (login_mode or "auto").strip().lower() or "auto"
    normalized_browser = (browser or "default").strip().lower()
    if normalized_mode != "auto":
        return normalized_mode
    if os.name == "nt" and normalized_browser in {"chrome", "chromium", "edge", "msedge"}:
        return "managed-profile"
    return "auto"


def _maybe_capture_cursor_token(
    timeout_sec: int,
    browser: str,
    user_data_dir: str,
    login_mode: str = "auto",
    lookback_days: Optional[int] = None,
) -> Optional[str]:
    _load_runtime_env()
    effective_login_mode = _resolve_cursor_login_mode(login_mode, browser)
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
            if effective_login_mode == "manual":
                if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=False):
                    return None
                print("warn: continuing with local cursor sources")
                return None
            try:
                _capture_and_save_cursor_token(
                    timeout_sec=timeout_sec,
                    browser=browser,
                    user_data_dir=user_data_dir,
                    login_mode=effective_login_mode,
                )
            except RuntimeError as exc:
                print(f"warn: {effective_login_mode} cursor login failed: {exc}")
                if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=True):
                    return None
                print("warn: continuing with local cursor sources")
                return None
            print("info: refreshed CURSOR_WEB_SESSION_TOKEN and saved to .env")
            return None
        return f"cursor dashboard probe failed with existing token: {msg}"

    resolved_lookback_days = _resolve_lookback_days(lookback_days)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=resolved_lookback_days)

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
    if effective_login_mode == "manual":
        if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=False):
            return None
        print("warn: continuing with local cursor sources")
        return None
    try:
        _capture_and_save_cursor_token(
            timeout_sec=timeout_sec,
            browser=browser,
            user_data_dir=user_data_dir,
            login_mode=effective_login_mode,
        )
    except RuntimeError as exc:
        print(f"warn: {effective_login_mode} cursor login failed: {exc}")
        if _prompt_for_manual_cursor_token(browser, automatic_capture_failed=True):
            return None
        print("warn: continuing with local cursor sources")
        return None
    if effective_login_mode == "managed-profile":
        print("info: refreshed CURSOR_WEB_SESSION_TOKEN and saved to .env")
    else:
        print("info: saved CURSOR_WEB_SESSION_TOKEN to .env")
    return None


def _resolve_remote_selection(
    args: argparse.Namespace,
    configured_remotes,
) -> tuple[list[str], list, dict[str, str]]:
    state_aliases = load_selected_remote_aliases(_runtime_state_path())
    configured_aliases = [config.alias for config in configured_remotes]
    if getattr(args, "ui", "auto") == "none":
        return [], [], {}
    if state_aliases:
        defaults = [alias for alias in state_aliases if alias in configured_aliases]
    else:
        defaults = list(configured_aliases)
    runtime_password: dict[str, str] = {}

    def _password_getter() -> Optional[str]:
        return next(iter(runtime_password.values()), None)

    def _password_setter(password: str) -> None:
        runtime_password["__current__"] = password

    result = select_remotes(
        configured_remotes,
        defaults,
        ui_mode=getattr(args, "ui", "auto"),
        password_getter=_password_getter,
        password_setter=_password_setter,
    )
    save_selected_remote_aliases(_runtime_state_path(), result.selected_aliases)
    resolved_passwords = dict(result.runtime_passwords or {})
    if "__current__" in runtime_password and not resolved_passwords:
        for config in result.temporary_remotes:
            if config.use_sshpass:
                resolved_passwords[config.alias] = runtime_password["__current__"]
    return result.selected_aliases, result.temporary_remotes, resolved_passwords


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
    lookback_days = _resolve_lookback_days(getattr(args, "lookback_days", None))
    local_source_host_hash = hash_source_host(username, "local", salt)

    configured_remotes = parse_remote_configs_from_env()
    selected_aliases, temporary_remotes, runtime_passwords = _resolve_remote_selection(args, configured_remotes)
    selected_configs = [config for config in configured_remotes if config.alias in selected_aliases]
    selected_configs.extend(temporary_remotes)

    collectors = _collectors(local_source_host_hash)
    collectors.extend(
        build_remote_collectors(
            selected_configs,
            username=username,
            salt=salt,
            runtime_passwords=runtime_passwords,
        )
    )
    events, warnings = _collect_all(lookback_days, collectors)
    user_hash = hash_user(username, salt)
    rows = aggregate_events(events, user_hash=user_hash, timezone_name=timezone_name)
    if temporary_remotes:
        _save_temporary_remotes(args, temporary_remotes, [config.alias for config in configured_remotes])
    return rows, warnings


def cmd_collect(args: argparse.Namespace) -> int:
    cursor_probe_warning = _maybe_capture_cursor_token(
        lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
        timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
        browser=getattr(args, "cursor_login_browser", "default"),
        user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
        login_mode=getattr(args, "cursor_login_mode", "auto"),
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
        lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
        timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
        browser=getattr(args, "cursor_login_browser", "default"),
        user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
        login_mode=getattr(args, "cursor_login_mode", "auto"),
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
    parser = argparse.ArgumentParser(
        description=(
            "Collect local and selected remote LLM usage, print a terminal summary, "
            "write reports/usage_report.csv, and optionally sync aggregated rows to Feishu.\n"
            "\n"
            "For configuration edits, use `llm-usage config` to open the interactive menu editor "
            "for the active runtime .env."
        ),
        epilog=(
            "Examples:\n"
            "  llm-usage doctor\n"
            "  llm-usage whoami\n"
            "  llm-usage config\n"
            "  llm-usage collect --ui auto\n"
            "  llm-usage sync --ui cli\n"
            "  llm-usage import-config --from /path/to/legacy/repo\n"
        ),
        formatter_class=_HelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "init",
        help="Initialize runtime .env and report folders",
        description=(
            "Create the active runtime .env and reports directory if they do not exist yet. "
            f"Use this once when bootstrapping a new checkout. The generated env defaults to "
            f"LOOKBACK_DAYS={DEFAULT_LOOKBACK_DAYS}."
        ),
        formatter_class=_HelpFormatter,
    )
    doctor_parser = sub.add_parser(
        "doctor",
        help="Check required config and available data sources",
        description=(
            "Validate identity settings, probe local collectors, and probe configured remote "
            "collectors without writing reports or syncing data."
        ),
        formatter_class=_HelpFormatter,
    )
    doctor_parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        metavar="LOOKBACK_DAYS",
        help=(
            "Collection window in days. Overrides LOOKBACK_DAYS from .env when provided; "
            f"defaults to {DEFAULT_LOOKBACK_DAYS} if neither is set."
        ),
    )
    sub.add_parser(
        "whoami",
        help="Show ORG_USERNAME, user_hash, and per-host source hashes",
        description=(
            "Show the current ORG_USERNAME, the derived user_hash, source_host_hash(local), "
            "and source_host_hash values for configured remotes."
        ),
        formatter_class=_HelpFormatter,
    )
    sub.add_parser(
        "config",
        help="Open the interactive menu editor for the active runtime .env",
        description=(
            "Open the interactive menu editor for the active runtime .env.\n"
            "\n"
            "This is the preferred menu-based flow for editing configuration, including remote "
            "hosts and other grouped settings, because changes stay in memory until you save."
        ),
        formatter_class=_HelpFormatter,
    )
    import_parser = sub.add_parser(
        "import-config",
        help="One-time migration of legacy .env and runtime state into runtime paths",
        description=(
            "One-time migration helper for moving legacy .env and reports/runtime_state.json "
            "into the active runtime paths."
        ),
        epilog=(
            "Examples:\n"
            "  llm-usage import-config --from /path/to/legacy/repo\n"
            "  llm-usage import-config --dry-run\n"
        ),
        formatter_class=_HelpFormatter,
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
        help="Show the import plan without copying any files",
    )
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing target files in the active runtime paths instead of stopping",
    )
    collect_parser = sub.add_parser(
        "collect",
        help="Collect usage, print terminal summary, and write the local CSV report",
        description=(
            "Collect usage from local and selected remote sources.\n"
            "\n"
            "Terminal output is grouped by date + tool + model for easier reading.\n"
            "The written reports/usage_report.csv keeps the original aggregated rows."
        ),
        epilog=(
            "Examples:\n"
            "  llm-usage collect --ui auto\n"
            "  llm-usage collect --ui cli --cursor-login-browser safari\n"
        ),
        formatter_class=_HelpFormatter,
    )
    collect_parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        metavar="LOOKBACK_DAYS",
        help=(
            "Collection window in days. Overrides LOOKBACK_DAYS from .env when provided; "
            f"defaults to {DEFAULT_LOOKBACK_DAYS} if neither is set."
        ),
    )
    collect_parser.add_argument(
        "--cursor-login-mode",
        default="auto",
        choices=["auto", "managed-profile", "manual"],
        help="Cursor dashboard login mode",
    )
    collect_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help=(
            "Maximum wait time when opening browser login or capturing the Cursor session token; "
            "increase this if you need more time to complete browser login"
        ),
    )
    collect_parser.add_argument(
        "--cursor-login-browser",
        choices=["default", "chrome", "edge", "safari", "firefox", "chromium", "msedge", "webkit"],
        default="default",
        help=(
            "Browser used for Cursor login capture; keep the default unless you need a specific "
            "installed browser for SSO or cookie access"
        ),
    )
    collect_parser.add_argument(
        "--cursor-login-user-data-dir",
        type=str,
        default="",
        help=(
            "Compatibility option for older browser-login flows; leave empty for the current "
            "system-browser flow"
        ),
    )
    collect_parser.add_argument(
        "--ui",
        choices=["auto", "tui", "cli", "none"],
        default="auto",
        help=(
            "Remote selection UI mode: auto picks the best interactive UI, tui forces the terminal "
            "selector, cli uses prompt-based selection, none disables remotes"
        ),
    )
    sync_parser = sub.add_parser(
        "sync",
        help="Collect usage and upsert aggregated rows to Feishu",
        description=(
            "Collect usage from local and selected remote sources, print a grouped terminal summary, "
            "write reports/usage_report.csv, then upsert the original aggregated rows to Feishu.\n"
            "\n"
            "Terminal output is grouped by date + tool + model for easier reading.\n"
            "CSV output and Feishu upserts keep the original aggregated rows."
        ),
        epilog=(
            "Examples:\n"
            "  llm-usage sync --ui auto\n"
            "  llm-usage sync --ui cli --cursor-login-browser chrome\n"
        ),
        formatter_class=_HelpFormatter,
    )
    sync_parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        metavar="LOOKBACK_DAYS",
        help=(
            "Collection window in days. Overrides LOOKBACK_DAYS from .env when provided; "
            f"defaults to {DEFAULT_LOOKBACK_DAYS} if neither is set."
        ),
    )
    sync_parser.add_argument(
        "--cursor-login-mode",
        default="auto",
        choices=["auto", "managed-profile", "manual"],
        help="Cursor dashboard login mode",
    )
    sync_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help=(
            "Maximum wait time when opening browser login or capturing the Cursor session token; "
            "increase this if you need more time to complete browser login"
        ),
    )
    sync_parser.add_argument(
        "--cursor-login-browser",
        choices=["default", "chrome", "edge", "safari", "firefox", "chromium", "msedge", "webkit"],
        default="default",
        help=(
            "Browser used for Cursor login capture; keep the default unless you need a specific "
            "installed browser for SSO or cookie access"
        ),
    )
    sync_parser.add_argument(
        "--cursor-login-user-data-dir",
        type=str,
        default="",
        help=(
            "Compatibility option for older browser-login flows; leave empty for the current "
            "system-browser flow"
        ),
    )
    sync_parser.add_argument(
        "--ui",
        choices=["auto", "tui", "cli", "none"],
        default="auto",
        help=(
            "Remote selection UI mode: auto picks the best interactive UI, tui forces the terminal "
            "selector, cli uses prompt-based selection, none disables remotes"
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cmd_map = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "whoami": cmd_whoami,
        "config": cmd_config,
        "import-config": cmd_import_config,
        "collect": cmd_collect,
        "sync": cmd_sync,
    }
    return cmd_map[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
