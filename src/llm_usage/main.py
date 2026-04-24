from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from llm_usage.aggregation import aggregate_events
from llm_usage.feishu_schema import feishu_schema_warnings
from llm_usage.feishu_targets import FeishuTargetConfig, resolve_feishu_targets_from_env, select_feishu_targets
from llm_usage.env import load_dotenv, load_env_document, upsert_env_var
from llm_usage.identity import hash_source_host, hash_user
from llm_usage.offline_bundle import OfflineBundleError, read_offline_bundle, write_offline_bundle
from llm_usage.paths import read_bootstrap_env_text, resolve_active_runtime_paths, resolve_runtime_paths
from llm_usage.remotes import RemoteHostConfig, append_remote_to_env, build_remote_collectors, is_ssh_auth_failure_message, parse_remote_configs_from_env, probe_remote_ssh
from llm_usage.reporting import print_terminal_report, write_csv_report
from llm_usage.runtime_preflight import validate_runtime_config
from llm_usage.runtime_state import load_selected_remote_aliases, save_selected_remote_aliases


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


ALL_TOOL_NAMES = ("claude_code", "codex", "copilot_cli", "copilot_vscode", "cline_vscode", "cursor", "opencode")


def _collectors(local_source_host_hash: str, skip_tools: Optional[set] = None) -> list[BaseCollector]:
    from llm_usage.collectors import (
        build_claude_collector,
        build_cline_vscode_collector,
        build_codex_collector,
        build_copilot_cli_collector,
        build_copilot_vscode_collector,
        build_cursor_collector,
        build_opencode_collector,
    )

    all_collectors = [
        build_claude_collector(source_host_hash=local_source_host_hash),
        build_codex_collector(source_host_hash=local_source_host_hash),
        build_copilot_cli_collector(source_host_hash=local_source_host_hash),
        build_copilot_vscode_collector(source_host_hash=local_source_host_hash),
        build_cline_vscode_collector(source_host_hash=local_source_host_hash),
        build_cursor_collector(source_host_hash=local_source_host_hash),
        build_opencode_collector(source_host_hash=local_source_host_hash),
    ]
    if not skip_tools:
        return all_collectors
    return [c for c in all_collectors if c.name not in skip_tools]


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


def _build_terminal_host_labels(username: str, salt: str, remote_configs: list[RemoteHostConfig]) -> dict[str, str]:
    labels: dict[str, str] = {hash_source_host(username, "local", salt): "local"}
    for config in remote_configs:
        labels[hash_source_host(username, config.source_label, salt)] = config.source_label
    return labels


def _terminal_host_labels_for_report() -> dict[str, str]:
    """Resolve Host column labels when identity env is present; otherwise empty (hash-prefix / local fallback)."""
    username = os.getenv("ORG_USERNAME", "").strip()
    salt = os.getenv("HASH_SALT", "").strip()
    if not username or not salt:
        return {}
    return _build_terminal_host_labels(username, salt, parse_remote_configs_from_env())


def _collect_all(
    lookback_days: int,
    collectors: list[BaseCollector],
    *,
    prompt_for_ssh_password: bool = True,
) -> tuple[list, list[str]]:
    import getpass

    from llm_usage.collectors.remote_file import RemoteFileCollector, SshAuthenticationError

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, lookback_days))

    events = []
    warnings: list[str] = []
    for collector in collectors:
        try:
            out = collector.collect(start=start, end=end)
        except SshAuthenticationError as exc:
            if not prompt_for_ssh_password:
                raise
            if not isinstance(collector, RemoteFileCollector):
                raise
            alias = getattr(collector, "source_name", "unknown")
            print(f"warn: SSH key 认证失败（{alias}）：{exc}")
            try:
                password = getpass.getpass(f"请输入 {alias} 的 SSH 密码（留空跳过）：")
            except (EOFError, KeyboardInterrupt):
                password = ""
            if not password.strip():
                warnings.append(f"remote[{alias}]: 跳过（未提供密码）")
                continue
            collector.ssh_password = password
            try:
                out = collector.collect(start=start, end=end)
            except Exception as retry_exc:
                warnings.append(f"remote[{alias}]: 密码重试失败：{retry_exc}")
                continue
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


def _tool_version() -> str:
    for package_name in ("llm-usage-horizon", "llm_usage_horizon"):
        try:
            return version(package_name)
        except PackageNotFoundError:
            continue
    return "dev"


def _default_bundle_output_path() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    return _reports_dir() / f"llm-usage-bundle-{timestamp}.zip"


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warn: {warning}")


def _add_feishu_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--feishu-target",
        action="append",
        default=[],
        metavar="NAME",
        help="Select a named Feishu target (repeatable). Default target is used when omitted.",
    )
    parser.add_argument(
        "--all-feishu-targets",
        action="store_true",
        help="Apply to every configured Feishu target (cannot combine with --feishu-target).",
    )


