from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, TextIO

from llm_usage.remotes import RemoteHostConfig, build_temporary_remote, probe_remote_ssh

try:
    from prompt_toolkit import prompt as pt_prompt
except ImportError:  # pragma: no cover
    pt_prompt = None


@dataclass(frozen=True)
class RemoteSelectionResult:
    selected_aliases: list[str]
    temporary_remotes: list[RemoteHostConfig]
    mode_used: str


def can_use_tui() -> bool:
    return pt_prompt is not None


def select_remotes(
    configs: list[RemoteHostConfig],
    default_aliases: list[str],
    ui_mode: str = "auto",
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    remote_validator: Callable[[RemoteHostConfig], tuple[bool, str]] | None = None,
) -> RemoteSelectionResult:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    remote_validator = remote_validator or probe_remote_ssh
    if ui_mode == "none" or not _is_interactive(stdin, stdout):
        return RemoteSelectionResult(selected_aliases=list(default_aliases), temporary_remotes=[], mode_used="none")

    use_prompt_toolkit = ui_mode == "tui" or (ui_mode == "auto" and can_use_tui())
    if not configs:
        return _select_without_configs(
            stdin,
            stdout,
            mode_used="tui" if use_prompt_toolkit else "cli",
            use_prompt_toolkit=use_prompt_toolkit,
            remote_validator=remote_validator,
        )
    return _select_with_list(
        configs,
        default_aliases,
        stdin=stdin,
        stdout=stdout,
        mode_used="tui" if use_prompt_toolkit else "cli",
        use_prompt_toolkit=use_prompt_toolkit,
        remote_validator=remote_validator,
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
    remote_validator: Callable[[RemoteHostConfig], tuple[bool, str]],
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
            )
        raw = answer.strip()
        if not raw:
            return RemoteSelectionResult(
                selected_aliases=list(default_aliases),
                temporary_remotes=[],
                mode_used=mode_used,
            )
        lower = raw.lower()
        if lower == "all":
            return RemoteSelectionResult(
                selected_aliases=list(alias_map),
                temporary_remotes=[],
                mode_used=mode_used,
            )
        if lower == "none":
            return RemoteSelectionResult(selected_aliases=[], temporary_remotes=[], mode_used=mode_used)
        if raw == "+":
            temp = _prompt_temporary_remote(stdin, stdout, use_prompt_toolkit, remote_validator)
            if temp is not None:
                temporary_remotes.append(temp)
                return RemoteSelectionResult(
                    selected_aliases=list(default_aliases),
                    temporary_remotes=temporary_remotes,
                    mode_used=mode_used,
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
            )
        stdout.write("输入无效，请重试。\n")


def _select_without_configs(
    stdin: TextIO,
    stdout: TextIO,
    mode_used: str,
    use_prompt_toolkit: bool,
    remote_validator: Callable[[RemoteHostConfig], tuple[bool, str]],
) -> RemoteSelectionResult:
    stdout.write("当前 .env 中还没有配置远端。\n")
    answer = _read_line(
        "回车表示仅统计本机，输入 + 新增一个临时远端：",
        stdin=stdin,
        stdout=stdout,
        use_prompt_toolkit=use_prompt_toolkit,
    )
    if answer.strip() != "+":
        return RemoteSelectionResult(selected_aliases=[], temporary_remotes=[], mode_used=mode_used)
    temp = _prompt_temporary_remote(stdin, stdout, use_prompt_toolkit, remote_validator)
    return RemoteSelectionResult(
        selected_aliases=[],
        temporary_remotes=[temp] if temp else [],
        mode_used=mode_used,
    )


def _prompt_temporary_remote(
    stdin: TextIO,
    stdout: TextIO,
    use_prompt_toolkit: bool,
    remote_validator: Callable[[RemoteHostConfig], tuple[bool, str]],
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
        config = build_temporary_remote(host, user, port)
        stdout.write("正在检查 SSH 连通性...\n")
        ok, message = remote_validator(config)
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
