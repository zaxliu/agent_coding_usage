from __future__ import annotations

import json
import os
import time
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput

_PROBE_SCRIPT = """
import glob, json, os, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    patterns = json.load(fh)
matches = []
for pattern in patterns:
    try:
        for path in glob.glob(os.path.expanduser(pattern), recursive=True):
            if not os.path.isfile(path):
                continue
            lower = path.lower()
            if not (lower.endswith('.json') or lower.endswith('.jsonl')):
                continue
            matches.append(path)
    except Exception:
        pass
print(json.dumps({"matches": len(sorted(set(matches)))}))
"""

_COLLECT_SCRIPT = """
import glob, json, os, re, sys
from datetime import datetime, timezone

with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    payload = json.load(fh)
patterns = payload.get("patterns", [])
tool = payload.get("tool", "unknown")
start_ts = float(payload.get("start_ts", 0))
end_ts = float(payload.get("end_ts", 0))
max_files = int(payload.get("max_files", 0) or 0)
max_total_bytes = int(payload.get("max_total_bytes", 0) or 0)
log_path = payload.get("log_path", "")
output_path = payload.get("output_path", "")

def log(message):
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(message + "\\n")
        except Exception:
            pass
    print(message, file=sys.stderr)

log("info: remote script started tool=" + tool + " patterns=" + str(len(patterns)))

def coerce_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0

def parse_time(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10000000000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 10000000000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None

def walk_json_nodes(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            for item in walk_json_nodes(value):
                yield item
    elif isinstance(obj, list):
        for item in obj:
            for node in walk_json_nodes(item):
                yield node

def extract_model(node):
    for key in ("model", "model_name", "modelName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"

def extract_time(node):
    for key in ("timestamp", "created_at", "createdAt", "time", "date"):
        parsed = parse_time(node.get(key))
        if parsed is not None:
            return parsed
    return None

def extract_usage(node):
    usage = node.get("usage") if isinstance(node.get("usage"), dict) else node
    input_tokens = coerce_int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("inputTokenCount"))
    output_tokens = coerce_int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("outputTokenCount"))
    cache_tokens = coerce_int(
        usage.get("cache_tokens") or usage.get("cached_tokens") or usage.get("cached_input_tokens")
    )
    if cache_tokens == 0:
        cache_tokens = coerce_int(usage.get("cache_read_input_tokens")) + coerce_int(
            usage.get("cache_creation_input_tokens")
        )
    return input_tokens, cache_tokens, output_tokens

def extract_codex_token_count_usage(node):
    if node.get("type") != "event_msg":
        return None
    payload = node.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    last_token_usage = info.get("last_token_usage")
    if not isinstance(last_token_usage, dict):
        return None
    cache_tokens = coerce_int(last_token_usage.get("cached_input_tokens"))
    input_tokens = max(0, coerce_int(last_token_usage.get("input_tokens")) - cache_tokens)
    output_tokens = coerce_int(last_token_usage.get("output_tokens"))
    return input_tokens, cache_tokens, output_tokens

def extract_codex_turn_model(node):
    if node.get("type") != "turn_context":
        return None
    payload = node.get("payload")
    if not isinstance(payload, dict):
        return None
    model = extract_model(payload)
    if model != "unknown":
        return model
    collaboration_mode = payload.get("collaboration_mode")
    if not isinstance(collaboration_mode, dict):
        return None
    settings = collaboration_mode.get("settings")
    if not isinstance(settings, dict):
        return None
    nested_model = extract_model(settings)
    return nested_model if nested_model != "unknown" else None

def build_session_fingerprint(path, tool_name):
    if tool_name == "codex":
        matches = re.findall(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            os.path.splitext(os.path.basename(path))[0],
        )
        if matches:
            return "codex:" + matches[-1].lower()
        stem = os.path.splitext(os.path.basename(path))[0]
        return "codex_file:" + stem
    if tool_name == "copilot_cli":
        session_dir = os.path.basename(os.path.dirname(path)).strip()
        if session_dir:
            return "copilot_cli:" + session_dir
        stem = os.path.splitext(os.path.basename(path))[0]
        return "copilot_cli_file:" + stem
    return None

def extract_copilot_cli_events(node, fallback_time, session_fingerprint, source_ref):
    if node.get("type") != "session.shutdown":
        return []
    data = node.get("data")
    if not isinstance(data, dict):
        return []
    model_metrics = data.get("modelMetrics")
    if not isinstance(model_metrics, dict):
        return []
    event_time = extract_time(node) or parse_time(data.get("sessionStartTime")) or fallback_time
    out = []
    prefix = session_fingerprint or "copilot_cli"
    for model_name, metrics in model_metrics.items():
        if not isinstance(model_name, str) or not model_name.strip() or not isinstance(metrics, dict):
            continue
        usage = metrics.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = coerce_int(usage.get("inputTokens"))
        output_tokens = coerce_int(usage.get("outputTokens"))
        cache_tokens = coerce_int(usage.get("cacheReadTokens")) + coerce_int(usage.get("cacheWriteTokens"))
        if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
            continue
        out.append((event_time, model_name.strip(), input_tokens, cache_tokens, output_tokens, prefix + ":" + model_name.strip(), source_ref))
    return out

def extract_copilot_vscode_model(session, request):
    agent = request.get("agent")
    if isinstance(agent, dict):
        model_id = agent.get("modelId")
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() != "copilot/auto":
            return model_id.strip()
    result = request.get("result")
    if isinstance(result, dict):
        details = result.get("details")
        if isinstance(details, str) and details.strip():
            return details.split("•", 1)[0].strip()
    input_state = session.get("inputState")
    if isinstance(input_state, dict):
        selected_model = input_state.get("selectedModel")
        if isinstance(selected_model, dict):
            metadata = selected_model.get("metadata")
            if isinstance(metadata, dict):
                for key in ("version", "name", "id"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return "unknown"

def extract_copilot_vscode_events(node, fallback_time, source_ref):
    session = node.get("v") if node.get("kind") == 0 and isinstance(node.get("v"), dict) else node
    if not isinstance(session, dict):
        return []
    session_id = session.get("sessionId")
    requests = session.get("requests")
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(requests, list):
        return []
    out = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        request_id = request.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            continue
        event_time = parse_time(request.get("timestamp")) or fallback_time
        out.append((event_time, extract_copilot_vscode_model(session, request), 0, 0, 0, "copilot_vscode:" + session_id.strip() + ":" + request_id.strip(), source_ref))
    return out

def append_event(out, event_time, model, input_tokens, cache_tokens, output_tokens, session_fingerprint, source_ref):
    if event_time is None:
        return
    ts = event_time.timestamp()
    if ts < start_ts or ts > end_ts:
        return
    out.append(
        {
            "tool": tool,
            "model": model,
            "event_time": event_time.isoformat(),
            "input_tokens": input_tokens,
            "cache_tokens": cache_tokens,
            "output_tokens": output_tokens,
            "session_fingerprint": session_fingerprint,
            "source_ref": source_ref,
        }
    )

events = []
warnings = []
seen = set()
processed_files = 0
total_bytes = 0
for pattern in patterns:
    try:
        log("info: expanding pattern " + pattern)
        for path in glob.glob(os.path.expanduser(pattern), recursive=True):
            if path in seen or not os.path.isfile(path):
                continue
            lower = path.lower()
            if not (lower.endswith('.json') or lower.endswith('.jsonl')):
                continue
            try:
                stat = os.stat(path)
            except OSError as exc:
                warnings.append("failed stating " + path + ": " + str(exc))
                continue
            if stat.st_mtime < start_ts:
                continue
            if max_files > 0 and processed_files >= max_files:
                warnings.append("stopped after reaching max_files=" + str(max_files))
                with open(output_path, "w", encoding="utf-8") as fh:
                    json.dump({"events": events, "warnings": warnings}, fh)
                raise SystemExit(0)
            file_size = int(stat.st_size)
            if max_total_bytes > 0 and total_bytes + file_size > max_total_bytes:
                warnings.append("stopped after reaching max_total_bytes=" + str(max_total_bytes))
                with open(output_path, "w", encoding="utf-8") as fh:
                    json.dump({"events": events, "warnings": warnings}, fh)
                raise SystemExit(0)
            seen.add(path)
            processed_files += 1
            total_bytes += file_size
            if processed_files == 1 or processed_files % 20 == 0:
                log(
                    "info: processing file "
                    + str(processed_files)
                    + " size="
                    + str(file_size)
                    + " path="
                    + path
                )
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    text = fh.read()
                fallback_time = datetime.fromtimestamp(int(os.path.getmtime(path)), tz=timezone.utc)
                session_fingerprint = build_session_fingerprint(path, tool)
                codex_model_hint = None
                if lower.endswith('.jsonl'):
                    idx = 0
                    for raw_line in text.splitlines():
                        idx += 1
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if tool == "copilot_cli":
                            for item in extract_copilot_cli_events(
                                obj,
                                fallback_time,
                                session_fingerprint,
                                path + ":" + str(idx),
                            ):
                                append_event(events, *item)
                            continue
                        if tool == "copilot_vscode":
                            for item in extract_copilot_vscode_events(
                                obj,
                                fallback_time,
                                path + ":" + str(idx),
                            ):
                                append_event(events, *item)
                            continue
                        if tool == "codex":
                            turn_model = extract_codex_turn_model(obj)
                            if turn_model:
                                codex_model_hint = turn_model
                            usage = extract_codex_token_count_usage(obj)
                            if usage is None:
                                continue
                            input_tokens, cache_tokens, output_tokens = usage
                            if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                continue
                            event_time = extract_time(obj) or fallback_time
                            model = extract_model(obj)
                            if model == "unknown" and codex_model_hint:
                                model = codex_model_hint
                            append_event(
                                events,
                                event_time,
                                model,
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                session_fingerprint,
                                path + ":" + str(idx),
                            )
                            continue
                        local_seen = set()
                        for candidate in walk_json_nodes(obj):
                            input_tokens, cache_tokens, output_tokens = extract_usage(candidate)
                            if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                continue
                            event_time = extract_time(candidate) or fallback_time
                            dedupe_key = (
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                event_time.isoformat(),
                            )
                            if dedupe_key in local_seen:
                                continue
                            local_seen.add(dedupe_key)
                            append_event(
                                events,
                                event_time,
                                extract_model(candidate),
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                session_fingerprint,
                                path + ":" + str(idx),
                            )
                elif lower.endswith('.json'):
                    try:
                        obj = json.loads(text)
                    except ValueError:
                        warnings.append("failed decoding " + path)
                        continue
                    if tool == "codex":
                        for candidate in walk_json_nodes(obj):
                            turn_model = extract_codex_turn_model(candidate)
                            if turn_model:
                                codex_model_hint = turn_model
                        for candidate in walk_json_nodes(obj):
                            usage = extract_codex_token_count_usage(candidate)
                            if usage is None:
                                continue
                            input_tokens, cache_tokens, output_tokens = usage
                            if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                continue
                            event_time = extract_time(candidate) or fallback_time
                            model = extract_model(candidate)
                            if model == "unknown" and codex_model_hint:
                                model = codex_model_hint
                            append_event(
                                events,
                                event_time,
                                model,
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                session_fingerprint,
                                path,
                            )
                    elif tool == "copilot_cli":
                        for item in extract_copilot_cli_events(obj, fallback_time, session_fingerprint, path):
                            append_event(events, *item)
                    elif tool == "copilot_vscode":
                        for item in extract_copilot_vscode_events(obj, fallback_time, path):
                            append_event(events, *item)
                    else:
                        local_seen = set()
                        for candidate in walk_json_nodes(obj):
                            input_tokens, cache_tokens, output_tokens = extract_usage(candidate)
                            if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                                continue
                            event_time = extract_time(candidate) or fallback_time
                            dedupe_key = (
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                event_time.isoformat(),
                            )
                            if dedupe_key in local_seen:
                                continue
                            local_seen.add(dedupe_key)
                            append_event(
                                events,
                                event_time,
                                extract_model(candidate),
                                input_tokens,
                                cache_tokens,
                                output_tokens,
                                session_fingerprint,
                                path,
                            )
            except Exception as exc:
                warnings.append("failed reading " + path + ": " + str(exc))
    except Exception:
        pass
with open(output_path, "w", encoding="utf-8") as fh:
    json.dump({"events": events, "warnings": warnings}, fh)
"""


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    port: int

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"