def _resolve_feishu_sync_selection(args: argparse.Namespace) -> list[FeishuTargetConfig]:
    targets = resolve_feishu_targets_from_env()
    names = list(getattr(args, "feishu_target", None) or [])
    all_t = bool(getattr(args, "all_feishu_targets", False))
    if all_t and names:
        raise RuntimeError("cannot combine --all-feishu-targets with --feishu-target")
    try:
        return select_feishu_targets(
            targets,
            selected_names=names if names else None,
            select_all=all_t,
            default_only=not all_t and len(names) == 0,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _execution_preflight(
    *,
    feishu_target: Optional[list[str]] = None,
    all_feishu_targets: bool = False,
) -> object:
    from llm_usage.interaction import BASIC_KEYS, FEISHU_KEYS, ConfigDraft

    _load_runtime_env()
    document = load_env_document(_env_path())
    draft = ConfigDraft.from_document(document)
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    return validate_runtime_config(
        basic={key: os.getenv(key, draft.values.get(key, "")) for key in BASIC_KEYS},
        feishu_default={key: os.getenv(key, draft.values.get(key, "")) for key in FEISHU_KEYS},
        feishu_targets=[
            {
                "name": target.name,
                "app_token": target.app_token,
                "table_id": target.table_id,
                "app_id": target.app_id,
                "app_secret": target.app_secret,
                "bot_token": target.bot_token,
            }
            for target in draft.feishu_named_targets
        ],
        mode="execution",
        selected_feishu_targets=list(feishu_target or []),
        all_feishu_targets=all_feishu_targets,
        is_interactive_tty=is_tty,
    )


def _basic_preflight() -> int:
    from llm_usage.interaction import BASIC_KEYS, ConfigDraft

    _load_runtime_env()
    document = load_env_document(_env_path())
    draft = ConfigDraft.from_document(document)
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    result = validate_runtime_config(
        basic={key: os.getenv(key, draft.values.get(key, "")) for key in BASIC_KEYS},
        feishu_default={},
        feishu_targets=[],
        mode="execution",
        is_interactive_tty=is_tty,
        skip_feishu=True,
    )
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}")
        return 1
    return 0


_FEISHU_CONNECTIVITY_TIMEOUT_SEC = 5


def _probe_feishu_connectivity(targets: list[FeishuTargetConfig]) -> Optional[str]:
    """Return an error message if Feishu API is unreachable, or None on success."""
    import requests as _requests

    from llm_usage.sinks.feishu_bitable import fetch_tenant_access_token

    # Pick the first target with app credentials for a real auth probe;
    # otherwise fall back to a lightweight HTTPS connectivity check.
    for target in targets:
        app_id = target.app_id.strip()
        app_secret = target.app_secret.strip()
        if app_id and app_secret:
            try:
                fetch_tenant_access_token(
                    app_id=app_id,
                    app_secret=app_secret,
                    request_timeout_sec=_FEISHU_CONNECTIVITY_TIMEOUT_SEC,
                )
                return None
            except (_requests.ConnectionError, _requests.Timeout, OSError) as exc:
                return f"feishu: cannot reach open.feishu.cn ({exc})"
            except RuntimeError:
                # Auth failed but network is reachable — connectivity is fine
                return None

    # No app credentials available; try a plain HTTPS GET to the API root.
    try:
        resp = _requests.get(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            timeout=_FEISHU_CONNECTIVITY_TIMEOUT_SEC,
        )
        # Any HTTP response (even 400) means the network is reachable.
        _ = resp.status_code
        return None
    except (_requests.ConnectionError, _requests.Timeout, OSError) as exc:
        return f"feishu: cannot reach open.feishu.cn ({exc})"


def _sync_execution_preflight(
    *,
    dry_run: bool = False,
    feishu_target: Optional[list[str]] = None,
    all_feishu_targets: bool = False,
) -> int:
    if dry_run:
        return _basic_preflight()
    preflight = _execution_preflight(feishu_target=feishu_target, all_feishu_targets=all_feishu_targets)
    if not preflight.ok:
        for error in preflight.errors:
            print(f"error: {error}")
        return 1
    connectivity_error = _probe_feishu_connectivity(preflight.resolved_feishu_targets)
    if connectivity_error:
        print(f"error: {connectivity_error}")
        return 1
    return 0


def _feishu_bot_token_for_target(target: FeishuTargetConfig) -> str:
    from llm_usage.sinks.feishu_bitable import fetch_tenant_access_token

    bot = target.bot_token.strip()
    if bot:
        return bot
    app_id = target.app_id.strip()
    app_secret = target.app_secret.strip()
    if not app_id or not app_secret:
        raise RuntimeError(f"missing Feishu app credentials for target {target.name!r}")
    return fetch_tenant_access_token(app_id=app_id, app_secret=app_secret)


