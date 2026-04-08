from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from collections import defaultdict
import threading
import traceback
from types import SimpleNamespace
from typing import Any, Callable, Optional
from urllib.parse import urlparse
import webbrowser

from llm_usage.aggregation import aggregate_events
from llm_usage.collectors import BaseCollector
from llm_usage.collectors.remote_file import RemoteFileCollector, SshAuthenticationError
from llm_usage.env import load_env_document
from llm_usage.feishu_targets import normalize_feishu_target_name, resolve_feishu_targets_from_env, select_feishu_targets
from llm_usage.identity import hash_source_host, hash_user
from llm_usage.interaction import (
    ADVANCED_KEYS,
    BASIC_KEYS,
    CURSOR_KEYS,
    FEISHU_KEYS,
    ConfigDraft,
    FeishuTargetDraft,
    _save_config_draft,
)
from llm_usage.interaction_flow import RemotePromptRunner
from llm_usage.main import (
    _build_terminal_host_labels,
    _collect_all,
    _collectors,
    _env_path,
    _load_runtime_env,
    _reports_dir,
    _required_env,
    _required_org_username,
    _resolve_lookback_days,
    _runtime_state_path,
    _sync_rows_to_feishu_targets,
    _tool_version,
    run_feishu_doctor,
)
from llm_usage.paths import read_bootstrap_env_text
from llm_usage.remotes import RemoteDraft, build_remote_collectors, parse_remote_configs_from_env
from llm_usage.runtime_state import save_selected_remote_aliases
from llm_usage.runtime_preflight import ensure_runtime_bootstrap, validate_runtime_config


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _web_root() -> Path:
    return _repo_root() / "web"


def _json_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _overlay_runtime_env() -> dict[str, str]:
    document = load_env_document(_env_path())
    env_map: dict[str, str] = {}
    for line in document.lines:
        if line.kind == "entry" and line.key is not None and line.value is not None:
            env_map[line.key] = line.value
            os.environ[line.key] = line.value
    return env_map


def _serialize_remote(remote: Any) -> dict[str, Any]:
    return {
        "alias": remote.alias,
        "ssh_host": remote.ssh_host,
        "ssh_user": remote.ssh_user,
        "ssh_port": remote.ssh_port,
        "source_label": remote.source_label,
        "claude_log_paths": list(remote.claude_log_paths),
        "codex_log_paths": list(remote.codex_log_paths),
        "copilot_cli_log_paths": list(remote.copilot_cli_log_paths),
        "copilot_vscode_session_paths": list(remote.copilot_vscode_session_paths),
        "use_sshpass": bool(getattr(remote, "use_sshpass", False)),
    }


def _raw_env_entries(values: dict[str, str]) -> list[dict[str, str]]:
    managed = set(BASIC_KEYS + FEISHU_KEYS + CURSOR_KEYS + ADVANCED_KEYS + ["FEISHU_TARGETS"])
    out: list[dict[str, str]] = []
    for key in sorted(values):
        if key.startswith("FEISHU_") and key.endswith(("_APP_TOKEN", "_TABLE_ID", "_APP_ID", "_APP_SECRET", "_BOT_TOKEN")):
            middle = key[len("FEISHU_") :].rsplit("_", 1)[0]
            if middle and middle not in {"APP", "TABLE", "BOT"} and key not in FEISHU_KEYS:
                continue
        if key in managed:
            continue
        out.append({"key": key, "value": values[key]})
    return out


def _row_tokens(row: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(row.get("input_tokens_sum", 0) or 0),
        int(row.get("cache_tokens_sum", 0) or 0),
        int(row.get("output_tokens_sum", 0) or 0),
    )


def _total_tokens(input_tokens: int, cache_tokens: int, output_tokens: int) -> int:
    return input_tokens + cache_tokens + output_tokens


def _empty_breakdown_item(name: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "input_tokens_sum": 0,
        "cache_tokens_sum": 0,
        "output_tokens_sum": 0,
        "total_tokens": 0,
        "row_count": 0,
    }


