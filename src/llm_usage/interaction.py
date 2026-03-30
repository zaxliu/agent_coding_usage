from __future__ import annotations

import getpass
import inspect
import sys
from dataclasses import dataclass, field
from typing import Callable, TextIO

from llm_usage.remotes import RemoteHostConfig, RemoteValidator, build_temporary_remote, probe_remote_ssh

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


def can_use_tui() -> bool:
    return pt_prompt is not None


def select_remotes(
    configs: list[RemoteHostConfig],
    default_aliases: list[str],
    ui_mode: str = "auto",
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    remote_validator: RemoteValidator | None = None,
    password_getter: Callable[[], str | None] | None = None,
    password_setter: Callable[[str], None] | None = None,
    interactive_password_reader: Callable[[str], str] | None = None,
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
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
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


def _select_with_list(
    configs: list[RemoteHostConfig],
    default_aliases: list[str],
    stdin: TextIO,
    stdout: TextIO,
    mode_used: str,
    use_prompt_toolkit: bool,
    remote_validator: RemoteValidator,
    password_getter: Callable[[], str | None] | None,
    password_setter: Callable[[str], None] | None,
    interactive_password_reader: Callable[[str], str] | None,
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
        valid = True
        for token in [item.strip() for item in raw.split(",") if item.strip()]:
            if token.isdigit():
                idx = int(token)
                if 1 <= idx <= len(configs):
                    resolved.append(configs[idx - 1].alias)
                    continue
            token_upper = token.upper()
            if token_upper in alias_map:
                resolved.append(token_upper)
                continue
            valid = False
            break
        if valid:
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
    password_getter: Callable[[], str | None] | None,
    password_setter: Callable[[str], None] | None,
    interactive_password_reader: Callable[[str], str] | None,
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
    password_getter: Callable[[], str | None] | None,
    password_setter: Callable[[str], None] | None,
    interactive_password_reader: Callable[[str], str] | None,
    runtime_passwords: dict[str, str],
) -> RemoteHostConfig | None:
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
        if ssh_password is not None:
            runtime_passwords[config.alias] = ssh_password
        stdout.write("正在检查 SSH 连通性...\n")
        ok, message = _invoke_remote_validator(remote_validator, config, ssh_password=ssh_password)
        if ok:
            stdout.write(f"SSH 检查通过：{message}\n")
            return config
        stdout.write(f"SSH 检查失败：{message}\n")
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
    interactive_password_reader: Callable[[str], str] | None,
) -> str:
    if interactive_password_reader is not None:
        return interactive_password_reader(prompt_text)
    if use_prompt_toolkit and pt_prompt is not None and _is_interactive(stdin, stdout):
        return pt_prompt(prompt_text, is_password=True)
    return getpass.getpass(prompt_text)


def _invoke_remote_validator(
    remote_validator: RemoteValidator,
    config: RemoteHostConfig,
    ssh_password: str | None,
) -> tuple[bool, str]:
    try:
        signature = inspect.signature(remote_validator)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        params = list(signature.parameters.values())
        has_keyword = "ssh_password" in signature.parameters
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
        if has_keyword or accepts_kwargs or len(params) >= 2:
            return remote_validator(config, ssh_password=ssh_password)
    try:
        return remote_validator(config, ssh_password=ssh_password)
    except TypeError:
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
