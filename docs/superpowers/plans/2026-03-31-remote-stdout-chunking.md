# Remote Stdout Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make remote collection reliable on restrictive bastion hosts by replacing single-line JSON stdout payloads with an ASCII-safe chunked stdout protocol that the local collector reassembles and verifies before parsing.

**Architecture:** Keep the existing single-SSH remote execution flow and existing stderr progress logs. Change the remote collect script to emit a chunked `base64(JSON)` protocol on stdout, add a local decoder that validates headers, chunk count, byte length, and hash before calling `json.loads()`, and retain the current direct-JSON extractor as a fallback for older outputs and noise-tolerant cases.

**Tech Stack:** Python, pytest, subprocess, base64, hashlib, JSON

---

### File Map

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remote_file_collector.py`

Responsibilities:

- `src/llm_usage/collectors/remote_file.py`: define the chunked stdout protocol, teach the remote collect script to emit chunked `base64(JSON)`, decode and validate chunked output locally, and keep legacy JSON parsing as a compatibility fallback.
- `tests/test_remote_file_collector.py`: cover chunked happy paths, chunk corruption paths, protocol/noise coexistence, and backward compatibility with direct JSON payloads.

### Task 1: Add Failing Tests For Chunked Stdout Decoding

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_file_collector_collect_accepts_chunked_stdout(monkeypatch):
    printed: list[str] = []
    payload = {"events": [], "warnings": []}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=24)

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=chunked)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert not any("remote stdout noise:" in line for line in printed)


def test_decode_chunked_stdout_payload_rejects_missing_chunk():
    payload = {"events": [{"tool": "claude_code"}], "warnings": []}
    chunked = remote_file._encode_chunked_stdout_payload(payload, chunk_size=12).splitlines()
    broken = "\n".join(line for line in chunked if " index=1 " not in line)

    parsed, discarded, error = remote_file._decode_chunked_stdout_payload(broken)

    assert parsed is None
    assert discarded == broken
    assert error == "remote chunked stdout missing chunks"


def test_decode_chunked_stdout_payload_rejects_hash_mismatch():
    payload = {"events": [{"tool": "claude_code"}], "warnings": []}
    lines = remote_file._encode_chunked_stdout_payload(payload, chunk_size=12).splitlines()
    lines[1] = lines[1].replace("Y2xhdWRl", "Y29kZXg=", 1)

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload("\n".join(lines))

    assert parsed is None
    assert error == "remote chunked stdout hash mismatch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k chunked_stdout -v`
Expected: FAIL because no chunked stdout encoder/decoder exists, and `collect()` only knows how to extract direct JSON payloads.

- [ ] **Step 3: Write minimal implementation**

```python
_CHUNKED_STDOUT_PREFIX = "LLMUSAGE_CHUNKED_V1"
_DEFAULT_STDOUT_CHUNK_SIZE = 32 * 1024


def _encode_chunked_stdout_payload(payload: dict[str, object], *, chunk_size: int = _DEFAULT_STDOUT_CHUNK_SIZE) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    blob = base64.b64encode(raw).decode("ascii")
    chunks = [blob[index : index + chunk_size] for index in range(0, len(blob), chunk_size)] or [""]
    lines = [
        f"{_CHUNKED_STDOUT_PREFIX} BEGIN total_chunks={len(chunks)} total_bytes={len(raw)} sha256={digest}",
    ]
    for index, chunk in enumerate(chunks):
        lines.append(f"{_CHUNKED_STDOUT_PREFIX} CHUNK index={index} data={chunk}")
    lines.append(f"{_CHUNKED_STDOUT_PREFIX} END")
    return "\n".join(lines)


def _decode_chunked_stdout_payload(stdout: str) -> tuple[Optional[dict[str, object]], str, Optional[str]]:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py -k chunked_stdout -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "feat: add chunked remote stdout decoding"
```

