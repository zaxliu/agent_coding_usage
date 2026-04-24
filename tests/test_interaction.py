from io import StringIO
from pathlib import Path
from typing import Optional

import llm_usage.interaction as interaction
from llm_usage.interaction import confirm_save_temporary_remote, run_config_editor, select_remotes
from llm_usage.interaction_flow import RemotePromptRunner
from llm_usage.remotes import RemoteHostConfig


class _TTYStringIO(StringIO):
    def isatty(self):  # noqa: ANN201
        return True


_VALID_DEFAULT_FEISHU_ENV = "FEISHU_APP_TOKEN=app-default\nFEISHU_BOT_TOKEN=bot-default\nHASH_SALT=salt\n"


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
        cline_vscode_session_paths=["~/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/tasks/*/api_conversation_history.json"],
    )


def test_edit_remote_paths_menu_includes_cline_vscode():
    remote = interaction.RemoteDraft(
        alias="SERVER_A",
        ssh_host="host-a",
        ssh_user="alice",
        ssh_port=22,
        source_label="alice@host-a",
        claude_log_paths=[],
        codex_log_paths=[],
        copilot_cli_log_paths=[],
        copilot_vscode_session_paths=[],
        cline_vscode_session_paths=[],
    )
    stdout = _TTYStringIO()

    changed = interaction._edit_remote_paths(remote, stdin=_TTYStringIO("b\n"), stdout=stdout)

    assert changed is False
    assert "Cline VSCode" in stdout.getvalue()


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


def test_select_remotes_cli_dedupes_repeated_alias_tokens_preserving_order():
    configs = [_config("SERVER_A"), _config("SERVER_B")]
    result = select_remotes(
        configs,
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("1,server_a,2,SERVER_B,1\n"),
        stdout=_TTYStringIO(),
    )

    assert result.selected_aliases == ["SERVER_A", "SERVER_B"]


def test_select_remotes_cli_supports_temporary_remote():
    configs = [_config("SERVER_A")]
    result = select_remotes(
        configs,
        ["SERVER_A"],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == ["SERVER_A"]
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"
    assert result.temporary_remotes[0].alias == "BOB_HOST_B"
    assert result.temporary_remotes[0].source_label == "bob@host-b"


def test_select_remotes_cli_reprompts_for_invalid_temporary_remote_port():
    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n0\n-1\n2200\n\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )

    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_port == 2200


def test_select_remotes_cli_supports_temporary_remote_without_configured_hosts():
    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == []
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"


def test_select_remotes_cli_drives_remote_prompt_runner_in_terminal_order(monkeypatch):
    prompts: list[str] = []
    applied_values: list[str] = []

    class _FakeRunner:
        def __init__(self, existing_aliases):  # noqa: ANN001
            assert existing_aliases == []
            self._requests = [
                type("Req", (), {"kind": "ssh_host", "message": "SSH 主机："})(),
                type("Req", (), {"kind": "ssh_user", "message": "SSH 用户："})(),
                type("Req", (), {"kind": "ssh_port", "message": "SSH 端口 [22]："})(),
            ]
            self.state = type(
                "State",
                (),
                {"alias": "", "ssh_host": "", "ssh_user": "", "ssh_port": 22, "ssh_jump_host": "", "ssh_jump_port": 2222},
            )()
            self._index = 0

        def next_request(self):
            if self._index >= len(self._requests):
                return None
            request = self._requests[self._index]
            self._index += 1
            return request

        def apply_input(self, value):
            applied_values.append(value)
            if len(applied_values) == 1:
                self.state.alias = "BOB_HOST_B"
                self.state.ssh_host = "host-b"
            if len(applied_values) == 2:
                self.state.ssh_user = "bob"
            if len(applied_values) == 3:
                self.state.ssh_port = 22
            return True

    def _fake_read_line(prompt_text, **_kwargs):  # noqa: ANN001
        prompts.append(prompt_text)
        answers = {
            "回车表示仅统计本机，输入 + 新增一个临时远端：": "+",
            "SSH 主机：": "host-b",
            "SSH 用户：": "bob",
            "SSH 端口 [22]：": "22",
        }
        return answers[prompt_text]

    monkeypatch.setattr("llm_usage.interaction.RemotePromptRunner", _FakeRunner)
    monkeypatch.setattr("llm_usage.interaction._read_line", _fake_read_line)

    result = select_remotes([], [], ui_mode="cli", stdin=_TTYStringIO(), stdout=_TTYStringIO(), remote_validator=lambda config: (True, "ok"))

    assert prompts == [
        "回车表示仅统计本机，输入 + 新增一个临时远端：",
        "SSH 主机：",
        "SSH 用户：",
        "SSH 端口 [22]：",
    ]
    assert applied_values == ["host-b", "bob", "22"]
    assert result.mode_used == "cli"
    assert len(result.temporary_remotes) == 1
    temp = result.temporary_remotes[0]
    assert temp.alias == "BOB_HOST_B"
    assert temp.ssh_host == "host-b"
    assert temp.ssh_user == "bob"
    assert temp.ssh_port == 22
    assert result.runtime_passwords == {}


