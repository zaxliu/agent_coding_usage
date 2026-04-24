from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from llm_usage.collectors.base import BaseCollector
from llm_usage.collectors.remote_file import (
    RemoteCollectJob,
    RemoteFileCollector,
    SshTarget,
    _is_ssh_auth_failure,
    _ssh_command_and_env,
)
from llm_usage.collectors.cline import ClineRemoteCollector, default_remote_cline_vscode_paths
from llm_usage.env import EnvDocument, upsert_env_var
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
DEFAULT_REMOTE_CLINE_VSCODE_SESSION_PATHS = default_remote_cline_vscode_paths()


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
    cline_vscode_session_paths: list[str] = field(default_factory=list)
    is_ephemeral: bool = False
    use_sshpass: bool = False
    ssh_jump_host: str = ""
    ssh_jump_port: int = 2222


@dataclass
class RemoteDraft:
    alias: str
    ssh_host: str
    ssh_user: str
    ssh_port: int
    source_label: str
    claude_log_paths: list[str]
    codex_log_paths: list[str]
    copilot_cli_log_paths: list[str]
    copilot_vscode_session_paths: list[str]
    cline_vscode_session_paths: list[str] = field(default_factory=list)
    use_sshpass: bool = False
    ssh_jump_host: str = ""
    ssh_jump_port: int = 2222


RemoteValidator = Callable[[RemoteHostConfig, Optional[str]], tuple[bool, str]]


def is_ssh_auth_failure_message(message: str) -> bool:
    return _is_ssh_auth_failure(message or "")


def parse_remote_configs_from_env(env: Optional[dict[str, str]] = None) -> list[RemoteHostConfig]:
    data = os.environ if env is None else env
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
        cline_vscode_session_paths = _split_paths(
            data.get(prefix + "CLINE_VSCODE_SESSION_PATHS", ""),
            DEFAULT_REMOTE_CLINE_VSCODE_SESSION_PATHS,
        )
        ssh_jump_host = data.get(prefix + "SSH_JUMP_HOST", "").strip()
        ssh_jump_port = _safe_port(data.get(prefix + "SSH_JUMP_PORT", "2222"), default=2222)
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
                cline_vscode_session_paths=cline_vscode_session_paths,
                use_sshpass=_env_flag(data.get(prefix + "USE_SSHPASS", "")),
                ssh_jump_host=ssh_jump_host,
                ssh_jump_port=ssh_jump_port,
            )
        )
    return out


def drafts_from_env_document(document: EnvDocument) -> list[RemoteDraft]:
    env: dict[str, str] = {}
    for line in document.lines:
        if line.kind != "entry" or line.key is None or line.value is None:
            continue
        env[line.key] = line.value
    configs = parse_remote_configs_from_env(env)
    return [
        RemoteDraft(
            alias=config.alias,
            ssh_host=config.ssh_host,
            ssh_user=config.ssh_user,
            ssh_port=config.ssh_port,
            source_label=config.source_label,
            claude_log_paths=list(config.claude_log_paths),
            codex_log_paths=list(config.codex_log_paths),
            copilot_cli_log_paths=list(config.copilot_cli_log_paths),
            copilot_vscode_session_paths=list(config.copilot_vscode_session_paths),
            cline_vscode_session_paths=list(config.cline_vscode_session_paths),
            use_sshpass=config.use_sshpass,
            ssh_jump_host=config.ssh_jump_host,
            ssh_jump_port=config.ssh_jump_port,
        )
        for config in configs
    ]


def apply_remote_drafts_to_document(document: EnvDocument, drafts: list[RemoteDraft]) -> None:
    remote_keys = []
    for line in document.lines:
        if line.kind == "entry" and line.key and line.key.startswith("REMOTE_"):
            remote_keys.append(line.key)
    for key in remote_keys:
        document.delete(key)

    if not drafts:
        return

    existing_aliases: list[str] = []
    normalized_drafts: list[tuple[str, RemoteDraft]] = []
    for draft in drafts:
        alias = unique_alias(draft.alias, existing_aliases)
        existing_aliases.append(alias)
        normalized_drafts.append((alias, draft))

    document.set("REMOTE_HOSTS", ",".join(alias for alias, _ in normalized_drafts))
    for alias, draft in normalized_drafts:
        prefix = f"REMOTE_{alias}_"
        document.set(prefix + "SSH_HOST", draft.ssh_host)
        document.set(prefix + "SSH_USER", draft.ssh_user)
        document.set(prefix + "SSH_PORT", str(draft.ssh_port))
        document.set(prefix + "LABEL", draft.source_label)
        document.set(prefix + "CLAUDE_LOG_PATHS", ",".join(draft.claude_log_paths))
        document.set(prefix + "CODEX_LOG_PATHS", ",".join(draft.codex_log_paths))
        document.set(prefix + "COPILOT_CLI_LOG_PATHS", ",".join(draft.copilot_cli_log_paths))
        document.set(prefix + "COPILOT_VSCODE_SESSION_PATHS", ",".join(draft.copilot_vscode_session_paths))
        document.set(prefix + "CLINE_VSCODE_SESSION_PATHS", ",".join(draft.cline_vscode_session_paths))
        document.set(prefix + "USE_SSHPASS", "1" if draft.use_sshpass else "0")
        if draft.ssh_jump_host:
            document.set(prefix + "SSH_JUMP_HOST", draft.ssh_jump_host)
            document.set(prefix + "SSH_JUMP_PORT", str(draft.ssh_jump_port))