def _feishu_table_id_for_target(target: FeishuTargetConfig, bot_token: str) -> str:
    from llm_usage.sinks.feishu_bitable import fetch_first_table_id

    table_id = target.table_id.strip()
    if table_id:
        return table_id
    app_token = target.app_token.strip()
    if not app_token:
        raise RuntimeError(f"missing FEISHU_APP_TOKEN for target {target.name!r}")
    tid = fetch_first_table_id(app_token=app_token, bot_token=bot_token)
    print(f"info: FEISHU_TABLE_ID empty for target {target.name!r}, auto-selected first table: {tid}")
    return tid


def run_feishu_doctor(args: argparse.Namespace) -> int:
    from llm_usage.sinks.feishu_bitable import FeishuBitableClient, fetch_bitable_field_type_map

    preflight = _execution_preflight(
        feishu_target=list(getattr(args, "feishu_target", []) or []),
        all_feishu_targets=bool(getattr(args, "all_feishu_targets", False)),
    )
    if not preflight.ok:
        raise RuntimeError("; ".join(preflight.errors))
    connectivity_error = _probe_feishu_connectivity(preflight.resolved_feishu_targets)
    if connectivity_error:
        raise RuntimeError(connectivity_error)
    targets = _resolve_feishu_sync_selection(args)
    if not targets:
        print("warn: no Feishu targets configured")
        return 0
    for target in targets:
        print(f"feishu[{target.name}]: checking...")
        try:
            bot_token = _feishu_bot_token_for_target(target)
            table_id = _feishu_table_id_for_target(target, bot_token)
            field_map = fetch_bitable_field_type_map(
                app_token=target.app_token.strip(),
                table_id=table_id,
                bot_token=bot_token,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"target {target.name}: {exc}") from exc
        warnings = feishu_schema_warnings(field_map)
        for msg in warnings:
            print(f"warn: {msg}")
        client = FeishuBitableClient(
            app_token=target.app_token.strip(),
            table_id=table_id,
            bot_token=bot_token,
        )
        try:
            client.probe_write_access()
        except RuntimeError as exc:
            raise RuntimeError(f"target {target.name}: {exc}") from exc
        print(f"feishu[{target.name}]: {'WARN' if warnings else 'OK'}")
    return 0


def ensure_feishu_schema_for_targets(*, dry_run: bool, targets: list[FeishuTargetConfig]) -> None:
    from llm_usage.sinks.feishu_bitable import FeishuBitableClient, create_missing_feishu_fields

    for target in targets:
        print(f"feishu[{target.name}]: ensuring bitable columns...")
        bot_token = _feishu_bot_token_for_target(target)
        table_id = _feishu_table_id_for_target(target, bot_token)
        client = FeishuBitableClient(
            app_token=target.app_token.strip(),
            table_id=table_id,
            bot_token=bot_token,
        )
        created = create_missing_feishu_fields(client, dry_run=dry_run)
        if created:
            action = "would create" if dry_run else "created"
            print(f"info: {action} columns for {target.name}: {', '.join(created)}")
        else:
            print(f"info: no missing columns for {target.name}")


def _sync_rows_to_single_feishu_target(rows: list, target: FeishuTargetConfig) -> int:
    from llm_usage.sinks.feishu_bitable import FeishuBitableClient

    bot_token = _feishu_bot_token_for_target(target)
    table_id = _feishu_table_id_for_target(target, bot_token)
    client = FeishuBitableClient(
        app_token=target.app_token.strip(),
        table_id=table_id,
        bot_token=bot_token,
    )
    result = client.upsert(rows)
    print(
        f"飞书同步完成（{target.name}）：新增={result.created} 更新={result.updated} 失败={result.failed}"
    )
    if result.warning_samples:
        for item in result.warning_samples:
            print(f"warn: {item}")
    if result.error_samples:
        print("飞书失败示例：")
        for item in result.error_samples:
            print(f"warn: {item}")
    return 0 if result.failed == 0 else 2


def _sync_rows_to_feishu_targets(
    rows: list,
    *,
    dry_run: bool = False,
    feishu_target: Optional[list[str]] = None,
    all_feishu_targets: bool = False,
) -> int:
    if dry_run:
        print("dry-run: bundle validated and upload skipped")
        return 0

    class _Args:
        pass

    sel = _Args()
    sel.feishu_target = feishu_target or []
    sel.all_feishu_targets = all_feishu_targets
    try:
        targets = _resolve_feishu_sync_selection(sel)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 1
    if not targets:
        print("error: no Feishu targets configured")
        return 1

    exit_code = 0
    for target in targets:
        code = _sync_rows_to_single_feishu_target(rows, target)
        if exit_code == 0 and code != 0:
            exit_code = code
    return exit_code


