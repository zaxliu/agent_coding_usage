# Web Remote Input State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the web console collect all remote-login inputs in browser modals while keeping the Python CLI interaction flow unchanged for end users.

**Architecture:** Extract remote-selection and remote-validation prompts into a transport-agnostic interaction state machine that emits structured input requests instead of reading from stdin directly. Keep a CLI adapter that drives the state machine synchronously in-terminal, and add a Web adapter that pauses jobs on pending input and resumes them through `/api/jobs/:id/input`.

**Tech Stack:** Python 3, existing `llm_usage.interaction` / `llm_usage.web` modules, pytest, browser-side JS modal flow

---

### Task 1: Extract Transport-Agnostic Input Request Types

**Files:**
- Create: `src/llm_usage/interaction_flow.py`
- Modify: `src/llm_usage/interaction.py`
- Test: `tests/test_interaction_flow.py`

- [ ] **Step 1: Write the failing test**

```python
from llm_usage.interaction_flow import (
    InputRequest,
    RemoteFlowState,
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
    assert password_req.kind == "ssh_password"
    assert password_req.remote_alias == "SERVER_A"
    assert password_req.secret is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction_flow.py::test_input_request_shapes_for_remote_steps -q`
Expected: FAIL with `ModuleNotFoundError` or missing symbols from `interaction_flow`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from typing import Optional


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
    use_sshpass: bool = False


def request_ssh_host_step() -> InputRequest:
    return InputRequest(kind="ssh_host", message="SSH 主机：")