def _top_summary_item(item: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not item:
        return {"name": "", "total_tokens": 0}
    return {"name": item.get("name", ""), "total_tokens": item.get("total_tokens", 0)}


def _sorted_breakdown_rows(buckets: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, totals in buckets.items():
        item = {
            "name": name,
            "input_tokens_sum": totals["input_tokens_sum"],
            "cache_tokens_sum": totals["cache_tokens_sum"],
            "output_tokens_sum": totals["output_tokens_sum"],
            "total_tokens": _total_tokens(
                totals["input_tokens_sum"], totals["cache_tokens_sum"], totals["output_tokens_sum"]
            ),
            "row_count": totals["row_count"],
        }
        items.append(item)
    items.sort(key=lambda item: (-item["total_tokens"], item["name"]))
    return items


def _dashboard_payload_from_rows(rows: list[dict[str, Any]], csv_path: Path, generated_at: Optional[str]) -> dict[str, Any]:
    totals = {
        "rows": 0,
        "input_tokens_sum": 0,
        "cache_tokens_sum": 0,
        "output_tokens_sum": 0,
    }
    timeseries_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "input_tokens_sum": 0,
            "cache_tokens_sum": 0,
            "output_tokens_sum": 0,
            "row_count": 0,
        }
    )
    tool_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "input_tokens_sum": 0,
            "cache_tokens_sum": 0,
            "output_tokens_sum": 0,
            "row_count": 0,
        }
    )
    model_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "input_tokens_sum": 0,
            "cache_tokens_sum": 0,
            "output_tokens_sum": 0,
            "row_count": 0,
        }
    )
    table_buckets: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for row in rows:
        date_local = str(row.get("date_local", "") or "")
        source_host_hash = str(row.get("source_host_hash", "") or "")
        tool = str(row.get("tool", "") or "")
        model = str(row.get("model", "") or "")
        input_tokens, cache_tokens, output_tokens = _row_tokens(row)
        row_total = _total_tokens(input_tokens, cache_tokens, output_tokens)

        totals["rows"] += 1
        totals["input_tokens_sum"] += input_tokens
        totals["cache_tokens_sum"] += cache_tokens
        totals["output_tokens_sum"] += output_tokens

        day_bucket = timeseries_buckets[date_local]
        day_bucket["input_tokens_sum"] += input_tokens
        day_bucket["cache_tokens_sum"] += cache_tokens
        day_bucket["output_tokens_sum"] += output_tokens
        day_bucket["row_count"] += 1

        tool_bucket = tool_buckets[tool]
        tool_bucket["input_tokens_sum"] += input_tokens
        tool_bucket["cache_tokens_sum"] += cache_tokens
        tool_bucket["output_tokens_sum"] += output_tokens
        tool_bucket["row_count"] += 1

        model_bucket = model_buckets[model]
        model_bucket["input_tokens_sum"] += input_tokens
        model_bucket["cache_tokens_sum"] += cache_tokens
        model_bucket["output_tokens_sum"] += output_tokens
        model_bucket["row_count"] += 1

        key = (date_local, source_host_hash, tool, model)
        bucket = table_buckets.setdefault(
            key,
            {
                "date_local": date_local,
                "source_host_hash": source_host_hash,
                "tool": tool,
                "model": model,
                "input_tokens_sum": 0,
                "cache_tokens_sum": 0,
                "output_tokens_sum": 0,
                "total_tokens": 0,
                "row_count": 0,
            },
        )
        bucket["input_tokens_sum"] += input_tokens
        bucket["cache_tokens_sum"] += cache_tokens
        bucket["output_tokens_sum"] += output_tokens
        bucket["total_tokens"] += row_total
        bucket["row_count"] += 1

    timeseries = []
    for date_local in sorted(timeseries_buckets):
        item = timeseries_buckets[date_local]
        timeseries.append(
            {
                "date_local": date_local,
                "input_tokens_sum": item["input_tokens_sum"],
                "cache_tokens_sum": item["cache_tokens_sum"],
                "output_tokens_sum": item["output_tokens_sum"],
                "total_tokens": _total_tokens(item["input_tokens_sum"], item["cache_tokens_sum"], item["output_tokens_sum"]),
                "row_count": item["row_count"],
            }
        )

    tool_breakdown = _sorted_breakdown_rows(tool_buckets)
    model_breakdown = _sorted_breakdown_rows(model_buckets)
    table_rows = sorted(
        table_buckets.values(),
        key=lambda item: (item["date_local"], item["source_host_hash"], item["tool"], item["model"]),
    )

    summary = {
        "totals": {
            **totals,
            "total_tokens": _total_tokens(
                totals["input_tokens_sum"], totals["cache_tokens_sum"], totals["output_tokens_sum"]
            ),
        },
        "active_days": len(timeseries),
        "top_tool": _top_summary_item(tool_breakdown[0] if tool_breakdown else None),
        "top_model": _top_summary_item(model_breakdown[0] if model_breakdown else None),
        "generated_at": generated_at,
    }

    return {
        "summary": summary,
        "timeseries": timeseries,
        "breakdowns": {"tools": tool_breakdown, "models": model_breakdown},
        "table_rows": table_rows,
        "warnings": [],
        "rows": rows,
        "csv_path": str(csv_path),
        "generated_at": generated_at,
        "ok": True,
    }


