# Remote sshpass Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Python CLI support for password-based remote collection through `sshpass -e`, using either `SSHPASS` from the environment or a one-run interactive password prompt that never persists secrets.

**Architecture:** Extend the remote config and interaction flow with a `use_sshpass` flag and an in-memory password store, then route all Python remote SSH execution through a shared launcher that can prepend `sshpass -e` and inject `SSHPASS` into subprocess environments. Keep existing SSH transport behavior unchanged for non-password remotes.

**Tech Stack:** Python, pytest, subprocess, prompt_toolkit fallback input

---

### File Map

**Files:**
- Modify: `src/llm_usage/remotes.py`
- Modify: `src/llm_usage/interaction.py`
- Modify: `src/llm_usage/main.py`
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remotes.py`
- Modify: `tests/test_remote_file_collector.py`
- Add: `tests/test_interaction.py`

Responsibilities:

- `src/llm_usage/remotes.py`: remote config parsing/persistence, ssh launcher metadata, `probe_remote_ssh()` password-aware execution
- `src/llm_usage/interaction.py`: interactive `use_sshpass` selection and password prompting
- `src/llm_usage/main.py`: runtime orchestration for prompted passwords and selected remotes
- `src/llm_usage/collectors/remote_file.py`: password-aware SSH subprocess launching for probe/collect/upload fallback
- `tests/test_remotes.py`: config parsing/persistence and password-aware probe execution
- `tests/test_remote_file_collector.py`: password-aware collector execution
- `tests/test_interaction.py`: interaction flow and “do not persist password” boundaries

### Task 1: Add Config Tests For `use_sshpass`

**Files:**
- Modify: `tests/test_remotes.py`
- Modify: `src/llm_usage/remotes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_remote_configs_from_env_reads_use_sshpass_flag():
    env = {
        "REMOTE_HOSTS": "server_a",
        "REMOTE_SERVER_A_SSH_HOST": "host-a",
        "REMOTE_SERVER_A_SSH_USER": "alice",
        "REMOTE_SERVER_A_USE_SSHPASS": "1",
    }

    configs = parse_remote_configs_from_env(env)

    assert configs[0].use_sshpass is True