def test_select_remotes_cli_preserves_runner_alias_when_temporary_alias_collides():
    configs = [_config("BOB_HOST_B")]
    result = select_remotes(
        configs,
        ["BOB_HOST_B"],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )

    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].alias == "BOB_HOST_B_2"


def test_select_remotes_cli_supports_validator_with_positional_password_parameter():
    validator_calls = []

    def _validator(config, password):  # noqa: ANN001
        validator_calls.append((config.alias, password))
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        interactive_password_reader=lambda prompt_text: "hunter2",
    )

    assert len(result.temporary_remotes) == 1
    assert validator_calls == [(result.temporary_remotes[0].alias, None)]


def test_select_remotes_cli_reuses_cached_password_after_key_auth_failure():
    password_store = {"value": None}
    password_prompts: list[str] = []
    validator_calls: list[tuple[str, Optional[str]]] = []

    def _password_getter():
        return password_store["value"]

    def _password_setter(password):  # noqa: ANN001
        password_store["value"] = password

    def _password_reader(prompt_text):  # noqa: ANN001
        password_prompts.append(prompt_text)
        return "hunter2"

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append((config.alias, ssh_password))
        if ssh_password is None:
            return False, "Permission denied (publickey)."
        return True, "ok"

    first = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=_password_reader,
    )

    assert len(first.temporary_remotes) == 1
    assert first.runtime_passwords == {first.temporary_remotes[0].alias: "hunter2"}
    assert password_store["value"] == "hunter2"
    assert password_prompts == ["SSH 密码："]
    assert validator_calls == [(first.temporary_remotes[0].alias, None), (first.temporary_remotes[0].alias, "hunter2")]

    password_prompts.clear()
    validator_calls.clear()

    second = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-c\ncarol\n2222\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=lambda prompt_text: (_ for _ in ()).throw(AssertionError(prompt_text)),
    )

    assert len(second.temporary_remotes) == 1
    assert password_prompts == []
    assert validator_calls == [(second.temporary_remotes[0].alias, None), (second.temporary_remotes[0].alias, "hunter2")]


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
        validator_calls.append(ssh_password)
        if ssh_password is None:
            return False, "Permission denied (publickey)."
        return True, "ok"

    result = interaction.select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )

    assert len(result.temporary_remotes) == 1
    assert getpass_calls == ["SSH 密码："]
    assert validator_calls == [None, "hidden-secret"]


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
            return False, "Connection timed out"
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nr\nhost-c\nroot\n22\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-c"


def test_select_remotes_cli_reprompts_for_new_password_after_failed_password_retry():
    password_store = {"value": None}
    password_prompts: list[str] = []
    validator_calls: list[tuple[str, Optional[str]]] = []

    def _password_getter():
        return password_store["value"]

    def _password_setter(password):  # noqa: ANN001
        password_store["value"] = password

    def _password_reader(prompt_text):  # noqa: ANN001
        password_prompts.append(prompt_text)
        return "hunter2" if len(password_prompts) == 1 else "new-secret"

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append((config.ssh_host, ssh_password))
        if ssh_password is None:
            return False, "Permission denied"
        if config.ssh_host == "host-b":
            return False, "Permission denied"
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nr\nhost-c\nroot\n22\n\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=_password_reader,
    )

    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-c"
    assert password_prompts == ["SSH 密码：", "SSH 密码："]
    assert validator_calls == [("host-b", None), ("host-b", "hunter2"), ("host-c", None), ("host-c", "new-secret")]
    assert password_store["value"] == "new-secret"
    assert result.runtime_passwords == {result.temporary_remotes[0].alias: "new-secret"}


def test_select_remotes_cli_retries_on_commas_only_selection():
    configs = [_config("SERVER_A"), _config("SERVER_B")]
    result = select_remotes(
        configs,
        ["SERVER_A"],
        ui_mode="cli",
        stdin=_TTYStringIO(" , \n2\n"),
        stdout=_TTYStringIO(),
    )

    assert result.selected_aliases == ["SERVER_B"]