class RemoteFileCollector(BaseCollector):
    def __init__(
        self,
        name: str,
        target: SshTarget,
        patterns: list[str],
        source_name: str,
        source_host_hash: str,
        max_files: int = 400,
        max_total_bytes: int = 64 * 1024 * 1024,
        timeout_sec: int = 120,
        runner=None,
        popen_factory=None,
    ) -> None:
        self.name = name
        self.target = target
        self.patterns = patterns
        self.source_name = source_name
        self.source_host_hash = source_host_hash
        self.max_files = max(1, max_files)
        self.max_total_bytes = max(1, max_total_bytes)
        self.timeout_sec = max(10, timeout_sec)
        self._runner = runner or subprocess.run
        self._popen_factory = popen_factory or (subprocess.Popen if runner is None else None)

    def probe(self) -> tuple[bool, str]:
        self._log_progress("探测：查找远端 Python")
        python_cmd = self._discover_python()
        if not python_cmd:
            return False, "no remote python interpreter found"
        self._log_progress(f"探测：使用远端解释器 {python_cmd}")
        payload, error = self._run_python_script(python_cmd, _PROBE_SCRIPT)
        if error:
            return False, error
        matches = payload.get("matches")
        if not isinstance(matches, int):
            return False, "remote probe returned invalid payload"
        if matches == 0:
            return False, f"no data files found for {self.name}"
        return True, f"{matches} remote files detected"

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        warnings: list[str] = []
        self.__active_start = start
        self.__active_end = end
        self._log_progress("采集：查找远端 Python")
        python_cmd = self._discover_python()
        if not python_cmd:
            return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: no remote python interpreter found"])
        self._log_progress(f"采集：使用远端解释器 {python_cmd}")

        payload, error = self._run_python_script(python_cmd, _COLLECT_SCRIPT)
        if error:
            return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: {error}"])

        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            return CollectOutput(
                events=[],
                warnings=[f"{self.source_name}/{self.name}: remote collect returned invalid payload"],
            )

        events: list[UsageEvent] = []
        warnings.extend(
            f"{self.source_name}/{self.name}: {warning}"
            for warning in payload.get("warnings", [])
            if isinstance(warning, str) and warning.strip()
        )
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_time_raw = item.get("event_time")
            if not isinstance(event_time_raw, str):
                continue
            try:
                event_time = datetime.fromisoformat(event_time_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_time = event_time.astimezone(timezone.utc)
            if start <= event_time <= end:
                events.append(
                    UsageEvent(
                        tool=str(item.get("tool") or self.name),
                        model=str(item.get("model") or "unknown"),
                        event_time=event_time,
                        input_tokens=_coerce_int(item.get("input_tokens")),
                        cache_tokens=_coerce_int(item.get("cache_tokens")),
                        output_tokens=_coerce_int(item.get("output_tokens")),
                        session_fingerprint=_optional_str(item.get("session_fingerprint")),
                        source_ref=_optional_str(item.get("source_ref")),
                        source_host_hash=self.source_host_hash,
                    )
                )
        if not events:
            warnings.append(f"{self.source_name}/{self.name}: no usage events in selected time range")
        return CollectOutput(events=events, warnings=warnings)

    def _discover_python(self) -> str | None:
        try:
            completed = self._runner(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-p",
                    str(self.target.port),
                    self.target.destination,
                    "sh",
                    "-lc",
                    "command -v python3 >/dev/null 2>&1 && printf python3 || "
                    "(command -v python >/dev/null 2>&1 && printf python || true)",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            return None
        if completed.returncode != 0:
            return None
        python_cmd = completed.stdout.strip()
        return python_cmd or None

    def _run_python_script(self, python_cmd: str, script: str) -> tuple[dict, str | None]:
        local_script = None
        local_patterns = None
        local_output = None
        remote_script = None
        remote_patterns = None
        remote_output = None
        remote_log = None
        try:
            local_script = self._write_temp_file(".py", script)
            remote_base = f"/tmp/llm_usage_{os.getpid()}_{next(tempfile._get_candidate_names())}"
            remote_script = f"{remote_base}.py"
            remote_patterns = f"{remote_base}_patterns.json"
            remote_output = f"{remote_base}_output.json"
            remote_log = f"{remote_base}.log"
            local_patterns = self._write_temp_file(
                ".json",
                json.dumps(
                    {
                        "patterns": self.patterns,
                        "tool": self.name,
                        "start_ts": self._active_start.timestamp(),
                        "end_ts": self._active_end.timestamp(),
                        "max_files": self.max_files,
                        "max_total_bytes": self.max_total_bytes,
                        "log_path": remote_log,
                        "output_path": remote_output,
                    }
                ),
            )
            local_output = self._temp_path(".json")

            self._log_progress(f"上传脚本 -> {remote_script}")
            error = self._scp_to_remote(local_script, remote_script)
            if error:
                return {}, error
            self._log_progress(f"上传输入 -> {remote_patterns}")
            error = self._scp_to_remote(local_patterns, remote_patterns)
            if error:
                return {}, error
            self._log_progress(f"执行远端脚本 -> {remote_output}")
            self._log_progress(f"远端日志保留在 {remote_log}")
            error = self._ssh_run_script([python_cmd, remote_script, remote_patterns])
            if error:
                return {}, error
            self._log_progress(f"下载结果 <- {remote_output}")
            error = self._scp_from_remote(remote_output, local_output)
            if error:
                return {}, error
            try:
                payload = json.loads(local_output.read_text(encoding="utf-8"))
            except ValueError:
                return {}, "remote command returned non-JSON output"
            if not isinstance(payload, dict):
                return {}, "remote command returned invalid JSON payload"
            return payload, None
        finally:
            for path in (local_script, local_patterns, local_output):
                if path and path.exists():
                    path.unlink(missing_ok=True)
            if remote_script and remote_patterns and remote_output:
                self._log_progress("清理远端临时文件")
                self._cleanup_remote_files([remote_script, remote_patterns, remote_output])

    def _write_temp_file(self, suffix: str, content: str):
        handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
        try:
            handle.write(content)
            return_value = handle.name
        finally:
            handle.close()
        return Path(return_value)

    def _temp_path(self, suffix: str):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return Path(path)

    def _scp_to_remote(self, local_path, remote_path: str) -> str | None:
        try:
            completed = self._runner(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-P",
                    str(self.target.port),
                    str(local_path),
                    f"{self.target.destination}:{remote_path}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return "scp upload timed out"
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or "scp upload failed"
        return None

    def _scp_from_remote(self, remote_path: str, local_path) -> str | None:
        try:
            completed = self._runner(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-P",
                    str(self.target.port),
                    f"{self.target.destination}:{remote_path}",
                    str(local_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return "scp download timed out"
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or "scp download failed"
        return None

    def _ssh_run_script(self, args: list[str]) -> str | None:
        if self._popen_factory is None:
            try:
                completed = self._runner(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=10",
                        "-p",
                        str(self.target.port),
                        self.target.destination,
                        *args,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                )
            except subprocess.TimeoutExpired:
                return "remote command timed out"
            if completed.returncode != 0:
                return completed.stderr.strip() or completed.stdout.strip() or "remote command failed"
            if completed.stderr.strip():
                for line in completed.stderr.strip().splitlines():
                    self._log_progress(f"remote stderr: {line}")
            return None

        try:
            process = self._popen_factory(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-p",
                    str(self.target.port),
                    self.target.destination,
                    *args,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return f"remote command failed to start: {exc}"

        deadline = time.monotonic() + self.timeout_sec
        stderr_lines: list[str] = []
        try:
            while True:
                if process.stderr is not None:
                    line = process.stderr.readline()
                else:
                    line = ""
                if line:
                    text = line.rstrip()
                    stderr_lines.append(text)
                    self._log_progress(f"remote stderr: {text}")
                    continue
                if process.poll() is not None:
                    break
                if time.monotonic() > deadline:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    return "remote command timed out"
                time.sleep(0.1)
            remaining = ""
            if process.stderr is not None:
                remaining = process.stderr.read() or ""
            if remaining.strip():
                for line in remaining.strip().splitlines():
                    stderr_lines.append(line)
                    self._log_progress(f"remote stderr: {line}")
            if process.returncode != 0:
                return "\n".join(stderr_lines).strip() or "remote command failed"
        finally:
            if process.stderr is not None:
                process.stderr.close()
        return None

    def _cleanup_remote_files(self, remote_paths: list[str]) -> None:
        command = "rm -f " + " ".join(_shell_quote(path) for path in remote_paths)
        try:
            self._runner(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-p",
                    str(self.target.port),
                    self.target.destination,
                    "sh",
                    "-lc",
                    command,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            return

    def _log_progress(self, message: str) -> None:
        print(f"info: remote[{self.source_name}/{self.name}] {message}")

    @property
    def _active_start(self) -> datetime:
        return getattr(self, "__active_start", datetime.fromtimestamp(0, tz=timezone.utc))

    @property
    def _active_end(self) -> datetime:
        return getattr(self, "__active_end", datetime.now(timezone.utc))


def _suffix(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".jsonl"):
        return ".jsonl"
    if lower.endswith(".json"):
        return ".json"
    return ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _coerce_int(value: object) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