def build_remote_collectors(
    configs: list[RemoteHostConfig],
    username: str,
    salt: str,
    runtime_passwords: Optional[dict[str, str]] = None,
    skip_tools: Optional[set] = None,
) -> list[BaseCollector]:
    collectors: list[BaseCollector] = []
    runtime_passwords = runtime_passwords or {}
    skip = skip_tools or set()
    for config in configs:
        source_host_hash = hash_source_host(username, config.source_label, salt)
        target = SshTarget(host=config.ssh_host, user=config.ssh_user, port=config.ssh_port,
                           jump_host=config.ssh_jump_host, jump_port=config.ssh_jump_port)
        jobs = []
        if config.claude_log_paths and "claude_code" not in skip:
            jobs.append(RemoteCollectJob(tool="claude_code", patterns=config.claude_log_paths))
        if config.codex_log_paths and "codex" not in skip:
            jobs.append(RemoteCollectJob(tool="codex", patterns=config.codex_log_paths))
        if config.copilot_cli_log_paths and "copilot_cli" not in skip:
            jobs.append(RemoteCollectJob(tool="copilot_cli", patterns=config.copilot_cli_log_paths))
        if config.copilot_vscode_session_paths and "copilot_vscode" not in skip:
            jobs.append(RemoteCollectJob(tool="copilot_vscode", patterns=config.copilot_vscode_session_paths))
        if config.cline_vscode_session_paths and "cline_vscode" not in skip:
            jobs.append(RemoteCollectJob(tool="cline_vscode", patterns=config.cline_vscode_session_paths))
        if jobs:
            collector_cls = ClineRemoteCollector if config.cline_vscode_session_paths else RemoteFileCollector
            collectors.append(
                collector_cls(
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
    claude_log_paths: Optional[list[str]] = None,
    codex_log_paths: Optional[list[str]] = None,
    use_sshpass: bool = False,
    ssh_jump_host: str = "",
    ssh_jump_port: int = 2222,
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
        claude_log_paths=list(DEFAULT_REMOTE_CLAUDE_LOG_PATHS) if claude_log_paths is None else list(claude_log_paths),
        codex_log_paths=list(DEFAULT_REMOTE_CODEX_LOG_PATHS) if codex_log_paths is None else list(codex_log_paths),
        copilot_cli_log_paths=list(DEFAULT_REMOTE_COPILOT_CLI_LOG_PATHS),
        copilot_vscode_session_paths=list(DEFAULT_REMOTE_COPILOT_VSCODE_SESSION_PATHS),
        cline_vscode_session_paths=list(DEFAULT_REMOTE_CLINE_VSCODE_SESSION_PATHS),
        is_ephemeral=True,
        use_sshpass=use_sshpass,
        ssh_jump_host=ssh_jump_host,
        ssh_jump_port=ssh_jump_port,
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
    upsert_env_var(
        path,
        prefix + "CLINE_VSCODE_SESSION_PATHS",
        ",".join(config.cline_vscode_session_paths),
    )
    upsert_env_var(path, prefix + "USE_SSHPASS", "1" if config.use_sshpass else "0")
    if config.ssh_jump_host:
        upsert_env_var(path, prefix + "SSH_JUMP_HOST", config.ssh_jump_host)
        upsert_env_var(path, prefix + "SSH_JUMP_PORT", str(config.ssh_jump_port))
    return alias


def probe_remote_ssh(
    config: RemoteHostConfig,
    timeout_sec: int = 10,
    *,
    ssh_password: Optional[str] = None,
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
        jump_host=config.ssh_jump_host,
        jump_port=config.ssh_jump_port,
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
        if command and command[0] == "sshpass":
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


def _safe_port(raw: str, default: int = 22) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default