def load_config_payload() -> dict[str, Any]:
    bootstrap = _bootstrap_runtime_for_web()
    document = load_env_document(_env_path())
    draft = ConfigDraft.from_document(document)
    return {
        "basic": {key: draft.values.get(key, "") for key in BASIC_KEYS},
        "cursor": {key: draft.values.get(key, "") for key in CURSOR_KEYS},
        "feishu_default": {key: draft.values.get(key, "") for key in FEISHU_KEYS},
        "feishu_targets": [asdict(target) for target in draft.feishu_named_targets],
        "remotes": [_serialize_remote(remote) for remote in draft.remotes],
        "raw_env": _raw_env_entries(draft.values),
        "reports_dir": str(_reports_dir()),
        "env_path": str(_env_path()),
        "bootstrap_applied": bootstrap["bootstrap_applied"],
        "auto_fixes": bootstrap["auto_fixes"],
    }


def _bootstrap_runtime_for_web() -> dict[str, Any]:
    result = ensure_runtime_bootstrap(
        env_path=_env_path(),
        reports_dir=_reports_dir(),
        bootstrap_text=read_bootstrap_env_text(),
    )
    return {
        "bootstrap_applied": result.bootstrap_applied,
        "auto_fixes": result.auto_fixes,
        "created_env": result.created_env,
        "created_reports": result.created_reports,
    }


def _validate_remote_payload(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_aliases: set[str] = set()
    for remote in payload.get("remotes", []) or []:
        alias = str(remote.get("alias", "")).strip().upper()
        ssh_host = str(remote.get("ssh_host", "")).strip()
        ssh_user = str(remote.get("ssh_user", "")).strip()
        if not alias:
            errors.append("Remote alias is required")
        elif alias in seen_aliases:
            errors.append(f"duplicate remote alias: {alias}")
        else:
            seen_aliases.add(alias)
        if not ssh_host:
            errors.append(f"remote {alias or '<new>'}: SSH host is required")
        if not ssh_user:
            errors.append(f"remote {alias or '<new>'}: SSH user is required")
        try:
            port = int(remote.get("ssh_port", 22))
            if port <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"remote {alias or '<new>'}: SSH port must be a positive integer")
    basic = payload.get("basic", {}) or {}
    if basic.get("ORG_USERNAME", "") and not basic.get("HASH_SALT", ""):
        warnings.append("HASH_SALT is empty; collect/sync will fail until set")
    return errors, warnings


def validate_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    bootstrap = _bootstrap_runtime_for_web()
    errors: list[str] = []
    warnings: list[str] = []

    for target in payload.get("feishu_targets", []) or []:
        name = str(target.get("name", "")).strip()
        if not name:
            errors.append("Feishu target name is required")
            continue
        try:
            normalize_feishu_target_name(name)
        except RuntimeError as exc:
            errors.append(str(exc))

    remote_errors, remote_warnings = _validate_remote_payload(payload)
    validation = validate_runtime_config(
        basic=payload.get("basic", {}) or {},
        feishu_default=payload.get("feishu_default", {}) or {},
        feishu_targets=payload.get("feishu_targets", []) or [],
        mode="config_save",
    )
    errors.extend(remote_errors)
    errors.extend(validation.errors)
    warnings.extend(remote_warnings)
    warnings.extend(validation.warnings)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "auto_fixes": bootstrap["auto_fixes"],
        "bootstrap_applied": bootstrap["bootstrap_applied"],
    }


