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
    assert state.ssh_jump_host == ""
    assert state.ssh_jump_port == 2222


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
    assert runner.next_request().kind == "use_jump"

    # decline jump host
    assert runner.apply_input("n") is True
    assert runner.state.ssh_jump_host == ""
    assert runner.next_request() is None


def test_remote_prompt_runner_skip_jump_with_empty_input():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-b") is True
    assert runner.apply_input("alice") is True
    assert runner.apply_input("") is True
    assert runner.next_request().kind == "use_jump"

    # empty input defaults to no
    assert runner.apply_input("") is True
    assert runner.state.ssh_jump_host == ""
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
    assert runner.next_request().kind == "use_jump"

    # decline jump
    assert runner.apply_input("n") is True
    assert runner.next_request() is None


def test_remote_prompt_runner_populates_unique_alias_from_remote_rules():
    runner = RemotePromptRunner(existing_aliases=["ALICE_SERVER_B"])

    assert runner.apply_input("server-b") is True
    assert runner.state.alias == ""

    assert runner.apply_input("alice") is True
    assert runner.state.alias == "ALICE_SERVER_B_2"
    assert runner.next_request().kind == "ssh_port"


def test_remote_prompt_runner_with_jump_host():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-c") is True
    assert runner.apply_input("bob") is True
    assert runner.apply_input("22") is True
    assert runner.next_request().kind == "use_jump"

    assert runner.apply_input("y") is True
    assert runner.next_request().kind == "ssh_jump_host"

    assert runner.apply_input("bastion.example.com") is True
    assert runner.state.ssh_jump_host == "bastion.example.com"
    assert runner.next_request().kind == "ssh_jump_port"

    assert runner.apply_input("") is True
    assert runner.state.ssh_jump_port == 2222
    assert runner.next_request() is None


def test_remote_prompt_runner_jump_host_custom_port():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-d") is True
    assert runner.apply_input("carol") is True
    assert runner.apply_input("") is True  # default port

    assert runner.apply_input("y") is True  # use jump
    assert runner.next_request().kind == "ssh_jump_host"

    assert runner.apply_input("jump.server") is True
    assert runner.next_request().kind == "ssh_jump_port"

    assert runner.apply_input("3333") is True
    assert runner.state.ssh_jump_port == 3333
    assert runner.next_request() is None


def test_remote_prompt_runner_rejects_invalid_jump_port():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-e") is True
    assert runner.apply_input("dave") is True
    assert runner.apply_input("22") is True
    assert runner.apply_input("y") is True  # use jump
    assert runner.apply_input("jump.host") is True

    assert runner.apply_input("abc") is False
    assert runner.next_request().kind == "ssh_jump_port"

    assert runner.apply_input("0") is False
    assert runner.next_request().kind == "ssh_jump_port"


def test_remote_prompt_runner_rejects_jump_host_with_at_sign():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-f") is True
    assert runner.apply_input("eve") is True
    assert runner.apply_input("22") is True
    assert runner.apply_input("y") is True  # use jump

    assert runner.apply_input("bad@host") is False
    assert runner.next_request().kind == "ssh_jump_host"


def test_remote_prompt_runner_rejects_jump_host_with_whitespace():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-g") is True
    assert runner.apply_input("frank") is True
    assert runner.apply_input("22") is True
    assert runner.apply_input("y") is True  # use jump

    assert runner.apply_input("bad host") is False
    assert runner.next_request().kind == "ssh_jump_host"


def test_remote_prompt_runner_jump_host_defaults_to_blj():
    """When user selects jump but leaves host empty, default to blj.horizon.cc."""
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-h") is True
    assert runner.apply_input("grace") is True
    assert runner.apply_input("22") is True
    assert runner.apply_input("y") is True  # use jump
    assert runner.next_request().kind == "ssh_jump_host"

    assert runner.apply_input("") is True
    assert runner.state.ssh_jump_host == "blj.horizon.cc"
    assert runner.next_request().kind == "ssh_jump_port"


def test_remote_prompt_runner_use_jump_rejects_invalid_input():
    runner = RemotePromptRunner(existing_aliases=[])

    assert runner.apply_input("host-i") is True
    assert runner.apply_input("ivan") is True
    assert runner.apply_input("22") is True
    assert runner.next_request().kind == "use_jump"

    assert runner.apply_input("maybe") is False
    assert runner.next_request().kind == "use_jump"

    assert runner.apply_input("Y") is True
    assert runner.next_request().kind == "ssh_jump_host"
