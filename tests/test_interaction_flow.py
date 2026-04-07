from llm_usage.interaction_flow import (
    InputRequest,
    RemoteFlowState,
    RemotePromptRunner,
    request_ssh_host_step,
    request_ssh_password_step,
)


def test_input_request_shapes_for_remote_steps():
    host_req = request_ssh_host_step()
    password_req = request_ssh_password_step("SERVER_A")

    assert host_req == InputRequest(
        kind="ssh_host",
        message="SSH 主机：",
        field="value",
        remote_alias="",
        secret=False,
        choices=None,
    )
    assert password_req == InputRequest(
        kind="ssh_password",
        message="请输入 SERVER_A 的 SSH 密码：",
        field="value",
        remote_alias="SERVER_A",
        secret=True,
        choices=None,
    )


def test_remote_flow_state_defaults_are_ssh_specific():
    state = RemoteFlowState()

    assert state.alias == ""
    assert state.ssh_host == ""
    assert state.ssh_user == ""
    assert state.ssh_port == 22
    assert state.use_sshpass is False


def test_remote_prompt_runner_advances_through_temp_remote_steps():
    runner = RemotePromptRunner(existing_aliases=["SERVER_A"])

    assert runner.next_request().kind == "ssh_host"

    assert runner.apply_input("host-b") is True
    assert runner.state.ssh_host == "host-b"
    assert runner.next_request().kind == "ssh_user"

    assert runner.apply_input("alice") is True
    assert runner.state.ssh_user == "alice"
    assert runner.next_request().kind == "ssh_port"

    assert runner.apply_input("2200") is True
    assert runner.state.ssh_port == 2200
    assert runner.next_request().kind == "use_sshpass"

    assert runner.apply_input("yes") is True
    assert runner.state.use_sshpass is True
    assert runner.next_request() is None


def test_remote_prompt_runner_rejects_empty_host_and_user():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("") is False
    assert runner.next_request().kind == "ssh_host"
    assert runner.state.alias == ""

    assert runner.apply_input("host-b") is True
    assert runner.next_request().kind == "ssh_user"

    assert runner.apply_input("") is False
    assert runner.next_request().kind == "ssh_user"
    assert runner.state.alias == ""


def test_remote_prompt_runner_rejects_invalid_port_values():
    runner = RemotePromptRunner(existing_aliases=[])
    assert runner.apply_input("host-b") is True
    assert runner.apply_input("alice") is True

    assert runner.apply_input("abc") is False
    assert runner.next_request().kind == "ssh_port"
    assert runner.state.ssh_port == 22

    assert runner.apply_input("0") is False
    assert runner.next_request().kind == "ssh_port"
    assert runner.state.ssh_port == 22


def test_remote_prompt_runner_uses_default_port_for_blank_input():
    runner = RemotePromptRunner(existing_aliases=[])
    assert runner.apply_input("host-b") is True
    assert runner.apply_input("alice") is True

    assert runner.apply_input("") is True
    assert runner.state.ssh_port == 22
    assert runner.next_request().kind == "use_sshpass"


def test_remote_prompt_runner_rejects_invalid_use_sshpass_input():
    runner = RemotePromptRunner(existing_aliases=[])
    assert runner.apply_input("host-b") is True
    assert runner.apply_input("alice") is True
    assert runner.apply_input("22") is True

    assert runner.apply_input("maybe") is False
    assert runner.next_request().kind == "use_sshpass"
    assert runner.state.use_sshpass is False


def test_remote_prompt_runner_populates_unique_alias_from_remote_rules():
    runner = RemotePromptRunner(existing_aliases=["ALICE_SERVER_B"])

    assert runner.apply_input("server-b") is True
    assert runner.state.alias == ""

    assert runner.apply_input("alice") is True
    assert runner.state.alias == "ALICE_SERVER_B_2"
    assert runner.next_request().kind == "ssh_port"