def test_select_remotes_cli_cancels_when_ssh_probe_fails():
    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (False, "Permission denied"),
    )
    assert result.temporary_remotes == []


def test_select_remotes_cli_propagates_validator_type_error():
    def _validator(config):  # noqa: ANN001, ANN201
        raise TypeError("validator broke internally")

    try:
        select_remotes(
            [],
            [],
            ui_mode="cli",
            stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\n"),
            stdout=_TTYStringIO(),
            remote_validator=_validator,
        )
    except TypeError as exc:
        assert "validator broke internally" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected TypeError to propagate")


def test_select_remotes_cli_clears_cached_password_after_failed_probe():
    password_store = {"value": None}

    def _password_setter(password):  # noqa: ANN001
        password_store["value"] = password

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config, ssh_password=None: (False, "Permission denied"),
        password_setter=_password_setter,
        interactive_password_reader=lambda prompt_text: "hunter2",
    )

    assert result.temporary_remotes == []
    assert result.runtime_passwords == {}
    assert password_store["value"] == ""


def test_select_remotes_cli_cleans_up_password_after_blank_password_cancel():
    password_store = {"value": None}

    def _password_setter(password):  # noqa: ANN001
        password_store["value"] = password

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config, ssh_password=None: (False, "Permission denied (publickey).")
        if ssh_password is None
        else (True, "ok"),
        password_setter=_password_setter,
        interactive_password_reader=lambda prompt_text: "",
    )

    assert result.temporary_remotes == []
    assert result.runtime_passwords == {}
    assert password_store["value"] is None


def test_select_remotes_cli_prompts_for_password_when_key_probe_auth_fails():
    validator_calls: list[Optional[str]] = []

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append(ssh_password)
        if ssh_password is None:
            return False, "Permission denied (publickey)."
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\n\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        interactive_password_reader=lambda prompt_text: "hunter2",
    )

    assert len(result.temporary_remotes) == 1
    remote = result.temporary_remotes[0]
    assert result.runtime_passwords == {remote.alias: "hunter2"}
    assert validator_calls == [None, "hunter2"]