def _validate_sync_bundle_args(args: argparse.Namespace) -> None:
    conflicts: list[str] = []
    if getattr(args, "lookback_days", None) is not None:
        conflicts.append("--lookback-days")
    if getattr(args, "ui", "auto") != "auto":
        conflicts.append("--ui")
    if getattr(args, "cursor_login_timeout_sec", 600) != 600:
        conflicts.append("--cursor-login-timeout-sec")
    if getattr(args, "cursor_login_browser", "default") != "default":
        conflicts.append("--cursor-login-browser")
    if getattr(args, "cursor_login_user_data_dir", ""):
        conflicts.append("--cursor-login-user-data-dir")
    if getattr(args, "cursor_login_mode", "auto") != "auto":
        conflicts.append("--cursor-login-mode")
    if getattr(args, "skip", None):
        conflicts.append("--skip")
    if conflicts:
        joined = ", ".join(conflicts)
        raise RuntimeError(f"--from-bundle cannot be combined with online collection flags: {joined}")


def cmd_export_bundle(args: argparse.Namespace) -> int:
    skip_tools = set(getattr(args, "skip", None) or [])
    cursor_probe_warning: Optional[str] = None
    if "cursor" not in skip_tools:
        cursor_probe_warning = _maybe_capture_cursor_token(
            lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
            timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
            browser=getattr(args, "cursor_login_browser", "default"),
            user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
            login_mode=getattr(args, "cursor_login_mode", "auto"),
        )
    rows, warnings, _host_labels = _build_aggregates(args)
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    output_path = Path(getattr(args, "output", "") or _default_bundle_output_path()).expanduser()
    timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    bundle_path = write_offline_bundle(
        rows,
        output_path,
        warnings=warnings,
        timezone_name=timezone_name,
        lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
        tool_version=_tool_version(),
        include_csv=getattr(args, "include_csv", True),
    )
    print(f"bundle: {bundle_path}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    has_feishu_target_flags = bool(getattr(args, "feishu_target", None)) or getattr(args, "all_feishu_targets", False)
    if has_feishu_target_flags and not getattr(args, "feishu_bitable_schema", False):
        print("error: --feishu-target and --all-feishu-targets require --feishu-bitable-schema")
        return 2

    root = _repo_root()
    env_example = root / ".env.example"
    env_example.parent.mkdir(parents=True, exist_ok=True)
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
                    "CLINE_VSCODE_SESSION_PATHS=",
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
                    "REMOTE_SAMPLE_CLINE_VSCODE_SESSION_PATHS=",
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

    if getattr(args, "feishu_bitable_schema", False):
        _load_runtime_env()
        try:
            targets = _resolve_feishu_sync_selection(args)
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
        if not targets:
            print("warn: no Feishu targets configured")
            return 0
        ensure_feishu_schema_for_targets(dry_run=getattr(args, "dry_run", False), targets=targets)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    has_target_flags = bool(getattr(args, "feishu_target", None)) or getattr(args, "all_feishu_targets", False)
    if has_target_flags and not getattr(args, "feishu", False):
        print("error: --feishu-target and --all-feishu-targets require --feishu")
        return 2
    if getattr(args, "feishu", False):
        _load_runtime_env()
        try:
            return run_feishu_doctor(args)
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
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

    remote_configs = parse_remote_configs_from_env()
    runtime_passwords: dict[str, str] = {}
    for config in remote_configs:
        if config.ssh_jump_host:
            ok, message = probe_remote_ssh(config)
            if not ok and is_ssh_auth_failure_message(message):
                import getpass
                password = getpass.getpass(f"SSH password for {config.alias}: ")
                if password.strip():
                    runtime_passwords[config.alias] = password

    for collector in build_remote_collectors(remote_configs, username=username, salt=salt,
                                             runtime_passwords=runtime_passwords):
        try:
            ok, msg = collector.probe()
        except Exception as exc:
            ok, msg = False, str(exc)
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


def cmd_config(args: argparse.Namespace) -> int:
    from llm_usage.interaction import (
        feishu_config_add_target,
        feishu_config_delete_target,
        feishu_config_list_targets,
        feishu_config_set_target,
        feishu_config_setup_target,
        feishu_config_show_target,
        run_config_editor,
        run_feishu_setup_wizard,
    )

    env_path = _ensure_env_file_exists()
    shortcut_flags = sum(
        [
            bool(getattr(args, "list_feishu_targets", False)),
            getattr(args, "show_feishu_target", None) is not None,
            getattr(args, "add_feishu_target", None) is not None,
            getattr(args, "delete_feishu_target", None) is not None,
            getattr(args, "set_feishu_target", None) is not None,
            bool(getattr(args, "setup_feishu", False)),
        ]
    )
    if shortcut_flags > 1:
        print("error: use at most one Feishu config shortcut flag at a time")
        return 2
    if getattr(args, "list_feishu_targets", False):
        return feishu_config_list_targets(env_path, sys.stdout)
    if getattr(args, "show_feishu_target", None) is not None:
        return feishu_config_show_target(env_path, args.show_feishu_target, sys.stdout)
    if getattr(args, "add_feishu_target", None) is not None:
        return feishu_config_add_target(env_path, args.add_feishu_target, sys.stdout)
    if getattr(args, "delete_feishu_target", None) is not None:
        return feishu_config_delete_target(env_path, args.delete_feishu_target, sys.stdout)
    if getattr(args, "set_feishu_target", None) is not None:
        return feishu_config_set_target(
            env_path,
            args.set_feishu_target,
            sys.stdout,
            app_token=getattr(args, "set_feishu_app_token", None),
            table_id=getattr(args, "set_feishu_table_id", None),
            app_id=getattr(args, "set_feishu_app_id", None),
            app_secret=getattr(args, "set_feishu_app_secret", None),
            bot_token=getattr(args, "set_feishu_bot_token", None),
        )
    if getattr(args, "setup_feishu", False):
        app_token = getattr(args, "set_feishu_app_token", None)
        if app_token is not None:
            return feishu_config_setup_target(
                env_path,
                getattr(args, "setup_feishu_name", None),
                sys.stdout,
                app_token=app_token,
                table_id=getattr(args, "set_feishu_table_id", None),
                app_id=getattr(args, "set_feishu_app_id", None),
                app_secret=getattr(args, "set_feishu_app_secret", None),
                bot_token=getattr(args, "set_feishu_bot_token", None),
            )
        return run_feishu_setup_wizard(env_path, sys.stdout)
    from llm_usage.remotes import probe_remote_ssh

    return run_config_editor(env_path, remote_validator=probe_remote_ssh)


def _capture_and_save_cursor_token(
    timeout_sec: int,
    browser: str,
    user_data_dir: str,
    *,
    login_mode: str = "auto",
) -> str:
    from llm_usage.cursor_login import (
        fetch_cursor_session_token_via_browser,
        fetch_cursor_workos_id_from_local_browsers,
    )

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
    from llm_usage.cursor_login import open_cursor_dashboard_login_page

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
    from llm_usage.cursor_login import resolve_cursor_login_browser_choice

    normalized_mode = (login_mode or "auto").strip().lower() or "auto"
    normalized_browser = resolve_cursor_login_browser_choice(browser)
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
    from llm_usage.collectors import build_cursor_collector
    from llm_usage.cursor_login import resolve_cursor_login_browser_choice

    _load_runtime_env()
    effective_login_mode = _resolve_cursor_login_mode(login_mode, browser)
    capture_browser = browser
    if effective_login_mode == "managed-profile":
        capture_browser = resolve_cursor_login_browser_choice(browser)
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
                    browser=capture_browser,
                    user_data_dir=user_data_dir,
                    login_mode=effective_login_mode,
                )
            except RuntimeError as exc:
                print(f"warn: {effective_login_mode} cursor login failed: {exc}")
                if _prompt_for_manual_cursor_token(capture_browser, automatic_capture_failed=True):
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
            browser=capture_browser,
            user_data_dir=user_data_dir,
            login_mode=effective_login_mode,
        )
    except RuntimeError as exc:
        print(f"warn: {effective_login_mode} cursor login failed: {exc}")
        if _prompt_for_manual_cursor_token(capture_browser, automatic_capture_failed=True):
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
    from llm_usage.interaction import select_remotes

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
            resolved_passwords[config.alias] = runtime_password["__current__"]
            break
    return result.selected_aliases, result.temporary_remotes, resolved_passwords


