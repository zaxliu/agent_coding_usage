# Remote Python Version Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure SSH remote collection only uses a Python interpreter whose version satisfies `project.requires-python` from `pyproject.toml`.

**Architecture:** Keep the existing shell/path discovery sequence in `RemoteFileCollector`, but add a version-validation helper that compares each candidate interpreter against a minimum-version tuple parsed from `pyproject.toml`. Tests drive the change from the current "accept first python-like name" behavior to "accept first compatible version".

**Tech Stack:** Python 3.9+, `pathlib`, `re`, remote SSH command runner, `pytest`.

---

### Task 1: Add test coverage for incompatible remote Python candidates

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Test: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Write the failing regression tests**

```python
def test_remote_file_collector_skips_python2_candidate_and_uses_later_python3(tmp_path):
    calls = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):
        calls.append(cmd)
        remote = _remote_command(cmd) if cmd[:1] == ["ssh"] else ""
        if remote == "'sh' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(stdout="python\n")
        if remote == "'sh' '-lc' 'python -c '"'"'import sys; print(f\"{sys.version_info[0]}.{sys.version_info[1]}\")'"'"''":
            return _Completed(stdout="2.7\n")
        if remote == "'bash' '-lc' 'command -v python3 >/dev/null 2>&1 && command -v python3 || (command -v python >/dev/null 2>&1 && command -v python || true)'":
            return _Completed(stdout="/opt/homebrew/bin/python3\n")
        if "/opt/homebrew/bin/python3 -c " in remote:
            return _Completed(stdout="3.9\n")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=json.dumps({"matches": 1}))
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    ok, msg = collector.probe()

    assert ok
    assert "remote files detected" in msg


def test_remote_file_collector_errors_when_all_remote_python_candidates_are_too_old(tmp_path):
    def _runner(cmd, check, capture_output, text, input=None, timeout=None):
        remote = _remote_command(cmd) if cmd[:1] == ["ssh"] else ""
        if "command -v python3" in remote:
            return _Completed(stdout="python3\n")
        if "python3 -c " in remote:
            return _Completed(stdout="3.8\n")
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    ok, msg = collector.probe()

    assert not ok
    assert ">=3.9" in msg
```

- [ ] **Step 2: Run the focused tests and verify they fail for the expected reason**

Run: `pytest tests/test_remote_file_collector.py -q`

Expected: FAIL because the collector still accepts the first `python` or `python3` candidate without checking its version, and because the compatibility error message does not exist yet.

### Task 2: Parse the minimum supported Python version from `pyproject.toml`

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Test: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Add a focused parser test**

```python
def test_remote_python_minimum_version_matches_pyproject_requirement():
    assert remote_file._remote_python_minimum_version() == (3, 9)
```

- [ ] **Step 2: Run the focused parser test and verify it fails**

Run: `pytest tests/test_remote_file_collector.py::test_remote_python_minimum_version_matches_pyproject_requirement -q`

Expected: FAIL with `AttributeError` because `_remote_python_minimum_version` does not exist yet.

- [ ] **Step 3: Add the parser helper in `remote_file.py`**

```python
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT_PATH = _PROJECT_ROOT / "pyproject.toml"


def _remote_python_minimum_version() -> tuple[int, int]:
    text = _PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(r"(?m)^requires-python\s*=\s*\"\\s*>=(\\d+)\\.(\\d+)\\s*\"\\s*$", text)
    if not match:
        raise RuntimeError("Unable to determine remote Python minimum version from pyproject.toml")
    return int(match.group(1)), int(match.group(2))
```

- [ ] **Step 4: Re-run the parser test and verify it passes**

Run: `pytest tests/test_remote_file_collector.py::test_remote_python_minimum_version_matches_pyproject_requirement -q`

Expected: PASS

### Task 3: Validate each remote Python candidate before accepting it

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Test: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Add a helper that probes candidate version**

```python
def _extract_python_version(stdout: str) -> Optional[tuple[int, int]]:
    match = re.search(r"(\\d+)\\.(\\d+)", stdout)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
```

- [ ] **Step 2: Update discovery to skip incompatible candidates**

```python
def _discover_python(self) -> tuple[Optional[str], Optional[str]]:
    required_version = _remote_python_minimum_version()
    saw_incompatible = False
    for remote_args in _python_discovery_commands():
        completed = self._run_ssh_with_optional_fallback(remote_args, timeout=15)
        # existing error handling omitted
        python_cmd = _extract_python_command(completed.stdout)
        if not python_cmd:
            continue
        version_completed = self._run_ssh_with_optional_fallback(
            ["sh", "-lc", f"{_shell_quote(python_cmd)} -c {_shell_quote('import sys; print(f\"{sys.version_info[0]}.{sys.version_info[1]}\")')}"],
            timeout=15,
        )
        version = _extract_python_version(version_completed.stdout if version_completed else "")
        if version is None or version < required_version:
            saw_incompatible = True
            continue
        return python_cmd, None
    if saw_incompatible:
        return None, f"remote Python does not satisfy >={required_version[0]}.{required_version[1]}"
    return None, None
```

- [ ] **Step 3: Run the focused regression tests and verify they pass**

Run: `pytest tests/test_remote_file_collector.py -q`

Expected: PASS for the new tests, and no regressions in existing remote collector tests.

### Task 4: Run a broader verification pass

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Run the targeted remote collector suite**

Run: `pytest tests/test_remote_file_collector.py -q`

Expected: PASS

- [ ] **Step 2: Run a slightly broader safety check**

Run: `pytest tests/test_packaging.py tests/test_remote_file_collector.py -q`

Expected: PASS
