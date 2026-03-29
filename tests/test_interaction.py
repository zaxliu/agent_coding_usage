from io import StringIO

from llm_usage.interaction import confirm_save_temporary_remote, select_remotes
from llm_usage.remotes import RemoteHostConfig


class _TTYStringIO(StringIO):
    def isatty(self):  # noqa: ANN201
        return True


def _config(alias: str) -> RemoteHostConfig:
    return RemoteHostConfig(
        alias=alias,
        ssh_host=f"{alias.lower()}.example",
        ssh_user="alice",
        ssh_port=22,
        source_label=alias.lower(),
        claude_log_paths=["~/.claude/**/*.jsonl"],
        codex_log_paths=["~/.codex/**/*.jsonl"],
        copilot_cli_log_paths=["~/.copilot/session-state/**/*.jsonl"],
        copilot_vscode_session_paths=["~/.vscode-server/data/User/globalStorage/emptyWindowChatSessions/*.jsonl"],
    )


def test_select_remotes_cli_accepts_default():
    configs = [_config("SERVER_A"), _config("SERVER_B")]
    result = select_remotes(
        configs,
        ["SERVER_A"],
        ui_mode="cli",
        stdin=_TTYStringIO("\n"),
        stdout=_TTYStringIO(),
    )
    assert result.selected_aliases == ["SERVER_A"]
    assert result.temporary_remotes == []


def test_select_remotes_cli_supports_temporary_remote():
    configs = [_config("SERVER_A")]
    result = select_remotes(
        configs,
        ["SERVER_A"],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == ["SERVER_A"]
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"
    assert result.temporary_remotes[0].alias == "BOB_HOST_B"
    assert result.temporary_remotes[0].source_label == "bob@host-b"


def test_select_remotes_cli_supports_temporary_remote_without_configured_hosts():
    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == []
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"


def test_confirm_save_temporary_remote_cli_yes():
    config = _config("SERVER_A")
    assert confirm_save_temporary_remote(
        config,
        ui_mode="cli",
        stdin=_TTYStringIO("y\n"),
        stdout=_TTYStringIO(),
    )


def test_select_remotes_cli_retries_when_ssh_probe_fails():
    calls = {"count": 0}

    def _validator(config):  # noqa: ANN001, ANN201
        calls["count"] += 1
        if calls["count"] == 1:
            return False, "Permission denied"
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nr\nhost-c\nroot\n22\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-c"


def test_select_remotes_cli_cancels_when_ssh_probe_fails():
    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (False, "Permission denied"),
    )
    assert result.temporary_remotes == []