def save_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_config_payload(payload)
    if not validation["ok"]:
        return {**validation, "saved": False}

    document = load_env_document(_env_path())
    values: dict[str, str] = {}
    for item in payload.get("raw_env", []) or []:
        key = str(item.get("key", "")).strip().upper()
        if key and not key.startswith("REMOTE_"):
            values[key] = str(item.get("value", ""))
    for group_name, keys in (
        ("basic", BASIC_KEYS),
        ("cursor", CURSOR_KEYS),
        ("feishu_default", FEISHU_KEYS),
    ):
        group = payload.get(group_name, {}) or {}
        for key in keys:
            values[key] = str(group.get(key, ""))

    remotes = [
        RemoteDraft(
            alias=str(remote.get("alias", "")).strip().upper(),
            ssh_host=str(remote.get("ssh_host", "")).strip(),
            ssh_user=str(remote.get("ssh_user", "")).strip(),
            ssh_port=int(remote.get("ssh_port", 22) or 22),
            source_label=str(remote.get("source_label", "")).strip() or f"{remote.get('ssh_user', '')}@{remote.get('ssh_host', '')}",
            claude_log_paths=list(remote.get("claude_log_paths", []) or []),
            codex_log_paths=list(remote.get("codex_log_paths", []) or []),
            copilot_cli_log_paths=list(remote.get("copilot_cli_log_paths", []) or []),
            copilot_vscode_session_paths=list(remote.get("copilot_vscode_session_paths", []) or []),
            use_sshpass=bool(remote.get("use_sshpass", False)),
        )
        for remote in payload.get("remotes", []) or []
    ]
    feishu_named_targets = [
        FeishuTargetDraft(
            name=normalize_feishu_target_name(str(target.get("name", "")).strip()),
            app_token=str(target.get("app_token", "")),
            table_id=str(target.get("table_id", "")),
            app_id=str(target.get("app_id", "")),
            app_secret=str(target.get("app_secret", "")),
            bot_token=str(target.get("bot_token", "")),
        )
        for target in payload.get("feishu_targets", []) or []
    ]
    draft = ConfigDraft(
        document=document,
        values=values,
        remotes=remotes,
        feishu_named_targets=feishu_named_targets,
        feishu_named_targets_parse_ok=True,
        dirty=True,
    )
    _save_config_draft(_env_path(), draft)
    _overlay_runtime_env()
    return {**validation, "ok": True, "errors": [], "saved": True}


def load_latest_results() -> dict[str, Any]:
    csv_path = _reports_dir() / "usage_report.csv"
    if not csv_path.exists():
        return _dashboard_payload_from_rows([], csv_path, None)
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(dict(row))
    generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=timezone.utc).isoformat()
    return _dashboard_payload_from_rows(rows, csv_path, generated_at)


def _resolve_feishu_targets_summary(names: list[str], select_all: bool) -> list[dict[str, str]]:
    targets = select_feishu_targets(
        resolve_feishu_targets_from_env(os.environ),
        selected_names=names,
        select_all=select_all,
        default_only=not names and not select_all,
    )
    return [{"name": target.name, "app_token": target.app_token, "table_id": target.table_id} for target in targets]


def _build_aggregates_for_web(
    payload: dict[str, Any],
    *,
    runtime_passwords: Optional[dict[str, str]] = None,
) -> tuple[list, list[str], dict[str, str]]:
    _load_runtime_env()
    _overlay_runtime_env()
    username = _required_org_username()
    salt = _required_env("HASH_SALT")
    timezone_name = os.getenv("TIMEZONE", "Asia/Shanghai")
    lookback_days = _resolve_lookback_days(payload.get("lookback_days"))
    local_source_host_hash = hash_source_host(username, "local", salt)
    configured_remotes = parse_remote_configs_from_env()
    selected_aliases = [str(item).strip().upper() for item in (payload.get("selected_remotes") or []) if str(item).strip()]
    if selected_aliases:
        selected_configs = [config for config in configured_remotes if config.alias in selected_aliases]
        save_selected_remote_aliases(_runtime_state_path(), [config.alias for config in selected_configs])
    else:
        selected_configs = configured_remotes
    collectors: list[BaseCollector] = _collectors(local_source_host_hash)
    collectors.extend(build_remote_collectors(selected_configs, username=username, salt=salt, runtime_passwords=runtime_passwords))
    events, warnings = _collect_all(lookback_days, collectors)
    rows = aggregate_events(events, user_hash=hash_user(username, salt), timezone_name=timezone_name)
    host_labels = _build_terminal_host_labels(username, salt, selected_configs)
    return rows, warnings, host_labels


