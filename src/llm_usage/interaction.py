from __future__ import annotations

import getpass
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TextIO

from llm_usage.env import EnvDocument, load_env_document, save_env_document
from llm_usage.remotes import (
    RemoteDraft,
    RemoteHostConfig,
    RemoteValidator,
    apply_remote_drafts_to_document,
    build_temporary_remote,
    default_source_label,
    drafts_from_env_document,
    normalize_alias,
    probe_remote_ssh,
    unique_alias,
)

try:
    from prompt_toolkit import prompt as pt_prompt
except ImportError:  # pragma: no cover
    pt_prompt = None


@dataclass(frozen=True)
class RemoteSelectionResult:
    selected_aliases: list[str]
    temporary_remotes: list[RemoteHostConfig]
    mode_used: str
    runtime_passwords: dict[str, str] = field(default_factory=dict)


@dataclass
class ConfigDraft:
    document: EnvDocument
    values: dict[str, str]
    remotes: list[RemoteDraft]
    dirty: bool = False

    @classmethod
    def from_document(cls, document: EnvDocument) -> "ConfigDraft":
        values: dict[str, str] = {}
        for line in document.lines:
            if line.kind != "entry" or line.key is None:
                continue
            if line.key.startswith("REMOTE_"):
                continue
            values[line.key] = line.value or ""
        remotes = drafts_from_env_document(document)
        return cls(document=document, values=values, remotes=remotes)


BASIC_KEYS = [
    "ORG_USERNAME",
    "HASH_SALT",
    "TIMEZONE",
    "LOOKBACK_DAYS",
]

FEISHU_KEYS = [
    "FEISHU_APP_TOKEN",
    "FEISHU_TABLE_ID",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BOT_TOKEN",
]

CURSOR_KEYS = [
    "CURSOR_LOG_PATHS",
    "CURSOR_WEB_SESSION_TOKEN",
    "CURSOR_WEB_WORKOS_ID",
    "CURSOR_DASHBOARD_BASE_URL",
    "CURSOR_DASHBOARD_TEAM_ID",
    "CURSOR_DASHBOARD_PAGE_SIZE",
    "CURSOR_DASHBOARD_TIMEOUT_SEC",
]

ADVANCED_KEYS = [
    "CLAUDE_LOG_PATHS",
    "CODEX_LOG_PATHS",
    "COPILOT_CLI_LOG_PATHS",
    "COPILOT_VSCODE_SESSION_PATHS",
]

KNOWN_CONFIG_KEYS = BASIC_KEYS + FEISHU_KEYS + CURSOR_KEYS + ADVANCED_KEYS


def can_use_tui() -> bool:
    return pt_prompt is not None


def select_remotes(
    configs: list[RemoteHostConfig],
    default_aliases: list[str],
    ui_mode: str = "auto",
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    remote_validator: Optional[RemoteValidator] = None,
    password_getter: Optional[Callable[[], Optional[str]]] = None,
    password_setter: Optional[Callable[[str], None]] = None,
    interactive_password_reader: Optional[Callable[[str], str]] = None,
) -> RemoteSelectionResult:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    remote_validator = remote_validator or probe_remote_ssh
    runtime_passwords: dict[str, str] = {}
    if ui_mode == "none" or not _is_interactive(stdin, stdout):
        return RemoteSelectionResult(
            selected_aliases=list(default_aliases),
            temporary_remotes=[],
            mode_used="none",
            runtime_passwords=runtime_passwords,
        )

    use_prompt_toolkit = ui_mode == "tui" or (ui_mode == "auto" and can_use_tui())
    if not configs:
        return _select_without_configs(
            stdin,
            stdout,
            mode_used="tui" if use_prompt_toolkit else "cli",
            use_prompt_toolkit=use_prompt_toolkit,
            remote_validator=remote_validator,
            password_getter=password_getter,
            password_setter=password_setter,
            interactive_password_reader=interactive_password_reader,
            runtime_passwords=runtime_passwords,
        )
    return _select_with_list(
        configs,
        default_aliases,
        stdin=stdin,
        stdout=stdout,
        mode_used="tui" if use_prompt_toolkit else "cli",
        use_prompt_toolkit=use_prompt_toolkit,
        remote_validator=remote_validator,
        password_getter=password_getter,
        password_setter=password_setter,
        interactive_password_reader=interactive_password_reader,
        runtime_passwords=runtime_passwords,
    )


