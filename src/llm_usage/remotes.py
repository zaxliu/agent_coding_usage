from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from llm_usage.collectors.base import BaseCollector
from llm_usage.collectors.remote_file import (
    RemoteCollectJob,
    RemoteFileCollector,
    SshTarget,
    _ssh_command_and_env,
)
from llm_usage.env import upsert_env_var
from llm_usage.identity import hash_source_host

DEFAULT_REMOTE_CLAUDE_LOG_PATHS = [
    "~/.claude/**/*.jsonl",
    "~/.claude/**/*.json",
    "~/.config/claude/**/*.jsonl",
]
DEFAULT_REMOTE_CODEX_LOG_PATHS = [
    "~/.codex/**/*.jsonl",
    "~/.codex/**/*.json",
]
DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS = [
    "~/.copilot/session-state/**/*.jsonl",
]
DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS = [
    "~/.vscode-server/data/User/globalStorage/emptyWindowChatSessions/*.jsonl",
    "~/.vscode-server/data/User/workspaceStorage/**/chatEditingSessions/*/state.json",
]


@dataclass(frozen=True)
class RemoteHostConfig:
    alias: str
    ssh_host: str
    ssh_user: str
    ssh_port: int
    source_label: str
    claude_log_paths: list[str]
    codex_log_paths: list[str]
    copilot_cli_log_paths: list[str]
    copilot_vscode_session_paths: list[str]
    is_ephemeral: bool = False
    use_sshpass: bool = False


RemoteValidator = Callable[[RemoteHostConfig, str | None], tuple[bool, str]]


def parse_remote_configs_from_env(env: dict[str, str] | None = None) -> list[RemoteHostConfig]:
    data = env or os.environ
    aliases = _split_aliases(data.get("REMOTE_HOSTS", ""))
    out: list[RemoteHostConfig] = []
    for alias in aliases:
        prefix = f"REMOTE_{alias}_"
        ssh_host = data.get(prefix + "SSH_HOST", "").strip()
        ssh_user = data.get(prefix + "SSH_USER", "").strip()
        if not ssh_host or not ssh_user:
            continue
        ssh_port = _safe_port(data.get(prefix + "SSH_PORT", "22"))
        source_label = data.get(prefix + "LABEL", "").strip() or default_source_label(ssh_user, ssh_host)
        claude_log_paths = _split_paths(
            data.get(prefix + "CLAUDE_LOG_PATHS", ""),
            DEFAULT_REMOTE_CLAUDE_LOG_PATHS,
        )
        codex_log_paths = _split_paths(
            data.get(prefix + "CODEX_LOG_PATHS", ""),
            DEFAULT_REMOTE_CODEX_LOG_PATHS,
        )
        copilot_cli_log_paths = _split_paths(
            data.get(prefix + "COPILOT_CLI_LOG_PATHS", ""),
            DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS,
        )
        copilot_vscode_session_paths = _split_paths(
            data.get(prefix + "COPILOT_VSCODE_SESSION_PATHS", ""),
            DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS,
        )
        out.append(
            RemoteHostConfig(
                alias=alias,
                ssh_host=ssh_host,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                source_label=source_label,
                claude_log_paths=claude_log_paths,
                codex_log_paths=codex_log_paths,
                copilot_cli_log_paths=copilot_cli_log_paths,
                copilot_vscode_session_paths=copilot_vscode_session_paths,
                use_sshpass=_env_flag(data.get(prefix + "USE_SSHPASS", "")),
            )
        )
    return out


def build_remote_collectors(
    configs: list[RemoteHostConfig],
    username: str,
    salt: str,
    runtime_passwords: dict[str, str] | None = None,
) -> list[BaseCollector]:
    collectors: list[BaseCollector] = []
    runtime_passwords = runtime_passwords or {}
    for config in configs:
        source_host_hash = hash_source_host(username, config.source_label, salt)
        target = SshTarget(host=config.ssh_host, user=config.ssh_user, port=config.ssh_port)
        jobs = []
        if config.claude_log_paths:
            jobs.append(RemoteCollectJob(tool="claude_code", patterns=config.claude_log_paths))
        if config.codex_log_paths:
            jobs.append(RemoteCollectJob(tool="codex", patterns=config.codex_log_paths))
        if config.copilot_cli_log_paths:
            jobs.append(RemoteCollectJob(tool="copilot_cli", patterns=config.copilot_cli_log_paths))
        if config.copilot_vscode_session_paths:
            jobs.append(RemoteCollectJob(tool="copilot_vscode", patterns=config.copilot_vscode_session_paths))
        if jobs:
            collectors.append(
                RemoteFileCollector(
                    "remote",
                    target=target,
                    source_name=config.alias.lower(),
                    source_host_hash=source_host_hash,
                    jobs=jobs,
                    use_sshpass=config.use_sshpass,
                    ssh_password=runtime_passwords.get(config.alias),
                )
            )
    return collectors