def test_run_config_editor_discards_unsaved_changes(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\nq\nd\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\n"


def test_run_config_editor_save_does_not_validate_existing_remotes(tmp_path: Path):
    env_path = tmp_path / ".env"
    original = (
        _VALID_DEFAULT_FEISHU_ENV
        + "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n"
    )
    env_path.write_text(original, encoding="utf-8")

    def _validator(config, ssh_password=None):  # noqa: ANN001
        raise AssertionError(f"existing remote should not be validated on save: {config.alias}")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("s\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=SERVER_A" in text
    assert "REMOTE_SERVER_A_SSH_HOST=host-a" in text


def test_run_config_editor_add_validates_only_new_remote_and_saves_immediately(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV
        + "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )
    validator_calls: list[tuple[str, Optional[str]]] = []

    def _validator(config, ssh_password=None):  # noqa: ANN001
        validator_calls.append((config.alias, ssh_password))
        if config.alias != "PROD_A":
            return False, f"unexpected validation for {config.alias}"
        return True, "ok"

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\na\nprod-a\nhost-b\nbob\n2200\n\n\nb\nq\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )

    assert exit_code == 0
    assert validator_calls == [("PROD_A", None)]
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=SERVER_A,PROD_A" in text
    assert "REMOTE_PROD_A_SSH_HOST=host-b" in text
    assert "REMOTE_PROD_A_SSH_PORT=2200" in text
    assert "USE_SSHPASS" not in text


def test_run_config_editor_saves_draft_changes(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_FEISHU_ENV + "ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert "ORG_USERNAME=bob" in env_path.read_text(encoding="utf-8")


def test_run_config_editor_save_rejects_incomplete_default_feishu_auth(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    user_input = "\n".join(
        [
            "2",
            "1",
            "1",
            "app-default",
            "b",
            "s",
            "d",
            "",
        ]
    )

    stdout = _TTYStringIO()
    exit_code = run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=stdout)

    assert exit_code == 0
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in stdout.getvalue()
    assert "FEISHU_APP_TOKEN=app-default" not in env_path.read_text(encoding="utf-8")


def test_run_config_editor_edits_grouped_non_remote_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=alice\n"
        "HASH_SALT=salt\n"
        "FEISHU_APP_TOKEN=old-token\n"
        "FEISHU_BOT_TOKEN=bot-default\n",
        encoding="utf-8",
    )

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n1\n1\nnew-token\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_APP_TOKEN=new-token" in text
    assert "FEISHU_BOT_TOKEN=bot-default" in text


def test_run_config_editor_adds_remote_and_path_entries(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_FEISHU_ENV + "ORG_USERNAME=alice\n", encoding="utf-8")

    user_input = "\n".join(
        [
            "4",
            "a",
            "prod-a",
            "host-a",
            "alice",
            "22",
            "",
            "",
            "n",
            "p",
            "1",
            "a",
            "/logs/claude.jsonl",
            "b",
            "b",
            "b",
            "b",
            "s",
        ]
    ) + "\n"

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO(user_input),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=PROD_A" in text
    assert "REMOTE_PROD_A_SSH_HOST=host-a" in text
    assert "REMOTE_PROD_A_CLAUDE_LOG_PATHS=/logs/claude.jsonl" in text


def test_run_config_editor_reprompts_for_invalid_remote_ports(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV +
        "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )

    user_input = "\n".join(
        [
            "4",
            "a",
            "prod-a",
            "host-b",
            "bob",
            "0",
            "-1",
            "2200",
            "",
            "",
            "n",
            "b",
            "e",
            "1",
            "4",
            "0",
            "-3",
            "2222",
            "b",
            "b",
            "s",
        ]
    ) + "\n"

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO(user_input),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_SERVER_A_SSH_PORT=2222" in text
    assert "REMOTE_PROD_A_SSH_PORT=2200" in text


def test_run_config_editor_edits_existing_remote_without_dirtying_on_noop(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV
        + "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n",
        encoding="utf-8",
    )

    stdout = _TTYStringIO()
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\ne\n1\nb\nq\n"),
        stdout=stdout,
    )

    assert exit_code == 0
    assert "Config *" not in stdout.getvalue()
    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice\n" in text
    assert "REMOTE_HOSTS=SERVER_A\n" in text


def test_run_config_editor_enforces_unique_remote_alias_on_edit(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV +
        "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A,SERVER_B\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n"
        "REMOTE_SERVER_B_SSH_HOST=host-b\n"
        "REMOTE_SERVER_B_SSH_USER=bob\n"
        "REMOTE_SERVER_B_SSH_PORT=22\n",
        encoding="utf-8",
    )

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\ne\n2\n1\nserver_a\nb\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=SERVER_A,SERVER_A_2" in text
    assert "REMOTE_SERVER_A_2_SSH_HOST=host-b" in text


def test_run_config_editor_deletes_nested_path_entry(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV +
        "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n"
        "REMOTE_SERVER_A_SSH_PORT=22\n"
        "REMOTE_SERVER_A_CLAUDE_LOG_PATHS=/a,/b\n",
        encoding="utf-8",
    )

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\ne\n1\np\n1\nd\n2\nb\nb\nb\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_SERVER_A_CLAUDE_LOG_PATHS=/a" in text
    assert "/b" not in text


def test_run_config_editor_deletes_remote_without_touching_other_keys(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_FEISHU_ENV
        + "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n",
        encoding="utf-8",
    )

    def _validator(config, ssh_password=None):  # noqa: ANN001
        raise AssertionError(f"deleted remote should not be validated on save: {config.alias}")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\nd\n1\nb\ns\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice" in text
    assert "REMOTE_HOSTS" not in text
    assert "REMOTE_SERVER_A_SSH_HOST" not in text


def test_run_config_editor_rejects_blank_remote_fields(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_FEISHU_ENV + "ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\na\nprod-a\n\nalice\n22\n\nn\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice\n" in text
    assert "REMOTE_HOSTS" not in text


def _request_kinds(runner: RemotePromptRunner, values: list[str]) -> list[str]:
    kinds: list[str] = []
    for value in values:
        request = runner.next_request()
        if request is None:
            break
        kinds.append(request.kind)
        runner.apply_input(value)
    return kinds


def test_cli_and_web_share_same_remote_input_sequence():
    runner = RemotePromptRunner(existing_aliases=[])
    kinds = _request_kinds(runner, ["host-a", "alice", "22", ""])

    assert kinds == ["ssh_host", "ssh_user", "ssh_port", "ssh_jump_host"]
    assert runner.next_request() is None
    assert runner.state.ssh_host == "host-a"
    assert runner.state.ssh_user == "alice"
    assert runner.state.ssh_port == 22
    assert runner.state.ssh_jump_host == ""