class _JobNeedsInput(RuntimeError):
    def __init__(self, input_request: dict[str, Any], resume_handler: Callable[[str], dict[str, Any]]) -> None:
        super().__init__("job needs input")
        self.input_request = input_request
        self.resume_handler = resume_handler


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[], dict[str, Any]]] = {}
        self._resume_handlers: dict[str, Callable[[str], dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._write_job_id: Optional[str] = None

    def _make_job(self, job_type: str, *, write_operation: bool = False) -> dict[str, Any]:
        job_id = f"job-{len(self._jobs) + 1}-{int(datetime.now().timestamp() * 1000)}"
        return {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "created_at": _json_now(),
            "updated_at": _json_now(),
            "logs": [],
            "result": None,
            "error": None,
            "input_request": None,
            "write_operation": write_operation,
        }

    def _run_handler(self, job_id: str, handler: Callable[[], dict[str, Any]]) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["updated_at"] = _json_now()
        try:
            from contextlib import redirect_stderr, redirect_stdout

            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = handler()
            logs = [line for line in (stdout.getvalue() + stderr.getvalue()).splitlines() if line.strip()]
            with self._lock:
                self._jobs[job_id]["status"] = "succeeded"
                self._jobs[job_id]["updated_at"] = _json_now()
                self._jobs[job_id]["logs"] = logs
                self._jobs[job_id]["result"] = result
                self._jobs[job_id]["error"] = None
                self._jobs[job_id]["input_request"] = None
                self._handlers.pop(job_id, None)
                self._resume_handlers.pop(job_id, None)
                if self._write_job_id == job_id:
                    self._write_job_id = None
        except _JobNeedsInput as exc:
            logs = [line for line in (stdout.getvalue() + stderr.getvalue()).splitlines() if line.strip()]
            with self._lock:
                self._jobs[job_id]["status"] = "needs_input"
                self._jobs[job_id]["updated_at"] = _json_now()
                self._jobs[job_id]["logs"] = logs
                self._jobs[job_id]["input_request"] = exc.input_request
                self._jobs[job_id]["error"] = None
                self._jobs[job_id]["result"] = None
                self._handlers.pop(job_id, None)
                self._resume_handlers[job_id] = exc.resume_handler
        except Exception as exc:  # pragma: no cover - defensive
            logs = [line for line in (stdout.getvalue() + stderr.getvalue()).splitlines() if line.strip()]
            logs.extend(traceback.format_exc().splitlines())
            with self._lock:
                self._jobs[job_id]["status"] = "failed"
                self._jobs[job_id]["updated_at"] = _json_now()
                self._jobs[job_id]["logs"] = logs
                self._jobs[job_id]["error"] = str(exc)
                self._jobs[job_id]["input_request"] = None
                self._handlers.pop(job_id, None)
                self._resume_handlers.pop(job_id, None)
                if self._write_job_id == job_id:
                    self._write_job_id = None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)]

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def start(self, job_type: str, handler: Callable[[], dict[str, Any]], *, write_operation: bool = False) -> dict[str, Any]:
        with self._lock:
            if write_operation and self._write_job_id:
                raise RuntimeError("another write operation is already running")
            job = self._make_job(job_type, write_operation=write_operation)
            job_id = job["id"]
            self._jobs[job_id] = job
            self._handlers[job_id] = handler
            if write_operation:
                self._write_job_id = job_id

        thread = threading.Thread(target=lambda: self._run_handler(job_id, handler), daemon=True)
        thread.start()
        return self.get_job(job_id) or {"id": job_id}

    def create_needs_input(
        self,
        job_type: str,
        input_request: dict[str, Any],
        resume_handler: Callable[[str], dict[str, Any]],
        *,
        write_operation: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if write_operation and self._write_job_id:
                raise RuntimeError("another write operation is already running")
            job = self._make_job(job_type, write_operation=write_operation)
            job_id = job["id"]
            job["status"] = "needs_input"
            job["input_request"] = input_request
            self._jobs[job_id] = job
            self._resume_handlers[job_id] = resume_handler
            if write_operation:
                self._write_job_id = job_id
        return self.get_job(job_id) or {"id": job_id}

    def submit_input(self, job_id: str, value: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise RuntimeError("job not found")
            if job.get("status") != "needs_input":
                raise RuntimeError("job is not waiting for input")
            resume_handler = self._resume_handlers.get(job_id)
            if resume_handler is None:
                raise RuntimeError("job does not accept input")
            job["status"] = "queued"
            job["updated_at"] = _json_now()

        thread = threading.Thread(
            target=lambda: self._run_handler(job_id, lambda: resume_handler(value)),
            daemon=True,
        )
        thread.start()
        return self.get_job(job_id) or {"id": job_id}


class WebService:
    def __init__(self) -> None:
        self.jobs = JobManager()
        self._runtime_credentials: dict[str, str] = {}
        self._runtime_lock = threading.Lock()

    def runtime_payload(self) -> dict[str, Any]:
        _load_runtime_env()
        _overlay_runtime_env()
        return {
            "backend": "python",
            "version": _tool_version(),
            "env_path": str(_env_path()),
            "reports_dir": str(_reports_dir()),
            "base_url": None,
            "capabilities": {"config": True, "collect": True, "sync": True, "doctor": True},
        }

    def _runtime_passwords_for(self, selected_aliases: list[str]) -> dict[str, str]:
        with self._runtime_lock:
            return {alias: self._runtime_credentials[alias] for alias in selected_aliases if alias in self._runtime_credentials}

    def _remember_runtime_password(self, alias: str, value: str) -> None:
        with self._runtime_lock:
            self._runtime_credentials[alias] = value

    def _missing_runtime_password_request(self, selected_configs: list[Any]) -> Optional[dict[str, Any]]:
        with self._runtime_lock:
            for config in selected_configs:
                if not bool(getattr(config, "use_sshpass", False)):
                    continue
                if self._runtime_credentials.get(config.alias, "").strip():
                    continue
                return {
                    "kind": "ssh_password",
                    "remote_alias": config.alias,
                    "message": f"Provide the SSH password for {config.alias}. It will be cached in memory for this session only.",
                    "cache_scope": "session",
                }
        return None

    def _selected_remote_configs(self, payload: dict[str, Any]) -> list[Any]:
        _load_runtime_env()
        _overlay_runtime_env()
        configured_remotes = parse_remote_configs_from_env()
        selected_aliases = [str(item).strip().upper() for item in (payload.get("selected_remotes") or []) if str(item).strip()]
        if not selected_aliases:
            return configured_remotes
        selected_aliases_set = set(selected_aliases)
        return [config for config in configured_remotes if config.alias in selected_aliases_set]

    def _wrap_with_ssh_auth_fallback(
        self,
        operation: Callable[[], dict[str, Any]],
        selected_configs: list[Any],
    ) -> Callable[[], dict[str, Any]]:
        """Wrap a handler to catch SSH auth failures and prompt for password via the frontend."""

        def handler() -> dict[str, Any]:
            try:
                return operation()
            except SshAuthenticationError as exc:
                alias = None
                for config in selected_configs:
                    if config.alias.lower() == exc.source_name:
                        alias = config.alias
                        break
                if alias is None:
                    raise

                input_request = {
                    "kind": "ssh_password",
                    "remote_alias": alias,
                    "message": f"SSH key 认证失败（{alias}）。请提供 SSH 密码重试，密码仅缓存在本次会话中。",
                    "cache_scope": "session",
                }

                def resume_handler(value: str) -> dict[str, Any]:
                    self._remember_runtime_password(alias, value)
                    return operation()

                raise _JobNeedsInput(input_request, resume_handler)

        return handler

    def _run_collect_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime_passwords = self._runtime_passwords_for([config.alias for config in self._selected_remote_configs(payload)])
        rows, warnings, host_labels = _build_aggregates_for_web(payload, runtime_passwords=runtime_passwords)
        csv_path = _reports_dir() / "usage_report.csv"
        from llm_usage.reporting import write_csv_report

        write_csv_report(rows, _reports_dir())
        return {
            "row_count": len(rows),
            "warnings": warnings,
            "host_labels": host_labels,
            "csv_path": str(csv_path),
        }

    def _run_sync_preview_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime_passwords = self._runtime_passwords_for([config.alias for config in self._selected_remote_configs(payload)])
        rows, warnings, _host_labels = _build_aggregates_for_web(payload, runtime_passwords=runtime_passwords)
        names = [str(item).strip() for item in (payload.get("feishu_targets") or []) if str(item).strip()]
        return {
            "row_count": len(rows),
            "warnings": warnings,
            "targets": _resolve_feishu_targets_summary(names, bool(payload.get("all_feishu_targets", False))),
        }

    def _run_sync_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime_passwords = self._runtime_passwords_for([config.alias for config in self._selected_remote_configs(payload)])
        rows, warnings, _host_labels = _build_aggregates_for_web(payload, runtime_passwords=runtime_passwords)
        from llm_usage.reporting import write_csv_report

        csv_path = write_csv_report(rows, _reports_dir())
        exit_code = _sync_rows_to_feishu_targets(
            rows,
            dry_run=False,
            feishu_target=[str(item).strip() for item in (payload.get("feishu_targets") or []) if str(item).strip()],
            all_feishu_targets=bool(payload.get("all_feishu_targets", False)),
        )
        return {"row_count": len(rows), "warnings": warnings, "csv_path": str(csv_path), "exit_code": exit_code}

    def _collect_or_pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_configs = self._selected_remote_configs(payload)
        input_request = self._missing_runtime_password_request(selected_configs)
        if input_request:
            alias = str(input_request["remote_alias"])

            def resume_handler(value: str) -> dict[str, Any]:
                self._remember_runtime_password(alias, value)
                return self._run_collect_operation(payload)

            return self.jobs.create_needs_input("collect", input_request, resume_handler, write_operation=True)
        handler = self._wrap_with_ssh_auth_fallback(
            lambda: self._run_collect_operation(payload), selected_configs,
        )
        return self.jobs.start("collect", handler, write_operation=True)

    def _sync_or_pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_configs = self._selected_remote_configs(payload)
        input_request = self._missing_runtime_password_request(selected_configs)
        if input_request:
            alias = str(input_request["remote_alias"])

            def resume_handler(value: str) -> dict[str, Any]:
                self._remember_runtime_password(alias, value)
                return self._run_sync_operation(payload)

            return self.jobs.create_needs_input("sync", input_request, resume_handler, write_operation=True)
        handler = self._wrap_with_ssh_auth_fallback(
            lambda: self._run_sync_operation(payload), selected_configs,
        )
        return self.jobs.start("sync", handler, write_operation=True)

    def _sync_preview_or_pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_configs = self._selected_remote_configs(payload)
        input_request = self._missing_runtime_password_request(selected_configs)
        if input_request:
            alias = str(input_request["remote_alias"])

            def resume_handler(value: str) -> dict[str, Any]:
                self._remember_runtime_password(alias, value)
                return self._run_sync_preview_operation(payload)

            return self.jobs.create_needs_input("sync_preview", input_request, resume_handler)
        handler = self._wrap_with_ssh_auth_fallback(
            lambda: self._run_sync_preview_operation(payload), selected_configs,
        )
        return self.jobs.start("sync_preview", handler)

    def _start_remote_setup_flow(self) -> dict[str, Any]:
        _load_runtime_env()
        _overlay_runtime_env()
        runner = RemotePromptRunner(existing_aliases=[config.alias for config in parse_remote_configs_from_env()])
        request = runner.next_request()
        if request is None:
            return {"remote_setup": asdict(runner.state)}

        def resume_handler(value: str) -> dict[str, Any]:
            current_request = runner.next_request()
            if current_request is None:
                return {"remote_setup": asdict(runner.state)}
            if not runner.apply_input(value):
                raise _JobNeedsInput(asdict(current_request), resume_handler)
            next_request = runner.next_request()
            if next_request is not None:
                raise _JobNeedsInput(asdict(next_request), resume_handler)
            return {"remote_setup": asdict(runner.state)}

        return self.jobs.create_needs_input("remote_setup", asdict(request), resume_handler)

    def run_init(self) -> dict[str, Any]:
        env_path = _env_path()
        reports_dir = _reports_dir()
        created_env = False
        created_reports = False

        env_path.parent.mkdir(parents=True, exist_ok=True)
        if not env_path.exists():
            env_path.write_text(read_bootstrap_env_text(), encoding="utf-8")
            created_env = True

        if not reports_dir.exists():
            reports_dir.mkdir(parents=True, exist_ok=True)
            created_reports = True

        _load_runtime_env()
        _overlay_runtime_env()

        return {
            "ok": True,
            "env_path": str(env_path),
            "reports_dir": str(reports_dir),
            "created_env": created_env,
            "created_reports": created_reports,
        }

    def start_doctor(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("remote_setup", False):
            return self._start_remote_setup_flow()

        def handler() -> dict[str, Any]:
            _load_runtime_env()
            _overlay_runtime_env()
            feishu = bool(payload.get("feishu", False))
            if feishu:
                args = SimpleNamespace(feishu_target=payload.get("feishu_targets", []), all_feishu_targets=payload.get("all_feishu_targets", False))
                exit_code = run_feishu_doctor(args)
                return {"exit_code": exit_code, "mode": "feishu"}
            username = _required_org_username()
            salt = _required_env("HASH_SALT")
            configs = parse_remote_configs_from_env()
            runtime_passwords = self._runtime_passwords_for([c.alias for c in configs])
            probes: list[dict[str, Any]] = []
            for collector in _collectors(hash_source_host(username, "local", salt)):
                ok, msg = collector.probe()
                probes.append({"name": collector.name, "source_name": collector.source_name, "ok": ok, "message": msg})
            for collector in build_remote_collectors(configs, username=username, salt=salt, runtime_passwords=runtime_passwords):
                if isinstance(collector, RemoteFileCollector):
                    ok, msg = collector.probe()
                    probes.append({"name": collector.name, "source_name": collector.source_name, "ok": ok, "message": msg})
            return {"exit_code": 0, "probes": probes}

        configs = parse_remote_configs_from_env()
        wrapped = self._wrap_with_ssh_auth_fallback(handler, configs)
        return self.jobs.start("doctor", wrapped)

    def start_collect(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._collect_or_pause(payload)

    def start_sync_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._sync_preview_or_pause(payload)

    def start_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("confirm_sync", False):
            raise RuntimeError("confirm_sync is required")
        return self._sync_or_pause(payload)


class _Handler(BaseHTTPRequestHandler):
    server_version = "llm-usage-web/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    @property
    def service(self) -> WebService:
        return self.server.service  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/runtime":
            return self._write_json(HTTPStatus.OK, self.service.runtime_payload())
        if parsed.path == "/api/config":
            return self._write_json(HTTPStatus.OK, load_config_payload())
        if parsed.path == "/api/results/latest":
            return self._write_json(HTTPStatus.OK, load_latest_results())
        if parsed.path == "/api/jobs":
            return self._write_json(HTTPStatus.OK, {"jobs": self.service.jobs.list_jobs()})
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stream"):
            return self._stream_job(parsed.path.split("/")[3])
        if parsed.path.startswith("/api/jobs/"):
            job = self.service.jobs.get_job(parsed.path.rsplit("/", 1)[-1])
            if not job:
                return self._write_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
            return self._write_json(HTTPStatus.OK, job)
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload = self._read_json()
        try:
            if parsed.path == "/api/init":
                return self._write_json(HTTPStatus.OK, self.service.run_init())
            if parsed.path == "/api/config/validate":
                return self._write_json(HTTPStatus.OK, validate_config_payload(payload))
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/input"):
                value = str(payload.get("value", ""))
                job_id = parsed.path.split("/")[3]
                return self._write_json(HTTPStatus.ACCEPTED, self.service.jobs.submit_input(job_id, value))
            if parsed.path == "/api/doctor":
                return self._write_json(HTTPStatus.ACCEPTED, self.service.start_doctor(payload))
            if parsed.path == "/api/collect":
                return self._write_json(HTTPStatus.ACCEPTED, self.service.start_collect(payload))
            if parsed.path == "/api/sync/preview":
                return self._write_json(HTTPStatus.ACCEPTED, self.service.start_sync_preview(payload))
            if parsed.path == "/api/sync":
                return self._write_json(HTTPStatus.ACCEPTED, self.service.start_sync(payload))
        except RuntimeError as exc:
            return self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        payload = self._read_json()
        if parsed.path == "/api/config":
            result = save_config_payload(payload)
            status = HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST
            return self._write_json(status, result)
        return self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_job(self, job_id: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for _ in range(50):
            job = self.service.jobs.get_job(job_id) or {"error": "job not found"}
            self.wfile.write(f"data: {json.dumps(job, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            threading.Event().wait(0.2)

    def _serve_static(self, raw_path: str) -> None:
        relative = "index.html" if raw_path in {"", "/"} else raw_path.lstrip("/")
        file_path = (_web_root() / relative).resolve()
        if not str(file_path).startswith(str(_web_root().resolve())) or not file_path.exists():
            file_path = _web_root() / "index.html"
        content_type = "text/html; charset=utf-8"
        if file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WebConsoleServer:
    def __init__(self, host: str, port: int, open_browser: bool) -> None:
        self.host = host
        self.port = port
        self.open_browser = open_browser
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.service = WebService()  # type: ignore[attr-defined]
        self.thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.httpd.server_address[1]}"

    def start(self) -> None:
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        if self.open_browser:
            webbrowser.open(self.base_url)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


def create_server(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> WebConsoleServer:
    _load_runtime_env()
    _overlay_runtime_env()
    return WebConsoleServer(host=host, port=port, open_browser=open_browser)


def cmd_web(args: argparse.Namespace) -> int:
    server = create_server(
        host=getattr(args, "host", "127.0.0.1"),
        port=int(getattr(args, "port", 0) or 0),
        open_browser=not bool(getattr(args, "no_open", False)),
    )
    server.start()
    print(f"web: {server.base_url}")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()
    return 0