def build_temporary_remote(
    ssh_host: str,
    ssh_user: str,
    ssh_port: int = 22,
    claude_log_paths: list[str] | None = None,
    codex_log_paths: list[str] | None = None,
    use_sshpass: bool = False,
) -> RemoteHostConfig:
    ssh_host = ssh_host.strip()
    ssh_user = ssh_user.strip()
    source_label = default_source_label(ssh_user, ssh_host)
    alias_seed = source_label
    alias = unique_alias(normalize_alias(alias_seed), [])
    return RemoteHostConfig(
        alias=alias,
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_port=max(1, ssh_port),
        source_label=source_label,
        claude_log_paths=claude_log_paths or list(DEFAULT_REMOTE_CLAUDE_LOG_PATHS),
        codex_log_paths=codex_log_paths or list(DEFAULT_REMOTE_CODEX_LOG_PATHS),
        copilot_cli_log_paths=list(DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS),
        copilot_vscode_session_paths=list(DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS),
        is_ephemeral=True,
        use_sshpass=use_sshpass,
    )


def append_remote_to_env(path: Path, config: RemoteHostConfig, existing_aliases: list[str]) -> str:
    alias = unique_alias(config.alias, existing_aliases)
    upsert_env_var(path, "REMOTE_HOSTS", ",".join(existing_aliases + [alias]))
    prefix = f"REMOTE_{alias}_"
    upsert_env_var(path, prefix + "SSH_HOST", config.ssh_host)
    upsert_env_var(path, prefix + "SSH_USER", config.ssh_user)
    upsert_env_var(path, prefix + "SSH_PORT", str(config.ssh_port))
    upsert_env_var(path, prefix + "LABEL", config.source_label)
    upsert_env_var(path, prefix + "CLAUDE_LOG_PATHS", ",".join(config.claude_log_paths))
    upsert_env_var(path, prefix + "CODEX_LOG_PATHS", ",".join(config.codex_log_paths))
    upsert_env_var(path, prefix + "COPILOT_CLI_LOG_PATHS", ",".join(config.copilot_cli_log_paths))
    upsert_env_var(
        path,
        prefix + "COPILOT_VSCODE_SESSION_PATHS",
        ",".join(config.copilot_vscode_session_paths),
    )
    upsert_env_var(path, prefix + "USE_SSHPASS", "1" if config.use_sshpass else "0")
    return alias


def probe_remote_ssh(
    config: RemoteHostConfig,
    timeout_sec: int = 10,
    *,
    ssh_password: str | None = None,
) -> tuple[bool, str]:
    password = ssh_password if ssh_password is not None else os.environ.get("SSHPASS", "")
    if config.use_sshpass and not password.strip():
        return False, "SSH 密码模式需要提供密码"

    command, env = _ssh_command_and_env(
        f"{config.ssh_user}@{config.ssh_host}",
        config.ssh_port,
        ["true"],
        use_sshpass=config.use_sshpass,
        ssh_password=password,
    )

    try:
        run_kwargs = {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": max(3, timeout_sec),
        }
        if env is not None:
            run_kwargs["env"] = env
        completed = subprocess.run(command, **run_kwargs)
    except FileNotFoundError:
        if config.use_sshpass:
            return False, "sshpass 未找到"
        return False, "SSH 命令未找到"
    except subprocess.TimeoutExpired:
        return False, "SSH 连接超时"
    if completed.returncode == 0:
        return True, "SSH 连接正常"
    message = completed.stderr.strip() or completed.stdout.strip() or "SSH 连接失败"
    return False, message


def default_source_label(ssh_user: str, ssh_host: str) -> str:
    return f"{ssh_user.strip()}@{ssh_host.strip()}"


def normalize_alias(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()
    return cleaned or "REMOTE"


def unique_alias(base: str, existing_aliases: list[str]) -> str:
    candidate = normalize_alias(base)
    used = {normalize_alias(alias) for alias in existing_aliases}
    if candidate not in used:
        return candidate
    idx = 2
    while f"{candidate}_{idx}" in used:
        idx += 1
    return f"{candidate}_{idx}"


def _split_aliases(raw: str) -> list[str]:
    out: list[str] = []
    for alias in raw.split(","):
        if alias.strip():
            out.append(normalize_alias(alias))
    return out


def _split_paths(raw: str, default: list[str]) -> list[str]:
    if not raw.strip():
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_flag(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_port(raw: str) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 22