### Task 2: Switch Remote Collect Output To Chunked Protocol With Legacy Fallback

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_collect_script_emits_chunked_stdout_protocol(tmp_path):
    collector = RemoteFileCollector(
        "remote",
        target=SshTarget(host="host", user="alice", port=22),
        source_name="server_a",
        source_host_hash="hash",
        jobs=[RemoteCollectJob(tool="codex", patterns=["~/.codex/**/*.jsonl"])],
        runner=lambda *args, **kwargs: _Completed(stdout="python3"),
    )

    command, script_input = collector._python_stdin_command("python3", remote_file._COLLECT_SCRIPT)
    payload = _extract_stdin_payload(script_input)

    assert payload["jobs"] == [{"tool": "codex", "patterns": ["~/.codex/**/*.jsonl"]}]
    assert "_emit_chunked_payload" in script_input
    assert "LLMUSAGE_CHUNKED_V1 BEGIN" in script_input
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k emits_chunked_stdout_protocol -v`
Expected: FAIL because `_COLLECT_SCRIPT` still ends with `print(json.dumps({"events": events, "warnings": warnings}))`.

- [ ] **Step 3: Write minimal implementation**

```python
_REMOTE_CHUNKED_STDOUT_HELPER = """
def _emit_chunked_payload(payload):
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    blob = base64.b64encode(raw).decode('ascii')
    chunk_size = 32768
    print('LLMUSAGE_CHUNKED_V1 BEGIN total_chunks=' + str((len(blob) + chunk_size - 1) // chunk_size or 1) + ' total_bytes=' + str(len(raw)) + ' sha256=' + digest)
    if not blob:
        print('LLMUSAGE_CHUNKED_V1 CHUNK index=0 data=')
    else:
        for index in range(0, len(blob), chunk_size):
            print('LLMUSAGE_CHUNKED_V1 CHUNK index=' + str(index // chunk_size) + ' data=' + blob[index:index + chunk_size])
    print('LLMUSAGE_CHUNKED_V1 END')
"""

_COLLECT_SCRIPT = (
    """
import base64, glob, hashlib, json, os, re, sys
...
_emit_chunked_payload({"events": events, "warnings": warnings})
"""
).replace("__REMOTE_CHUNKED_STDOUT_HELPER__", _REMOTE_CHUNKED_STDOUT_HELPER)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py -k emits_chunked_stdout_protocol -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "feat: emit chunked remote collect payloads"
```

### Task 3: Integrate Decoder, Compatibility Fallback, And Failure Diagnostics

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remote_file_collector.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_file_collector_collect_prefers_chunked_protocol_before_legacy_noise(monkeypatch):
    payload = {"events": [], "warnings": []}
    stdout = "audit prefix\n" + remote_file._encode_chunked_stdout_payload(payload, chunk_size=32) + "\naudit suffix"
    printed: list[str] = []

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=stdout)
        return _Completed()

    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(v) for v in args)))

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert out.warnings == ["server_a/codex: no usage events in selected time range"]
    assert any("remote stdout noise: audit prefix" in line for line in printed)
    assert any("remote stdout noise: audit suffix" in line for line in printed)


def test_remote_file_collector_reports_chunked_stdout_corruption():
    payload = {"events": [], "warnings": []}
    lines = remote_file._encode_chunked_stdout_payload(payload, chunk_size=16).splitlines()
    broken = "\n".join(lines[:-1])

    parsed, discarded = remote_file._extract_remote_payload_with_fallbacks(broken)

    assert parsed is None
    assert discarded == broken
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k "chunked_protocol_before_legacy_noise or reports_chunked_stdout_corruption" -v`
Expected: FAIL because `_run_python_script()` and `_run_python_script_via_uploaded_file()` still call `_extract_json_payload()` directly and have no chunked-specific corruption diagnostics.

- [ ] **Step 3: Write minimal implementation**

```python
def _extract_remote_payload_with_fallbacks(stdout: str) -> tuple[Optional[dict[str, object]], str, Optional[str]]:
    payload, discarded, chunk_error = _decode_chunked_stdout_payload(stdout)
    if payload is not None:
        return payload, discarded, None
    if chunk_error is not None:
        return None, stdout, chunk_error
    legacy_payload, discarded = _extract_json_payload(stdout)
    if isinstance(legacy_payload, dict):
        return legacy_payload, discarded, None
    if legacy_payload is None:
        return None, stdout, None
    return None, stdout, "remote command returned invalid JSON payload"


def _run_python_script(self, python_cmd: str, script: str) -> tuple[dict, Optional[str]]:
    ...
    payload, discarded, decode_error = _extract_remote_payload_with_fallbacks(completed.stdout)
    ...
    if decode_error:
        self._log_non_json_debug(completed.stdout, completed.stderr)
        return {}, decode_error
```

- [ ] **Step 4: Run targeted tests and full file**

Run: `pytest tests/test_remote_file_collector.py -k chunked -v`
Expected: PASS

Run: `pytest tests/test_remote_file_collector.py -v`
Expected: PASS, including existing direct-JSON and stdout-noise compatibility tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "fix: make remote stdout collection resilient to truncation"
```
