from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from llm_usage.remotes import default_source_label, normalize_alias, unique_alias


@dataclass(frozen=True)
class InputRequest:
    kind: str
    message: str
    field: str = "value"
    remote_alias: str = ""
    secret: bool = False
    choices: Optional[list[str]] = None


@dataclass
class RemoteFlowState:
    alias: str = ""
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_jump_host: str = ""
    ssh_jump_port: int = 2222


class RemotePromptRunner:
    def __init__(self, existing_aliases: list[str]) -> None:
        self.existing_aliases = list(existing_aliases)
        self.state = RemoteFlowState()
        self._stage = "ssh_host"

    def next_request(self) -> Optional[InputRequest]:
        if self._stage == "ssh_host":
            return InputRequest(kind="ssh_host", message="SSH 主机：")
        if self._stage == "ssh_user":
            return InputRequest(kind="ssh_user", message="SSH 用户：")
        if self._stage == "ssh_port":
            return InputRequest(kind="ssh_port", message="SSH 端口 [22]：")
        if self._stage == "ssh_jump_host":
            return InputRequest(kind="ssh_jump_host", message="跳板机地址（留空跳过）：")
        if self._stage == "ssh_jump_port":
            return InputRequest(kind="ssh_jump_port", message="跳板机端口 [2222]：")
        return None

    def apply_input(self, value: str) -> bool:
        value = value.strip()
        if self._stage == "ssh_host":
            if not value:
                return False
            self.state.ssh_host = value
            self._stage = "ssh_user"
            return True
        if self._stage == "ssh_user":
            if not value:
                return False
            self.state.ssh_user = value
            self._populate_alias()
            self._stage = "ssh_port"
            return True
        if self._stage == "ssh_port":
            if not value:
                self.state.ssh_port = 22
                self._stage = "ssh_jump_host"
                return True
            try:
                port = int(value)
            except ValueError:
                return False
            if port <= 0:
                return False
            self.state.ssh_port = port
            self._stage = "ssh_jump_host"
            return True
        if self._stage == "ssh_jump_host":
            if not value:
                self.state.ssh_jump_host = ""
                self._stage = "done"
                return True
            if "@" in value or any(c in value for c in " \t\n\r"):
                return False
            self.state.ssh_jump_host = value
            self._stage = "ssh_jump_port"
            return True
        if self._stage == "ssh_jump_port":
            if not value:
                self.state.ssh_jump_port = 2222
                self._stage = "done"
                return True
            try:
                port = int(value)
            except ValueError:
                return False
            if port <= 0:
                return False
            self.state.ssh_jump_port = port
            self._stage = "done"
            return True
        return False

    def _populate_alias(self) -> None:
        source_label = default_source_label(self.state.ssh_user, self.state.ssh_host)
        alias_seed = normalize_alias(source_label)
        self.state.alias = unique_alias(alias_seed, self.existing_aliases)

def request_ssh_host_step() -> InputRequest:
    return InputRequest(kind="ssh_host", message="SSH 主机：")


def request_ssh_password_step(alias: str) -> InputRequest:
    return InputRequest(
        kind="ssh_password",
        message=f"请输入 {alias} 的 SSH 密码：",
        remote_alias=alias,
        secret=True,
    )
