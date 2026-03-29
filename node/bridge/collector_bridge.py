from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from llm_usage.collectors import (  # noqa: E402
    build_claude_collector,
    build_codex_collector,
    build_copilot_cli_collector,
    build_copilot_vscode_collector,
    build_cursor_collector,
    build_opencode_collector,
)
from llm_usage.env import load_dotenv  # noqa: E402
from llm_usage.identity import hash_source_host  # noqa: E402
from llm_usage.paths import resolve_runtime_paths  # noqa: E402
from llm_usage.remotes import build_remote_collectors, parse_remote_configs_from_env  # noqa: E402
from llm_usage.runtime_state import load_selected_remote_aliases  # noqa: E402


def _env_path() -> Path:
    return resolve_runtime_paths(REPO_ROOT).env_path


def _runtime_state_path() -> Path:
    return resolve_runtime_paths(REPO_ROOT).runtime_state_path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing env var: {name}")
    return value


def _load_runtime_env() -> None:
    load_dotenv(_env_path())


def _collectors(local_source_host_hash: str):
    return [
        build_claude_collector(source_host_hash=local_source_host_hash),
        build_codex_collector(source_host_hash=local_source_host_hash),
        build_copilot_cli_collector(source_host_hash=local_source_host_hash),
        build_copilot_vscode_collector(source_host_hash=local_source_host_hash),
        build_cursor_collector(source_host_hash=local_source_host_hash),
        build_opencode_collector(source_host_hash=local_source_host_hash),
    ]


def _selected_remote_configs():
    configured = parse_remote_configs_from_env()
    if "LLM_USAGE_SELECTED_REMOTE_ALIASES" in os.environ:
        selected_raw = os.environ.get("LLM_USAGE_SELECTED_REMOTE_ALIASES", "").strip()
        selected_aliases = [item.strip().upper() for item in selected_raw.split(",") if item.strip()]
        return [config for config in configured if config.alias in selected_aliases]
    else:
        selected_aliases = load_selected_remote_aliases(_runtime_state_path())
    if not selected_aliases:
        return configured
    return [config for config in configured if config.alias in selected_aliases]


def _serialize_event(event) -> dict[str, object]:
    payload = asdict(event)
    payload["event_time"] = event.event_time.isoformat()
    return payload


def _collect_payload(lookback_days: int) -> dict[str, object]:
    _load_runtime_env()
    username = _required("ORG_USERNAME")
    salt = _required("HASH_SALT")
    local_source_host_hash = hash_source_host(username, "local", salt)

    collectors = list(_collectors(local_source_host_hash))
    collectors.extend(build_remote_collectors(_selected_remote_configs(), username=username, salt=salt))

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, lookback_days))
    events = []
    warnings: list[str] = []
    probes = []

    for collector in collectors:
        ok, message = collector.probe()
        probes.append(
            {
                "name": collector.name,
                "source_name": getattr(collector, "source_name", "local"),
                "source_host_hash": getattr(collector, "source_host_hash", ""),
                "ok": ok,
                "message": message,
            }
        )
        if not ok:
            continue

        out = collector.collect(start=start, end=end)
        for event in out.events:
            events.append(
                event
                if event.source_host_hash
                else replace(event, source_host_hash=getattr(collector, "source_host_hash", ""))
            )
        warnings.extend(out.warnings)

    return {
        "events": [_serialize_event(event) for event in events],
        "warnings": warnings,
        "probes": probes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Node collector bridge")
    parser.add_argument("command", choices=["collect", "doctor"])
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args()

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        payload = _collect_payload(max(1, args.lookback_days))
    captured = [line.strip() for line in stream.getvalue().splitlines() if line.strip()]
    if captured:
        payload["warnings"] = [*captured, *(payload.get("warnings") or [])]
    if args.command == "doctor":
        payload.pop("events", None)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
