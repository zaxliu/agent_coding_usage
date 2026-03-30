from io import StringIO
from typing import Optional

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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == []
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"


def test_select_remotes_cli_supports_sshpass_password_capture():
    password_store = {"value": None}
    password_prompts: list[str] = []
    validator_calls: list[tuple[str, Optional[str], bool]] = []

    def _password_getter():
        return password_store["value"]

    def _password_setter(password):  # noqa: ANN001
        password_store["value"] = password

    def _password_reader(prompt_text):  # noqa: ANN001
        password_prompts.append(prompt_text)
        return "hunter2"

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append((config.alias, ssh_password, config.use_sshpass))
        return True, "ok"

    first = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=_password_reader,
    )

    assert len(first.temporary_remotes) == 1
    assert first.temporary_remotes[0].use_sshpass is True
    assert first.runtime_passwords == {first.temporary_remotes[0].alias: "hunter2"}
    assert password_store["value"] == "hunter2"
    assert password_prompts == ["SSH 密码："]
    assert validator_calls == [(first.temporary_remotes[0].alias, "hunter2", True)]

    password_prompts.clear()
    validator_calls.clear()

    second = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-c\ncarol\n2222\ny\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=lambda prompt_text: (_ for _ in ()).throw(AssertionError(prompt_text)),
    )

    assert len(second.temporary_remotes) == 1
    assert second.temporary_remotes[0].use_sshpass is True
    assert password_prompts == []
    assert validator_calls == [(second.temporary_remotes[0].alias, "hunter2", True)]


def test_select_remotes_cli_uses_getpass_when_prompt_toolkit_is_unavailable(monkeypatch):
    import llm_usage.interaction as interaction

    monkeypatch.setattr(interaction, "pt_prompt", None)

    getpass_calls = []

    def _fake_getpass(prompt_text):  # noqa: ANN001
        getpass_calls.append(prompt_text)
        return "hidden-secret"

    monkeypatch.setattr(interaction.getpass, "getpass", _fake_getpass)

    validator_calls = []

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append((config.use_sshpass, ssh_password))
        return True, "ok"

    result = interaction.select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )

    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].use_sshpass is True
    assert getpass_calls == ["SSH 密码："]
    assert validator_calls == [(True, "hidden-secret")]


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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\nr\nhost-c\nroot\n22\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (False, "Permission denied"),
    )
    assert result.temporary_remotes == []