def _save_temporary_remotes(args: argparse.Namespace, remotes: list, configured_aliases: list[str]) -> None:
    from llm_usage.interaction import confirm_save_temporary_remote

    for remote in remotes:
        if confirm_save_temporary_remote(remote, ui_mode=getattr(args, "ui", "auto")):
            alias = append_remote_to_env(_env_path(), remote, configured_aliases)
            configured_aliases.append(alias)
            print(f"info: 已将临时远端保存到 .env：{alias}")


def _build_aggregates(args: argparse.Namespace) -> tuple[list, list[str], dict[str, str]]:
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

    skip_tools = set(getattr(args, "skip", None) or [])
    collectors = _collectors(local_source_host_hash, skip_tools=skip_tools)
    collectors.extend(
        build_remote_collectors(
            selected_configs,
            username=username,
            salt=salt,
            runtime_passwords=runtime_passwords,
            skip_tools=skip_tools,
        )
    )
    events, warnings = _collect_all(lookback_days, collectors)
    user_hash = hash_user(username, salt)
    rows = aggregate_events(events, user_hash=user_hash, timezone_name=timezone_name)
    if temporary_remotes:
        _save_temporary_remotes(args, temporary_remotes, [config.alias for config in configured_remotes])
    host_labels = _build_terminal_host_labels(username, salt, selected_configs)
    return rows, warnings, host_labels


