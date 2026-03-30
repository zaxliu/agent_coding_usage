from io import StringIO
from pathlib import Path
from typing import Optional

from llm_usage.interaction import confirm_save_temporary_remote, run_config_editor, select_remotes
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n0\n-1\n2200\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config: (True, "ok"),
    )
    assert result.selected_aliases == []
    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-b"


def test_select_remotes_cli_supports_validator_with_positional_password_parameter():
    validator_calls = []

    def _validator(config, password):  # noqa: ANN001
        validator_calls.append((config.alias, password))
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        interactive_password_reader=lambda prompt_text: "hunter2",
    )

    assert len(result.temporary_remotes) == 1
    assert validator_calls == [(result.temporary_remotes[0].alias, "hunter2")]


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


def test_select_remotes_cli_reprompts_for_new_password_after_failed_sshpass_probe():
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
        if len(validator_calls) == 1:
            return False, "Permission denied"
        return True, "ok"

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\nr\nhost-c\nroot\n22\ny\n"),
        stdout=_TTYStringIO(),
        remote_validator=_validator,
        password_getter=_password_getter,
        password_setter=_password_setter,
        interactive_password_reader=_password_reader,
    )

    assert len(result.temporary_remotes) == 1
    assert result.temporary_remotes[0].ssh_host == "host-c"
    assert password_prompts == ["SSH 密码：", "SSH 密码："]
    assert validator_calls == [("host-b", "hunter2"), ("host-c", "new-secret")]
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\nn\n"),
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
            stdin=_TTYStringIO("+\nhost-b\nbob\n2200\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\nn\n"),
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
        stdin=_TTYStringIO("+\nhost-b\nbob\n2200\ny\nn\n"),
        stdout=_TTYStringIO(),
        remote_validator=lambda config, ssh_password=None: (True, "ok"),
        password_setter=_password_setter,
        interactive_password_reader=lambda prompt_text: "",
    )

    assert result.temporary_remotes == []
    assert result.runtime_passwords == {}
    assert password_store["value"] is None


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


def test_run_config_editor_saves_draft_changes(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert "ORG_USERNAME=bob" in env_path.read_text(encoding="utf-8")


def test_run_config_editor_edits_grouped_non_remote_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("FEISHU_APP_TOKEN=old-token\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n1\nnew-token\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert env_path.read_text(encoding="utf-8") == "FEISHU_APP_TOKEN=new-token\n"


def test_run_config_editor_adds_remote_and_path_entries(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    user_input = "\n".join(
        [
            "4",
            "a",
            "prod-a",
            "host-a",
            "alice",
            "22",
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
        "ORG_USERNAME=alice\n"
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
    assert env_path.read_text(encoding="utf-8").startswith("ORG_USERNAME=alice\nREMOTE_HOSTS=SERVER_A\n")


def test_run_config_editor_enforces_unique_remote_alias_on_edit(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
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
        "ORG_USERNAME=alice\n"
        "REMOTE_HOSTS=SERVER_A\n"
        "REMOTE_SERVER_A_SSH_HOST=host-a\n"
        "REMOTE_SERVER_A_SSH_USER=alice\n",
        encoding="utf-8",
    )

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\nd\n1\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice" in text
    assert "REMOTE_HOSTS" not in text
    assert "REMOTE_SERVER_A_SSH_HOST" not in text


def test_run_config_editor_rejects_blank_remote_fields(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("4\na\nprod-a\n\nalice\n22\n\nn\nb\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\n"