def request_ssh_password_step(alias: str) -> InputRequest:
    return InputRequest(
        kind="ssh_password",
        message=f"请输入 {alias} 的 SSH 密码：",
        remote_alias=alias,
        secret=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction_flow.py::test_input_request_shapes_for_remote_steps -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/interaction_flow.py src/llm_usage/interaction.py tests/test_interaction_flow.py
git commit -m "feat: add remote interaction request types"
```

### Task 2: Move Temporary Remote Prompt Flow Into a Stateful Runner

**Files:**
- Modify: `src/llm_usage/interaction_flow.py`
- Modify: `src/llm_usage/interaction.py`
- Test: `tests/test_interaction_flow.py`

- [ ] **Step 1: Write the failing test**

```python
from llm_usage.interaction_flow import RemotePromptRunner


def test_remote_prompt_runner_collects_temporary_remote_values_in_order():
    runner = RemotePromptRunner(existing_aliases=["SERVER_A"])

    first = runner.next_request()
    assert first.kind == "ssh_host"

    runner.apply_input("host-b")
    assert runner.next_request().kind == "ssh_user"

    runner.apply_input("alice")
    assert runner.next_request().kind == "ssh_port"

    runner.apply_input("2200")
    assert runner.next_request().kind == "use_sshpass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction_flow.py::test_remote_prompt_runner_collects_temporary_remote_values_in_order -q`
Expected: FAIL because `RemotePromptRunner` does not exist or does not advance state

- [ ] **Step 3: Write minimal implementation**

```python
class RemotePromptRunner:
    def __init__(self, existing_aliases: list[str]) -> None:
        self.existing_aliases = existing_aliases
        self.state = RemoteFlowState()
        self.stage = "ssh_host"

    def next_request(self) -> Optional[InputRequest]:
        if self.stage == "ssh_host":
            return InputRequest(kind="ssh_host", message="SSH 主机：")
        if self.stage == "ssh_user":
            return InputRequest(kind="ssh_user", message="SSH 用户：")
        if self.stage == "ssh_port":
            return InputRequest(kind="ssh_port", message="SSH 端口 [22]：")
        if self.stage == "use_sshpass":
            return InputRequest(kind="use_sshpass", message="是否使用 sshpass？[y/N]：")
        return None

    def apply_input(self, value: str) -> None:
        if self.stage == "ssh_host":
            self.state.ssh_host = value.strip()
            self.stage = "ssh_user"
        elif self.stage == "ssh_user":
            self.state.ssh_user = value.strip()
            self.stage = "ssh_port"
        elif self.stage == "ssh_port":
            self.state.ssh_port = int(value.strip() or "22")
            self.stage = "use_sshpass"
        elif self.stage == "use_sshpass":
            self.state.use_sshpass = value.strip().lower() in {"y", "yes", "1", "true"}
            self.stage = "done"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction_flow.py::test_remote_prompt_runner_collects_temporary_remote_values_in_order -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/interaction_flow.py src/llm_usage/interaction.py tests/test_interaction_flow.py
git commit -m "feat: add stateful temporary remote runner"
```

### Task 3: Keep CLI Behavior by Adapting the State Machine to Terminal Prompts

**Files:**
- Modify: `src/llm_usage/interaction.py`
- Modify: `src/llm_usage/main.py`
- Test: `tests/test_interaction.py`

- [ ] **Step 1: Write the failing test**

```python
def test_select_remotes_cli_behavior_still_prompts_in_terminal_order(monkeypatch):
    prompts = []

    def fake_read_line(prompt_text, **_kwargs):
        prompts.append(prompt_text)
        answers = {
            "SSH 主机：": "host-b",
            "SSH 用户：": "alice",
            "SSH 端口 [22]：": "22",
            "是否使用 sshpass？[y/N]：": "n",
        }
        return answers[prompt_text]

    monkeypatch.setattr("llm_usage.interaction._read_line", fake_read_line)
    result = select_remotes([], ui_mode="cli")

    assert prompts[:4] == ["SSH 主机：", "SSH 用户：", "SSH 端口 [22]：", "是否使用 sshpass？[y/N]："]
    assert result.mode_used == "cli"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py::test_select_remotes_cli_behavior_still_prompts_in_terminal_order -q`
Expected: FAIL because prompt flow no longer matches direct terminal order

- [ ] **Step 3: Write minimal implementation**

```python
def _drive_runner_in_cli(runner, *, stdin, stdout, use_prompt_toolkit):
    while True:
        request = runner.next_request()
        if request is None:
            return runner
        if request.kind == "ssh_password":
            value = _read_password(
                request.message,
                stdin=stdin,
                stdout=stdout,
                use_prompt_toolkit=use_prompt_toolkit,
                interactive_password_reader=None,
            )
        else:
            value = _read_line(
                request.message,
                stdin=stdin,
                stdout=stdout,
                use_prompt_toolkit=use_prompt_toolkit,
            )
        runner.apply_input(value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py::test_select_remotes_cli_behavior_still_prompts_in_terminal_order -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/interaction.py src/llm_usage/main.py tests/test_interaction.py
git commit -m "refactor: keep cli remote prompts via adapter"
```

### Task 4: Add Web-Pausable Remote Input Flow for Temporary Remote Setup and Confirmations

**Files:**
- Modify: `src/llm_usage/web.py`
- Modify: `web/app.js`
- Modify: `web/index.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing test**

```python
def test_web_remote_setup_returns_structured_input_request_sequence(tmp_path, monkeypatch):
    service = web.WebService()
    queued = service.start_doctor({"remote_setup": True})

    assert queued["status"] == "needs_input"
    assert queued["input_request"]["kind"] == "ssh_host"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py::test_web_remote_setup_returns_structured_input_request_sequence -q`
Expected: FAIL because `start_doctor` does not yet pause on remote setup input

- [ ] **Step 3: Write minimal implementation**

```python
def _start_remote_setup_flow(self, payload: dict[str, Any]) -> dict[str, Any]:
    runner = RemotePromptRunner(existing_aliases=[config.alias for config in parse_remote_configs_from_env()])
    request = runner.next_request()

    def resume_handler(value: str) -> dict[str, Any]:
        runner.apply_input(value)
        next_request = runner.next_request()
        if next_request is not None:
            raise _JobNeedsInput(next_request.__dict__, resume_handler)
        return {"remote_setup": runner.state}

    if request is not None:
        return self.jobs.create_needs_input("remote_setup", request.__dict__, resume_handler)
    return {"remote_setup": runner.state}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_web.py::test_web_remote_setup_returns_structured_input_request_sequence -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/web.py web/app.js web/index.html tests/test_web.py
git commit -m "feat: add web-paused remote setup input flow"
```

### Task 5: Support Non-Password Input Kinds in the Existing Browser Modal

**Files:**
- Modify: `web/app.js`
- Modify: `web/index.html`
- Modify: `web/app.css`
- Test: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
test("credential prompt renders confirm and text input requests in browser-friendly form", () => {
  const request = {
    kind: "confirm",
    message: "Save this temporary remote to .env?",
    choices: ["yes", "no"],
  };

  const ui = describeInputRequest(request);
  assert.equal(ui.inputType, "confirm");
  assert.deepEqual(ui.choices, ["yes", "no"]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test node/test/web-app.test.js`
Expected: FAIL because confirm/text request rendering is not implemented

- [ ] **Step 3: Write minimal implementation**

```javascript
export function describeInputRequest(request = {}) {
  if (request.kind === "confirm") {
    return { inputType: "confirm", choices: request.choices || ["yes", "no"] };
  }
  if (request.kind === "ssh_password") {
    return { inputType: "password", choices: [] };
  }
  return { inputType: "text", choices: request.choices || [] };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test node/test/web-app.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/index.html web/app.css node/test/web-app.test.js
git commit -m "feat: support browser input kinds beyond passwords"
```

### Task 6: End-to-End Regression Checks for CLI Compatibility and Web Resume Flow

**Files:**
- Modify: `tests/test_interaction.py`
- Modify: `tests/test_web.py`
- Modify: `node/test/web-app.test.js`

- [ ] **Step 1: Write the failing test**

```python
def test_cli_and_web_share_same_remote_input_sequence():
    runner = RemotePromptRunner(existing_aliases=[])
    assert [runner.next_request().kind] == ["ssh_host"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py tests/test_web.py -q`
Expected: FAIL because shared sequence coverage is incomplete

- [ ] **Step 3: Write minimal implementation**

```python
def _request_kinds(runner, values):
    kinds = []
    for value in values:
        request = runner.next_request()
        if request is None:
            break
        kinds.append(request.kind)
        runner.apply_input(value)
    return kinds
```

```javascript
test("dismissed browser input request stays hidden until a different pending job appears", () => {
  const jobs = [
    { id: "job-1", status: "needs_input", input_request: { kind: "ssh_host", message: "SSH 主机：" } },
    { id: "job-2", status: "needs_input", input_request: { kind: "ssh_password", message: "Password" } },
  ];
  assert.equal(nextCredentialPromptJob(jobs, "job-1").id, "job-2");
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py tests/test_web.py -q && node --test node/test/web-app.test.js node/test/web.test.js node/test/cli.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_interaction.py tests/test_web.py node/test/web-app.test.js
git commit -m "test: cover shared remote input flow"
```