def confirm_save_temporary_remote(
    config: RemoteHostConfig,
    ui_mode: str = "auto",
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> bool:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not _is_interactive(stdin, stdout):
        return False
    answer = _read_line(
        "是否将这个临时远端保存到 .env？[y/N]: ",
        stdin=stdin,
        stdout=stdout,
        use_prompt_toolkit=(ui_mode == "tui" or (ui_mode == "auto" and can_use_tui())),
    ).strip().lower()
    return answer in {"y", "yes", "是", "确认"}


def run_config_editor(
    env_path: Path,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    draft = ConfigDraft.from_document(load_env_document(env_path))

    while True:
        dirty_mark = " *" if draft.dirty else ""
        stdout.write(f"Config{dirty_mark}\n")
        stdout.write("  1. Basic\n")
        stdout.write("  2. Feishu\n")
        stdout.write("  3. Cursor\n")
        stdout.write("  4. Remotes\n")
        stdout.write("  5. Advanced / Raw Env\n")
        stdout.write("  s. Save\n")
        stdout.write("  d. Discard\n")
        stdout.write("  q. Quit\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "":
            answer = "q"
        if answer == "1":
            _edit_key_menu(draft, "Basic", BASIC_KEYS, stdin=stdin, stdout=stdout)
            continue
        if answer == "2":
            _edit_key_menu(draft, "Feishu", FEISHU_KEYS, stdin=stdin, stdout=stdout)
            continue
        if answer == "3":
            _edit_key_menu(draft, "Cursor", CURSOR_KEYS, stdin=stdin, stdout=stdout)
            continue
        if answer == "4":
            _edit_remotes_menu(draft, stdin=stdin, stdout=stdout)
            continue
        if answer == "5":
            _edit_raw_env_menu(draft, stdin=stdin, stdout=stdout)
            continue
        if answer == "s":
            _save_config_draft(env_path, draft)
            return 0
        if answer == "d":
            return 0
        if answer == "q":
            if not draft.dirty:
                return 0
            decision = _read_line(
                "未保存的更改：s=保存并退出，d=丢弃并退出，其他任意键继续编辑：",
                stdin=stdin,
                stdout=stdout,
                use_prompt_toolkit=False,
            ).strip().lower()
            if decision == "s":
                _save_config_draft(env_path, draft)
                return 0
            if decision == "d":
                return 0


def _save_config_draft(env_path: Path, draft: ConfigDraft) -> None:
    existing_non_remote_keys = {
        line.key
        for line in draft.document.lines
        if line.kind == "entry" and line.key is not None and not line.key.startswith("REMOTE_")
    }
    for key in existing_non_remote_keys - set(draft.values):
        draft.document.delete(key)
    for key, value in draft.values.items():
        draft.document.set(key, value)
    apply_remote_drafts_to_document(draft.document, draft.remotes)
    save_env_document(env_path, draft.document)
    draft.dirty = False


def _edit_key_menu(
    draft: ConfigDraft,
    title: str,
    keys: list[str],
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    while True:
        stdout.write(f"{title}\n")
        for index, key in enumerate(keys, start=1):
            stdout.write(f"  {index}. {key} = {draft.values.get(key, '')}\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return
        if not answer.isdigit():
            continue
        index = int(answer) - 1
        if not 0 <= index < len(keys):
            continue
        key = keys[index]
        new_value = _read_line(
            f"{key}: ",
            stdin=stdin,
            stdout=stdout,
            use_prompt_toolkit=False,
        )
        if draft.values.get(key, "") != new_value:
            draft.values[key] = new_value
            draft.dirty = True
        return


def _edit_raw_env_menu(draft: ConfigDraft, stdin: TextIO, stdout: TextIO) -> None:
    while True:
        keys = sorted(draft.values)
        stdout.write("Advanced / Raw Env\n")
        for index, key in enumerate(keys, start=1):
            stdout.write(f"  {index}. {key} = {draft.values[key]}\n")
        stdout.write("  a. Add key\n")
        stdout.write("  d. Delete key\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return
        if answer == "a":
            key = _read_line("Key: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().upper()
            if not key or key.startswith("REMOTE_"):
                continue
            value = _read_line("Value: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False)
            if draft.values.get(key) != value:
                draft.values[key] = value
                draft.dirty = True
            continue
        if answer == "d":
            raw_index = _read_line("Delete which key: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if not raw_index.isdigit():
                continue
            index = int(raw_index) - 1
            if not 0 <= index < len(keys):
                continue
            removed = keys[index]
            draft.values.pop(removed, None)
            draft.dirty = True
            continue
        if answer.isdigit():
            index = int(answer) - 1
            if not 0 <= index < len(keys):
                continue
            key = keys[index]
            new_value = _read_line(f"{key}: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False)
            if draft.values.get(key, "") != new_value:
                draft.values[key] = new_value
                draft.dirty = True
            return


def _edit_remotes_menu(draft: ConfigDraft, stdin: TextIO, stdout: TextIO) -> None:
    while True:
        stdout.write("Remotes\n")
        for index, remote in enumerate(draft.remotes, start=1):
            stdout.write(f"  {index}. {remote.alias} {remote.ssh_user}@{remote.ssh_host}:{remote.ssh_port}\n")
        stdout.write("  a. Add remote\n")
        stdout.write("  e. Edit remote\n")
        stdout.write("  d. Delete remote\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return
        if answer == "a":
            remote = _prompt_remote(existing_aliases=[item.alias for item in draft.remotes], stdin=stdin, stdout=stdout)
            if remote is None:
                continue
            draft.remotes.append(remote)
            changed = _edit_remote_detail(
                remote,
                existing_aliases=[item.alias for item in draft.remotes if item is not remote],
                stdin=stdin,
                stdout=stdout,
            )
            draft.dirty = True or changed
            continue
        if answer == "e":
            index = _read_menu_index("Edit which remote: ", len(draft.remotes), stdin=stdin, stdout=stdout)
            if index is None:
                continue
            remote = draft.remotes[index]
            if _edit_remote_detail(
                remote,
                existing_aliases=[item.alias for idx, item in enumerate(draft.remotes) if idx != index],
                stdin=stdin,
                stdout=stdout,
            ):
                draft.dirty = True
            continue
        if answer == "d":
            index = _read_menu_index("Delete which remote: ", len(draft.remotes), stdin=stdin, stdout=stdout)
            if index is None:
                continue
            draft.remotes.pop(index)
            draft.dirty = True


def _prompt_remote(existing_aliases: list[str], stdin: TextIO, stdout: TextIO) -> Optional[RemoteDraft]:
    alias_raw = _read_line("Alias: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
    host = _read_line("SSH host: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
    user = _read_line("SSH user: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
    if not host or not user:
        stdout.write("SSH host 和 SSH user 为必填项。\n")
        return None
    port = _read_port(stdin=stdin, stdout=stdout, prompt_text="SSH port [22]: ", default=22)
    default_label = default_source_label(user, host)
    label = _read_line(
        f"Label [{default_label}]: ",
        stdin=stdin,
        stdout=stdout,
        use_prompt_toolkit=False,
    ).strip() or default_label
    use_sshpass = _prompt_yes_no("Use sshpass? [y/N]: ", stdin=stdin, stdout=stdout)
    alias_seed = alias_raw or label or default_label
    alias = unique_alias(normalize_alias(alias_seed), existing_aliases)
    return RemoteDraft(
        alias=alias,
        ssh_host=host,
        ssh_user=user,
        ssh_port=port,
        source_label=label,
        claude_log_paths=[],
        codex_log_paths=[],
        copilot_cli_log_paths=[],
        copilot_vscode_session_paths=[],
        use_sshpass=use_sshpass,
    )


def _edit_remote_detail(
    remote: RemoteDraft,
    existing_aliases: list[str],
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    changed = False
    while True:
        stdout.write("Remote Detail\n")
        stdout.write(f"  1. Alias = {remote.alias}\n")
        stdout.write(f"  2. SSH host = {remote.ssh_host}\n")
        stdout.write(f"  3. SSH user = {remote.ssh_user}\n")
        stdout.write(f"  4. SSH port = {remote.ssh_port}\n")
        stdout.write(f"  5. Label = {remote.source_label}\n")
        stdout.write(f"  6. Use sshpass = {'yes' if remote.use_sshpass else 'no'}\n")
        stdout.write("  p. Edit paths\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return changed
        if answer == "1":
            alias_input = _read_line("Alias: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if alias_input:
                next_alias = unique_alias(alias_input, existing_aliases)
                if next_alias != remote.alias:
                    remote.alias = next_alias
                    changed = True
        elif answer == "2":
            next_host = _read_line("SSH host: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if next_host and next_host != remote.ssh_host:
                remote.ssh_host = next_host
                changed = True
        elif answer == "3":
            next_user = _read_line("SSH user: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if next_user and next_user != remote.ssh_user:
                remote.ssh_user = next_user
                changed = True
        elif answer == "4":
            next_port = _read_port(stdin=stdin, stdout=stdout, prompt_text="SSH port: ", default=remote.ssh_port)
            if next_port != remote.ssh_port:
                remote.ssh_port = next_port
                changed = True
        elif answer == "5":
            next_label = _read_line("Label: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if next_label and next_label != remote.source_label:
                remote.source_label = next_label
                changed = True
        elif answer == "6":
            next_use_sshpass = _prompt_yes_no("Use sshpass? [y/N]: ", stdin=stdin, stdout=stdout)
            if next_use_sshpass != remote.use_sshpass:
                remote.use_sshpass = next_use_sshpass
                changed = True
        elif answer == "p":
            if _edit_remote_paths(remote, stdin=stdin, stdout=stdout):
                changed = True


def _edit_remote_paths(remote: RemoteDraft, stdin: TextIO, stdout: TextIO) -> bool:
    changed = False
    while True:
        stdout.write("Remote Paths\n")
        stdout.write(f"  1. Claude ({len(remote.claude_log_paths)})\n")
        stdout.write(f"  2. Codex ({len(remote.codex_log_paths)})\n")
        stdout.write(f"  3. Copilot CLI ({len(remote.copilot_cli_log_paths)})\n")
        stdout.write(f"  4. Copilot VSCode ({len(remote.copilot_vscode_session_paths)})\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return changed
        if answer == "1":
            if _edit_path_list(remote.claude_log_paths, stdin=stdin, stdout=stdout):
                changed = True
        elif answer == "2":
            if _edit_path_list(remote.codex_log_paths, stdin=stdin, stdout=stdout):
                changed = True
        elif answer == "3":
            if _edit_path_list(remote.copilot_cli_log_paths, stdin=stdin, stdout=stdout):
                changed = True
        elif answer == "4":
            if _edit_path_list(remote.copilot_vscode_session_paths, stdin=stdin, stdout=stdout):
                changed = True


def _edit_path_list(values: list[str], stdin: TextIO, stdout: TextIO) -> bool:
    changed = False
    while True:
        stdout.write("Path List\n")
        for index, value in enumerate(values, start=1):
            stdout.write(f"  {index}. {value}\n")
        stdout.write("  a. Add path\n")
        stdout.write("  d. Delete path\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "b" or answer == "":
            return changed
        if answer == "a":
            new_value = _read_line("Path: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if new_value:
                values.append(new_value)
                changed = True
            continue
        if answer == "d":
            index = _read_menu_index("Delete which path: ", len(values), stdin=stdin, stdout=stdout)
            if index is not None:
                values.pop(index)
                changed = True


def _read_menu_index(prompt_text: str, size: int, stdin: TextIO, stdout: TextIO) -> Optional[int]:
    if size <= 0:
        return None
    raw = _read_line(prompt_text, stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
    if not raw.isdigit():
        return None
    index = int(raw) - 1
    if not 0 <= index < size:
        return None
    return index


def _read_port(stdin: TextIO, stdout: TextIO, prompt_text: str, default: int) -> int:
    while True:
        raw = _read_line(prompt_text, stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
        if not raw:
            return default
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            stdout.write("端口格式不正确，请重新输入。\n")


def _prompt_yes_no(prompt_text: str, stdin: TextIO, stdout: TextIO) -> bool:
    answer = _read_line(prompt_text, stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
    return answer in {"y", "yes", "是", "确认"}


def _select_with_list(
    configs: list[RemoteHostConfig],
    default_aliases: list[str],
    stdin: TextIO,
    stdout: TextIO,
    mode_used: str,
    use_prompt_toolkit: bool,
    remote_validator: RemoteValidator,
    password_getter: Optional[Callable[[], Optional[str]]],
    password_setter: Optional[Callable[[str], None]],
    interactive_password_reader: Optional[Callable[[str], str]],
    runtime_passwords: dict[str, str],
) -> RemoteSelectionResult:
    alias_map = {config.alias: config for config in configs}
    temporary_remotes: list[RemoteHostConfig] = []
    while True:
        stdout.write("远端选择\n")
        for idx, config in enumerate(configs, start=1):
            mark = "x" if config.alias in default_aliases else " "
            stdout.write(f"  [{mark}] {idx}. {_describe(config)}\n")
        stdout.write("  [+] 新增临时远端\n")
        stdout.write("输入说明：回车=使用默认，all=全选，none=仅本机，1,2 或 ALIAS 选择，+=新增临时远端\n")
        default_label = "、".join(alias.lower() for alias in default_aliases) if default_aliases else "仅本机"
        answer = _read_line(
            f"本次远端选择（默认：{default_label}）：",
            stdin=stdin,
            stdout=stdout,
            use_prompt_toolkit=use_prompt_toolkit,
        )
        if answer == "":
            return RemoteSelectionResult(
                selected_aliases=list(default_aliases),
                temporary_remotes=[],
                mode_used=mode_used,
                runtime_passwords=dict(runtime_passwords),
            )
        raw = answer.strip()
        if not raw:
            return RemoteSelectionResult(
                selected_aliases=list(default_aliases),
                temporary_remotes=[],
                mode_used=mode_used,
                runtime_passwords=dict(runtime_passwords),
            )
        lower = raw.lower()
        if lower == "all":
            return RemoteSelectionResult(
                selected_aliases=list(alias_map),
                temporary_remotes=[],
                mode_used=mode_used,
                runtime_passwords=dict(runtime_passwords),
            )
        if lower == "none":
            return RemoteSelectionResult(selected_aliases=[], temporary_remotes=[], mode_used=mode_used, runtime_passwords=dict(runtime_passwords))
        if raw == "+":
            temp = _prompt_temporary_remote(
                stdin,
                stdout,
                use_prompt_toolkit,
                remote_validator,
                password_getter=password_getter,
                password_setter=password_setter,
                interactive_password_reader=interactive_password_reader,
                runtime_passwords=runtime_passwords,
            )
            if temp is not None:
                temporary_remotes.append(temp)
                return RemoteSelectionResult(
                    selected_aliases=list(default_aliases),
                    temporary_remotes=temporary_remotes,
                    mode_used=mode_used,
                    runtime_passwords=dict(runtime_passwords),
                )
            continue
        resolved: list[str] = []
        seen_aliases: set[str] = set()
        valid = True
        for token in [item.strip() for item in raw.split(",") if item.strip()]:
            resolved_alias: Optional[str] = None
            if token.isdigit():
                idx = int(token)
                if 1 <= idx <= len(configs):
                    resolved_alias = configs[idx - 1].alias
            else:
                token_upper = token.upper()
                if token_upper in alias_map:
                    resolved_alias = token_upper
            if resolved_alias is not None:
                if resolved_alias not in seen_aliases:
                    resolved.append(resolved_alias)
                    seen_aliases.add(resolved_alias)
                continue
            valid = False
            break
        if valid and resolved:
            return RemoteSelectionResult(
                selected_aliases=resolved,
                temporary_remotes=temporary_remotes,
                mode_used=mode_used,
                runtime_passwords=dict(runtime_passwords),
            )
        stdout.write("输入无效，请重试。\n")


def _select_without_configs(
    stdin: TextIO,
    stdout: TextIO,
    mode_used: str,
    use_prompt_toolkit: bool,
    remote_validator: RemoteValidator,
    password_getter: Optional[Callable[[], Optional[str]]],
    password_setter: Optional[Callable[[str], None]],
    interactive_password_reader: Optional[Callable[[str], str]],
    runtime_passwords: dict[str, str],
) -> RemoteSelectionResult:
    stdout.write("当前 .env 中还没有配置远端。\n")
    answer = _read_line(
        "回车表示仅统计本机，输入 + 新增一个临时远端：",
        stdin=stdin,
        stdout=stdout,
        use_prompt_toolkit=use_prompt_toolkit,
    )
    if answer.strip() != "+":
        return RemoteSelectionResult(selected_aliases=[], temporary_remotes=[], mode_used=mode_used, runtime_passwords=dict(runtime_passwords))
    temp = _prompt_temporary_remote(
        stdin,
        stdout,
        use_prompt_toolkit,
        remote_validator,
        password_getter=password_getter,
        password_setter=password_setter,
        interactive_password_reader=interactive_password_reader,
        runtime_passwords=runtime_passwords,
    )
    return RemoteSelectionResult(
        selected_aliases=[],
        temporary_remotes=[temp] if temp else [],
        mode_used=mode_used,
        runtime_passwords=dict(runtime_passwords),
    )


def _prompt_temporary_remote(
    stdin: TextIO,
    stdout: TextIO,
    use_prompt_toolkit: bool,
    remote_validator: RemoteValidator,
    password_getter: Optional[Callable[[], Optional[str]]],
    password_setter: Optional[Callable[[str], None]],
    interactive_password_reader: Optional[Callable[[str], str]],
    runtime_passwords: dict[str, str],
) -> Optional[RemoteHostConfig]:
    def _clear_cached_password() -> None:
        if password_setter is not None:
            password_setter("")

    while True:
        stdout.write("新增临时远端\n")
        host = _read_line("SSH 主机：", stdin=stdin, stdout=stdout, use_prompt_toolkit=use_prompt_toolkit).strip()
        if not host:
            return None
        user = _read_line("SSH 用户：", stdin=stdin, stdout=stdout, use_prompt_toolkit=use_prompt_toolkit).strip()
        if not user:
            return None
        while True:
            port_raw = _read_line(
                "SSH 端口 [22]：",
                stdin=stdin,
                stdout=stdout,
                use_prompt_toolkit=use_prompt_toolkit,
            ).strip() or "22"
            try:
                port = int(port_raw)
                if port <= 0:
                    raise ValueError
                break
            except ValueError:
                stdout.write("端口格式不正确，请重新输入。\n")
        use_sshpass = _prompt_use_sshpass(stdin, stdout, use_prompt_toolkit)
        ssh_password = None
        if use_sshpass:
            ssh_password = password_getter() if password_getter is not None else None
            if ssh_password is not None and not ssh_password.strip():
                ssh_password = None
            if ssh_password is None:
                ssh_password = _read_password(
                    "SSH 密码：",
                    stdin=stdin,
                    stdout=stdout,
                    use_prompt_toolkit=use_prompt_toolkit,
                    interactive_password_reader=interactive_password_reader,
                )
            if not ssh_password.strip():
                stdout.write("密码不能为空。\n")
                retry = _read_line(
                    "输入 r 重新填写，其他任意输入取消：",
                    stdin=stdin,
                    stdout=stdout,
                    use_prompt_toolkit=use_prompt_toolkit,
                ).strip().lower()
                if retry != "r":
                    return None
                continue
            if password_setter is not None:
                password_setter(ssh_password)
        config = build_temporary_remote(host, user, port, use_sshpass=use_sshpass)
        stdout.write("正在检查 SSH 连通性...\n")
        ok, message = _invoke_remote_validator(remote_validator, config, ssh_password=ssh_password)
        if ok:
            if ssh_password is not None:
                runtime_passwords[config.alias] = ssh_password
            stdout.write(f"SSH 检查通过：{message}\n")
            return config
        stdout.write(f"SSH 检查失败：{message}\n")
        if ssh_password is not None:
            _clear_cached_password()
        retry = _read_line(
            "输入 r 重新填写，其他任意输入取消：",
            stdin=stdin,
            stdout=stdout,
            use_prompt_toolkit=use_prompt_toolkit,
        ).strip().lower()
        if retry != "r":
            return None


def _prompt_use_sshpass(stdin: TextIO, stdout: TextIO, use_prompt_toolkit: bool) -> bool:
    answer = _read_line(
        "是否使用 sshpass？[y/N]：",
        stdin=stdin,
        stdout=stdout,
        use_prompt_toolkit=use_prompt_toolkit,
    ).strip().lower()
    return answer in {"y", "yes", "是", "确认"}
def _read_password(
    prompt_text: str,
    stdin: TextIO,
    stdout: TextIO,
    use_prompt_toolkit: bool,
    interactive_password_reader: Optional[Callable[[str], str]],
) -> str:
    if interactive_password_reader is not None:
        return interactive_password_reader(prompt_text)
    if use_prompt_toolkit and pt_prompt is not None and _is_interactive(stdin, stdout):
        return pt_prompt(prompt_text, is_password=True)
    return getpass.getpass(prompt_text)


def _invoke_remote_validator(
    remote_validator: RemoteValidator,
    config: RemoteHostConfig,
    ssh_password: Optional[str],
) -> tuple[bool, str]:
    try:
        signature = inspect.signature(remote_validator)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        params = list(signature.parameters.values())
        has_keyword = "ssh_password" in signature.parameters
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
        positional_params = [
            param
            for param in params
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if has_keyword or accepts_kwargs:
            return remote_validator(config, ssh_password=ssh_password)
        if len(positional_params) >= 2:
            return remote_validator(config, ssh_password)
    return remote_validator(config)


def _read_line(prompt_text: str, stdin: TextIO, stdout: TextIO, use_prompt_toolkit: bool) -> str:
    if use_prompt_toolkit and pt_prompt is not None and _is_interactive(stdin, stdout):
        return pt_prompt(prompt_text)
    stdout.write(prompt_text)
    stdout.flush()
    answer = stdin.readline()
    if answer == "":
        return ""
    return answer.rstrip("\n")


def _describe(config: RemoteHostConfig) -> str:
    details = f"{config.ssh_user}@{config.ssh_host}:{config.ssh_port}"
    if len(details) > 28:
        details = details[:25] + "..."
    return f"{config.alias.lower()} ({details})"


def _is_interactive(stdin: TextIO, stdout: TextIO) -> bool:
    return bool(getattr(stdin, "isatty", lambda: False)() and getattr(stdout, "isatty", lambda: False)())