def test_append_remote_to_env_writes_use_sshpass_flag(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    config = build_temporary_remote("host-b", "bob", 2200, use_sshpass=True)

    append_remote_to_env(env_path, config, [])

    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_BOB_HOST_B_USE_SSHPASS=1" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remotes.py -k use_sshpass -v`
Expected: FAIL because `RemoteHostConfig` / `build_temporary_remote()` do not expose `use_sshpass`, and `.env` persistence does not write the flag.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class RemoteHostConfig:
    ...
    is_ephemeral: bool = False
    use_sshpass: bool = False


def parse_remote_configs_from_env(env: dict[str, str] | None = None) -> list[RemoteHostConfig]:
    ...
            RemoteHostConfig(
                ...,
                use_sshpass=_env_flag(data.get(prefix + "USE_SSHPASS", "")),
            )


def build_temporary_remote(
    ssh_host: str,
    ssh_user: str,
    ssh_port: int = 22,
    claude_log_paths: list[str] | None = None,
    codex_log_paths: list[str] | None = None,
    use_sshpass: bool = False,
) -> RemoteHostConfig:
    ...
        use_sshpass=use_sshpass,


def append_remote_to_env(path: Path, config: RemoteHostConfig, existing_aliases: list[str]) -> str:
    ...
    upsert_env_var(path, prefix + "USE_SSHPASS", "1" if config.use_sshpass else "0")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remotes.py -k use_sshpass -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remotes.py src/llm_usage/remotes.py
git commit -m "feat: persist remote sshpass setting"
```

### Task 2: Add Password-Aware Probe Tests

**Files:**
- Modify: `tests/test_remotes.py`
- Modify: `src/llm_usage/remotes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_probe_remote_ssh_uses_sshpass_env(monkeypatch):
    captured = {}

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001, ANN201
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("llm_usage.remotes.subprocess.run", _fake_run)

    ok, msg = probe_remote_ssh(
        build_temporary_remote("host-a", "alice", 2200, use_sshpass=True),
        ssh_password="secret",
    )

    assert ok
    assert captured["cmd"][:2] == ["sshpass", "-e"]
    assert captured["cmd"][2] == "ssh"
    assert captured["env"]["SSHPASS"] == "secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remotes.py::test_probe_remote_ssh_uses_sshpass_env -v`
Expected: FAIL because `probe_remote_ssh()` does not accept password input or wrap the command with `sshpass`.

- [ ] **Step 3: Write minimal implementation**

```python
def build_ssh_command(
    destination: str,
    port: int,
    *,
    use_connection_sharing: bool = True,
    use_sshpass: bool = False,
) -> list[str]:
    ssh_command = _ssh_base_command(destination, port, use_connection_sharing=use_connection_sharing)
    if use_sshpass:
        return ["sshpass", "-e", *ssh_command]
    return ssh_command


def build_ssh_env(ssh_password: str | None = None) -> dict[str, str] | None:
    if ssh_password is None:
        return None
    env = os.environ.copy()
    env["SSHPASS"] = ssh_password
    return env


def probe_remote_ssh(config: RemoteHostConfig, timeout_sec: int = 10, ssh_password: str | None = None) -> tuple[bool, str]:
    if config.use_sshpass and not shutil.which("sshpass"):
        return False, "sshpass not found"
    if config.use_sshpass and not ssh_password and not os.getenv("SSHPASS"):
        return False, "missing SSHPASS for password-based remote"
    completed = subprocess.run(
        build_ssh_command(
            f"{config.ssh_user}@{config.ssh_host}",
            config.ssh_port,
            use_sshpass=config.use_sshpass,
        )
        + ["true"],
        ...,
        env=build_ssh_env(ssh_password) if config.use_sshpass else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remotes.py::test_probe_remote_ssh_uses_sshpass_env -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remotes.py src/llm_usage/remotes.py
git commit -m "feat: support sshpass in remote probe"
```

### Task 3: Add Collector Tests For `sshpass -e`

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `src/llm_usage/remotes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_file_collector_uses_sshpass_env_for_collect():
    captured = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None, env=None):  # noqa: ANN001, ANN201
        captured.append((cmd, env))
        if cmd[:3] == ["sshpass", "-e", "ssh"] and cmd[-1].startswith("command -v python3"):
            return _Completed(stdout="python3")
        if cmd[:3] == ["sshpass", "-e", "ssh"] and cmd[-3:-1] == ["sh", "-lc"]:
            return _Completed(stdout=json.dumps({"events": [], "warnings": []}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        patterns=["~/.codex/**/*.jsonl"],
        runner=_runner,
        use_sshpass=True,
        ssh_password="secret",
    )

    collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert captured[0][0][:2] == ["sshpass", "-e"]
    assert captured[0][1]["SSHPASS"] == "secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py::test_remote_file_collector_uses_sshpass_env_for_collect -v`
Expected: FAIL because `RemoteFileCollector` does not accept `use_sshpass` / `ssh_password` and runner calls do not pass `env`.

- [ ] **Step 3: Write minimal implementation**

```python
class RemoteFileCollector(BaseCollector):
    def __init__(..., use_sshpass: bool = False, ssh_password: str | None = None) -> None:
        ...
        self.use_sshpass = use_sshpass
        self.ssh_password = ssh_password

    def _ssh_command(self, remote_args: list[str]) -> list[str]:
        return build_ssh_command(
            self.target.destination,
            self.target.port,
            use_connection_sharing=self._use_connection_sharing,
            use_sshpass=self.use_sshpass,
        ) + remote_args

    def _ssh_env(self) -> dict[str, str] | None:
        if not self.use_sshpass:
            return None
        return build_ssh_env(self.ssh_password)

    def _run_ssh_with_optional_fallback(...):
        self._runner(
            self._ssh_command(remote_args),
            ...,
            env=self._ssh_env(),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py::test_remote_file_collector_uses_sshpass_env_for_collect -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py src/llm_usage/remotes.py
git commit -m "feat: support sshpass in remote collector"
```

### Task 4: Add Interaction Tests For Prompted Passwords

**Files:**
- Add: `tests/test_interaction.py`
- Modify: `src/llm_usage/interaction.py`
- Modify: `src/llm_usage/remotes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_select_remotes_prompts_for_temporary_sshpass_remote_password():
    stdin = io.StringIO("+\nhost-a\nalice\n2222\ny\nsecret\n")
    stdout = io.StringIO()

    result = select_remotes(
        [],
        [],
        ui_mode="cli",
        stdin=stdin,
        stdout=stdout,
        remote_validator=lambda config, ssh_password=None: (config.use_sshpass and ssh_password == "secret", "ok"),
        password_getter=lambda alias, source_name: None,
        password_setter=lambda alias, password: captured.update({alias: password}),
        interactive_password_reader=lambda prompt_text, stdin, stdout, use_prompt_toolkit: "secret",
    )

    assert result.temporary_remotes[0].use_sshpass is True
    assert captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py -v`
Expected: FAIL because interaction APIs do not expose password prompting/setter hooks or `use_sshpass`.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class RemoteSelectionResult:
    ...


def select_remotes(..., remote_validator=None, password_getter=None, password_setter=None, interactive_password_reader=None):
    ...

def _prompt_temporary_remote(...):
    ...
    use_sshpass = _read_yes_no("是否使用 sshpass 密码登录？[y/N]: ", ...)
    config = build_temporary_remote(host, user, port, use_sshpass=use_sshpass)
    ssh_password = _resolve_runtime_password(config, password_getter, password_setter, interactive_password_reader, ...)
    ok, message = remote_validator(config, ssh_password=ssh_password)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_interaction.py src/llm_usage/interaction.py src/llm_usage/remotes.py
git commit -m "feat: prompt for sshpass passwords during remote selection"
```

### Task 5: Wire Runtime Password Store Into Main Flow

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `src/llm_usage/remotes.py`
- Modify: `src/llm_usage/interaction.py`
- Modify: `tests/test_interaction.py`
- Modify: `tests/test_remotes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_runtime_password_is_not_persisted_to_env_or_state(tmp_path):
    env_path = tmp_path / ".env"
    state_path = tmp_path / "runtime_state.json"
    env_path.write_text("", encoding="utf-8")

    config = build_temporary_remote("host-a", "alice", use_sshpass=True)
    append_remote_to_env(env_path, config, [])
    save_selected_remote_aliases(state_path, [config.alias])

    assert "secret" not in env_path.read_text(encoding="utf-8")
    assert "secret" not in state_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py tests/test_remotes.py -k password -v`
Expected: FAIL once runtime wiring starts persisting too much or if the test needs the new helper structure and it does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def _build_runtime_remote_passwords() -> dict[str, str]:
    return {}


def _resolve_remote_selection(...):
    runtime_passwords = _build_runtime_remote_passwords()
    result = select_remotes(
        ...,
        password_getter=lambda alias, _source_name: runtime_passwords.get(alias) or os.getenv("SSHPASS"),
        password_setter=lambda alias, password: runtime_passwords.__setitem__(alias, password),
    )
    return result.selected_aliases, result.temporary_remotes, runtime_passwords


collectors.extend(
    build_remote_collectors(
        selected_configs,
        username=username,
        salt=salt,
        runtime_passwords=runtime_passwords,
    )
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py tests/test_remotes.py -k password -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/main.py src/llm_usage/interaction.py src/llm_usage/remotes.py tests/test_interaction.py tests/test_remotes.py
git commit -m "feat: keep remote sshpass passwords in runtime memory only"
```

### Task 6: Run Focused Verification

**Files:**
- Modify: `tests/test_remotes.py`
- Modify: `tests/test_remote_file_collector.py`
- Add: `tests/test_interaction.py`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_remotes.py tests/test_remote_file_collector.py tests/test_interaction.py -v`
Expected: PASS

- [ ] **Step 2: Run targeted CLI-path tests if available**

Run: `pytest tests/test_main_identity.py tests/test_cursor_login.py -v`
Expected: PASS or explicit confirmation they are unaffected

- [ ] **Step 3: Review warning/error messages**

Check that the following strings are covered and do not expose passwords:

```text
sshpass not found
missing SSHPASS for password-based remote
SSH authentication failure messages without secret echo
```

- [ ] **Step 4: Commit final verification snapshot**

```bash
git add tests/test_remotes.py tests/test_remote_file_collector.py tests/test_interaction.py src/llm_usage/main.py src/llm_usage/interaction.py src/llm_usage/remotes.py src/llm_usage/collectors/remote_file.py
git commit -m "feat: add sshpass support for password-based remotes"
```

## Self-Review

Spec coverage:

- `REMOTE_<ALIAS>_USE_SSHPASS=1`: Task 1
- `SSHPASS` env reuse and runtime prompt: Tasks 2, 4, 5
- shared SSH launcher for probe/collect: Tasks 2, 3
- no password persistence: Tasks 4, 5
- clear non-interactive / missing-tool failures: Tasks 2, 6

Placeholder scan:

- No `TODO` / `TBD` placeholders remain
- Each task includes exact file paths, commands, and concrete code snippets

Type consistency:

- `RemoteHostConfig.use_sshpass`
- `probe_remote_ssh(..., ssh_password=None)`
- `RemoteFileCollector(..., use_sshpass=False, ssh_password=None)`
- `build_remote_collectors(..., runtime_passwords=None)`

