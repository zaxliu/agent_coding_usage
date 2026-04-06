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
import threading
import traceback
from types import SimpleNamespace
from typing import Any, Callable, Optional
from urllib.parse import urlparse
import webbrowser

from llm_usage.aggregation import aggregate_events
from llm_usage.collectors import BaseCollector
from llm_usage.collectors.remote_file import RemoteFileCollector
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
from llm_usage.remotes import RemoteDraft, build_remote_collectors, parse_remote_configs_from_env
from llm_usage.runtime_state import save_selected_remote_aliases


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


def load_config_payload() -> dict[str, Any]:
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
    }


def validate_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
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

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def save_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_config_payload(payload)
    if not validation["ok"]:
        return validation

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
    return {"ok": True, "errors": [], "warnings": validation["warnings"]}


def load_latest_results() -> dict[str, Any]:
    csv_path = _reports_dir() / "usage_report.csv"
    if not csv_path.exists():
        return {"ok": True, "csv_path": str(csv_path), "rows": [], "generated_at": None}
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(dict(row))
    generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=timezone.utc).isoformat()
    return {"ok": True, "csv_path": str(csv_path), "rows": rows, "generated_at": generated_at}


def _resolve_feishu_targets_summary(names: list[str], select_all: bool) -> list[dict[str, str]]:
    targets = select_feishu_targets(
        resolve_feishu_targets_from_env(os.environ),
        selected_names=names,
        select_all=select_all,
        default_only=not names and not select_all,
    )
    return [{"name": target.name, "app_token": target.app_token, "table_id": target.table_id} for target in targets]


def _build_aggregates_for_web(payload: dict[str, Any]) -> tuple[list, list[str], dict[str, str]]:
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
    collectors.extend(build_remote_collectors(selected_configs, username=username, salt=salt))
    events, warnings = _collect_all(lookback_days, collectors)
    rows = aggregate_events(events, user_hash=hash_user(username, salt), timezone_name=timezone_name)
    host_labels = _build_terminal_host_labels(username, salt, selected_configs)
    return rows, warnings, host_labels


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._write_job_id: Optional[str] = None

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
            job_id = f"job-{len(self._jobs) + 1}-{int(datetime.now().timestamp() * 1000)}"
            job = {
                "id": job_id,
                "type": job_type,
                "status": "queued",
                "created_at": _json_now(),
                "updated_at": _json_now(),
                "logs": [],
                "result": None,
                "error": None,
                "write_operation": write_operation,
            }
            self._jobs[job_id] = job
            if write_operation:
                self._write_job_id = job_id

        def runner() -> None:
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
            except Exception as exc:  # pragma: no cover - defensive
                logs = [line for line in (stdout.getvalue() + stderr.getvalue()).splitlines() if line.strip()]
                logs.extend(traceback.format_exc().splitlines())
                with self._lock:
                    self._jobs[job_id]["status"] = "failed"
                    self._jobs[job_id]["updated_at"] = _json_now()
                    self._jobs[job_id]["logs"] = logs
                    self._jobs[job_id]["error"] = str(exc)
            finally:
                with self._lock:
                    if self._write_job_id == job_id:
                        self._write_job_id = None

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return self.get_job(job_id) or {"id": job_id}


class WebService:
    def __init__(self) -> None:
        self.jobs = JobManager()

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

    def start_doctor(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            probes: list[dict[str, Any]] = []
            for collector in _collectors(hash_source_host(username, "local", salt)):
                ok, msg = collector.probe()
                probes.append({"name": collector.name, "source_name": collector.source_name, "ok": ok, "message": msg})
            for collector in build_remote_collectors(parse_remote_configs_from_env(), username=username, salt=salt):
                if isinstance(collector, RemoteFileCollector):
                    ok, msg = collector.probe()
                    probes.append({"name": collector.name, "source_name": collector.source_name, "ok": ok, "message": msg})
            return {"exit_code": 0, "probes": probes}

        return self.jobs.start("doctor", handler)

    def start_collect(self, payload: dict[str, Any]) -> dict[str, Any]:
        def handler() -> dict[str, Any]:
            rows, warnings, host_labels = _build_aggregates_for_web(payload)
            csv_path = _reports_dir() / "usage_report.csv"
            from llm_usage.reporting import write_csv_report

            write_csv_report(rows, _reports_dir())
            return {
                "row_count": len(rows),
                "warnings": warnings,
                "host_labels": host_labels,
                "csv_path": str(csv_path),
            }

        return self.jobs.start("collect", handler, write_operation=True)

    def start_sync_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        def handler() -> dict[str, Any]:
            rows, warnings, _host_labels = _build_aggregates_for_web(payload)
            names = [str(item).strip() for item in (payload.get("feishu_targets") or []) if str(item).strip()]
            return {
                "row_count": len(rows),
                "warnings": warnings,
                "targets": _resolve_feishu_targets_summary(names, bool(payload.get("all_feishu_targets", False))),
            }

        return self.jobs.start("sync_preview", handler)

    def start_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("confirm_sync", False):
            raise RuntimeError("confirm_sync is required")

        def handler() -> dict[str, Any]:
            rows, warnings, _host_labels = _build_aggregates_for_web(payload)
            from llm_usage.reporting import write_csv_report

            csv_path = write_csv_report(rows, _reports_dir())
            exit_code = _sync_rows_to_feishu_targets(
                rows,
                dry_run=False,
                feishu_target=[str(item).strip() for item in (payload.get("feishu_targets") or []) if str(item).strip()],
                all_feishu_targets=bool(payload.get("all_feishu_targets", False)),
            )
            return {"row_count": len(rows), "warnings": warnings, "csv_path": str(csv_path), "exit_code": exit_code}

        return self.jobs.start("sync", handler, write_operation=True)


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
            if parsed.path == "/api/config/validate":
                return self._write_json(HTTPStatus.OK, validate_config_payload(payload))
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