def cmd_collect(args: argparse.Namespace) -> int:
    preflight_code = _basic_preflight()
    if preflight_code != 0:
        return preflight_code
    skip_tools = set(getattr(args, "skip", None) or [])
    cursor_probe_warning: Optional[str] = None
    if "cursor" not in skip_tools:
        cursor_probe_warning = _maybe_capture_cursor_token(
            lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
            timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
            browser=getattr(args, "cursor_login_browser", "default"),
            user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
            login_mode=getattr(args, "cursor_login_mode", "auto"),
        )
    rows, warnings, host_labels = _build_aggregates(args)
    print(f"env: {_env_path()}")
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    if warnings:
        _print_warnings(warnings)

    print_terminal_report(rows, host_labels=host_labels)
    path = write_csv_report(rows, _reports_dir())
    print(f"csv: {path}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    if getattr(args, "from_bundle", None):
        _load_runtime_env()
        _validate_sync_bundle_args(args)
        preflight_code = _sync_execution_preflight(
            dry_run=getattr(args, "dry_run", False),
            feishu_target=getattr(args, "feishu_target", None) or [],
            all_feishu_targets=getattr(args, "all_feishu_targets", False),
        )
        if preflight_code != 0:
            return preflight_code
        rows, warnings, _manifest = read_offline_bundle(Path(args.from_bundle).expanduser())
        print(f"env: {_env_path()}")
        if warnings:
            _print_warnings(warnings)
        print_terminal_report(rows, host_labels=_terminal_host_labels_for_report())
        return _sync_rows_to_feishu_targets(
            rows,
            dry_run=getattr(args, "dry_run", False),
            feishu_target=getattr(args, "feishu_target", None) or [],
            all_feishu_targets=getattr(args, "all_feishu_targets", False),
        )

    preflight_code = _sync_execution_preflight(
        dry_run=getattr(args, "dry_run", False),
        feishu_target=getattr(args, "feishu_target", None) or [],
        all_feishu_targets=getattr(args, "all_feishu_targets", False),
    )
    if preflight_code != 0:
        return preflight_code

    skip_tools = set(getattr(args, "skip", None) or [])
    cursor_probe_warning: Optional[str] = None
    if "cursor" not in skip_tools:
        cursor_probe_warning = _maybe_capture_cursor_token(
            lookback_days=_resolve_lookback_days(getattr(args, "lookback_days", None)),
            timeout_sec=getattr(args, "cursor_login_timeout_sec", 600),
            browser=getattr(args, "cursor_login_browser", "default"),
            user_data_dir=getattr(args, "cursor_login_user_data_dir", ""),
            login_mode=getattr(args, "cursor_login_mode", "auto"),
        )
    rows, warnings, host_labels = _build_aggregates(args)
    print(f"env: {_env_path()}")
    if cursor_probe_warning and not any(row.tool == "cursor" for row in rows):
        warnings = [cursor_probe_warning, *warnings]
    if warnings:
        _print_warnings(warnings)

    print_terminal_report(rows, host_labels=host_labels)
    csv_path = write_csv_report(rows, _reports_dir())
    print(f"csv: {csv_path}")
    return _sync_rows_to_feishu_targets(
        rows,
        dry_run=getattr(args, "dry_run", False),
        feishu_target=getattr(args, "feishu_target", None) or [],
        all_feishu_targets=getattr(args, "all_feishu_targets", False),
    )


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
            "  llm-usage web --no-open\n"
            "  llm-usage export-bundle --output /tmp/offline.zip\n"
            "  llm-usage import-config --from /path/to/legacy/repo\n"
        ),
        formatter_class=_HelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser(
        "init",
        help="Initialize runtime .env and report folders",
        description=(
            "Create the active runtime .env and reports directory if they do not exist yet. "
            f"Use this once when bootstrapping a new checkout. The generated env defaults to "
            f"LOOKBACK_DAYS={DEFAULT_LOOKBACK_DAYS}."
        ),
        formatter_class=_HelpFormatter,
    )
    _add_feishu_target_arguments(init_parser)
    init_parser.add_argument(
        "--feishu-bitable-schema",
        action="store_true",
        help="Ensure required Bitable columns exist for selected Feishu targets (additive only)",
    )
    init_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --feishu-bitable-schema, skip API calls and do not create columns",
    )
    init_parser.set_defaults(feishu_bitable_schema=False, feishu_target=[], all_feishu_targets=False)
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
    doctor_parser.add_argument(
        "--feishu",
        action="store_true",
        help="Check Feishu Bitable connectivity, auth, and required columns for selected targets",
    )
    _add_feishu_target_arguments(doctor_parser)
    doctor_parser.set_defaults(feishu=False, feishu_target=[], all_feishu_targets=False)
    sub.add_parser(
        "whoami",
        help="Show ORG_USERNAME, user_hash, and per-host source hashes",
        description=(
            "Show the current ORG_USERNAME, the derived user_hash, source_host_hash(local), "
            "and source_host_hash values for configured remotes."
        ),
        formatter_class=_HelpFormatter,
    )
    config_parser = sub.add_parser(
        "config",
        help="Open the interactive menu editor for the active runtime .env",
        description=(
            "Open the interactive menu editor for the active runtime .env.\n"
            "\n"
            "This is the preferred menu-based flow for editing configuration, including remote "
            "hosts and other grouped settings, because changes stay in memory until you save.\n"
            "\n"
            "Non-interactive shortcuts list or edit Feishu targets without opening the full menu."
        ),
        formatter_class=_HelpFormatter,
    )
    config_parser.add_argument(
        "--list-feishu-targets",
        action="store_true",
        help="Print resolved Feishu target names (legacy default plus named targets) and exit",
    )
    config_parser.add_argument(
        "--show-feishu-target",
        metavar="NAME",
        default=None,
        help="Print resolved fields for a Feishu target (NAME may be default) and exit",
    )
    config_parser.add_argument(
        "--add-feishu-target",
        metavar="NAME",
        default=None,
        help="Append a named Feishu target to FEISHU_TARGETS and exit",
    )
    config_parser.add_argument(
        "--delete-feishu-target",
        metavar="NAME",
        default=None,
        help="Remove a named Feishu target and its FEISHU_<NAME>_* keys and exit",
    )
    config_parser.add_argument(
        "--set-feishu-target",
        metavar="NAME",
        default=None,
        help="Update env fields for a Feishu target (use NAME default for legacy keys)",
    )
    config_parser.add_argument(
        "--app-token",
        dest="set_feishu_app_token",
        default=None,
        help="With --set-feishu-target, set APP_TOKEN (or FEISHU_APP_TOKEN when NAME is default)",
    )
    config_parser.add_argument(
        "--table-id",
        dest="set_feishu_table_id",
        default=None,
        help="With --set-feishu-target, set TABLE_ID (or FEISHU_TABLE_ID when NAME is default)",
    )
    config_parser.add_argument(
        "--app-id",
        dest="set_feishu_app_id",
        default=None,
        help="With --set-feishu-target, set APP_ID (or FEISHU_APP_ID when NAME is default)",
    )
    config_parser.add_argument(
        "--app-secret",
        dest="set_feishu_app_secret",
        default=None,
        help="With --set-feishu-target, set APP_SECRET (or FEISHU_APP_SECRET when NAME is default)",
    )
    config_parser.add_argument(
        "--bot-token",
        dest="set_feishu_bot_token",
        default=None,
        help="With --set-feishu-target, set BOT_TOKEN (or FEISHU_BOT_TOKEN when NAME is default)",
    )
    config_parser.add_argument(
        "--setup-feishu",
        action="store_true",
        help=(
            "One-step Feishu target setup: create (if needed) and configure a target. "
            "Pass --app-token and auth fields for non-interactive mode, or omit for guided wizard."
        ),
    )
    config_parser.add_argument(
        "--name",
        dest="setup_feishu_name",
        default=None,
        metavar="NAME",
        help="With --setup-feishu, target name to configure (default: legacy default target)",
    )
    config_parser.set_defaults(
        list_feishu_targets=False,
        show_feishu_target=None,
        add_feishu_target=None,
        delete_feishu_target=None,
        set_feishu_target=None,
        setup_feishu=False,
        setup_feishu_name=None,
    )
    web_parser = sub.add_parser(
        "web",
        help="Start the local web console",
        description="Start the local web console for config, collect, sync, and result browsing.",
        formatter_class=_HelpFormatter,
    )
    web_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    web_parser.add_argument("--port", type=int, default=0, help="Port to bind; 0 selects a free port")
    web_parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
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
            "Terminal output is grouped by date + host + tool + model for easier reading.\n"
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
    collect_parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=ALL_TOOL_NAMES,
        metavar="TOOL",
        help="Skip collecting from the specified tool (can be repeated); choices: " + ", ".join(ALL_TOOL_NAMES),
    )
    export_parser = sub.add_parser(
        "export-bundle",
        help="Collect usage and write an offline bundle for later upload",
        description=(
            "Collect usage from local and selected remote sources, then write a single offline bundle "
            "file that can be copied to another machine and uploaded later via `llm-usage sync --from-bundle`."
        ),
        epilog=(
            "Examples:\n"
            "  llm-usage export-bundle\n"
            "  llm-usage export-bundle --output /tmp/offline.zip\n"
            "  llm-usage export-bundle --no-csv\n"
        ),
        formatter_class=_HelpFormatter,
    )
    export_parser.add_argument(
        "--output",
        type=str,
        default="",
        metavar="OUTPUT",
        help="Output zip path for the offline bundle; defaults to reports/llm-usage-bundle-<timestamp>.zip",
    )
    export_parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        metavar="LOOKBACK_DAYS",
        help=(
            "Collection window in days. Overrides LOOKBACK_DAYS from .env when provided; "
            f"defaults to {DEFAULT_LOOKBACK_DAYS} if neither is set."
        ),
    )
    export_parser.add_argument(
        "--cursor-login-mode",
        default="auto",
        choices=["auto", "managed-profile", "manual"],
        help="Cursor dashboard login mode",
    )
    export_parser.add_argument(
        "--cursor-login-timeout-sec",
        type=int,
        default=600,
        help=(
            "Maximum wait time when opening browser login or capturing the Cursor session token; "
            "increase this if you need more time to complete browser login"
        ),
    )
    export_parser.add_argument(
        "--cursor-login-browser",
        choices=["default", "chrome", "edge", "safari", "firefox", "chromium", "msedge", "webkit"],
        default="default",
        help=(
            "Browser used for Cursor login capture; keep the default unless you need a specific "
            "installed browser for SSO or cookie access"
        ),
    )
    export_parser.add_argument(
        "--cursor-login-user-data-dir",
        type=str,
        default="",
        help=(
            "Compatibility option for older browser-login flows; leave empty for the current "
            "system-browser flow"
        ),
    )
    export_parser.add_argument(
        "--ui",
        choices=["auto", "tui", "cli", "none"],
        default="auto",
        help=(
            "Remote selection UI mode: auto picks the best interactive UI, tui forces the terminal "
            "selector, cli uses prompt-based selection, none disables remotes"
        ),
    )
    export_parser.add_argument(
        "--no-csv",
        action="store_false",
        dest="include_csv",
        help="Do not include usage_report.csv inside the bundle",
    )
    export_parser.set_defaults(include_csv=True)
    export_parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=ALL_TOOL_NAMES,
        metavar="TOOL",
        help="Skip collecting from the specified tool (can be repeated); choices: " + ", ".join(ALL_TOOL_NAMES),
    )
    sync_parser = sub.add_parser(
        "sync",
        help="Collect usage and upsert aggregated rows to Feishu",
        description=(
            "Collect usage from local and selected remote sources, print a grouped terminal summary, "
            "write reports/usage_report.csv, then upsert the original aggregated rows to Feishu.\n"
            "\n"
            "Terminal output is grouped by date + host + tool + model for easier reading.\n"
            "CSV output and Feishu upserts keep the original aggregated rows.\n"
            "\n"
            "Use --from-bundle to upload rows from an offline bundle instead of collecting live data."
        ),
        epilog=(
            "Feishu target selection:\n"
            "  By default, sync uploads to the default target (legacy FEISHU_* keys).\n"
            "  Use --feishu-target NAME to select one or more named targets.\n"
            "  Use --all-feishu-targets to upload to every configured target.\n"
            "\n"
            "Examples:\n"
            "  llm-usage sync --ui auto\n"
            "  llm-usage sync --feishu-target team_b\n"
            "  llm-usage sync --all-feishu-targets --dry-run\n"
            "  llm-usage sync --ui cli --cursor-login-browser chrome\n"
            "  llm-usage sync --from-bundle /tmp/offline.zip --dry-run\n"
        ),
        formatter_class=_HelpFormatter,
    )
    sync_parser.add_argument(
        "--from-bundle",
        type=str,
        default="",
        metavar="FROM_BUNDLE",
        help="Read aggregated rows from an offline bundle instead of collecting from local/remote sources",
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
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate rows and print the terminal summary without uploading to Feishu",
    )
    _add_feishu_target_arguments(sync_parser)
    sync_parser.set_defaults(feishu_target=[], all_feishu_targets=False)
    sync_parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=ALL_TOOL_NAMES,
        metavar="TOOL",
        help="Skip collecting from the specified tool (can be repeated); choices: " + ", ".join(ALL_TOOL_NAMES),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "web":
        from llm_usage.web import cmd_web

        return cmd_web(args)
    cmd_map = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "whoami": cmd_whoami,
        "config": cmd_config,
        "import-config": cmd_import_config,
        "collect": cmd_collect,
        "export-bundle": cmd_export_bundle,
        "sync": cmd_sync,
    }
    try:
        return cmd_map[args.command](args)
    except (OfflineBundleError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
