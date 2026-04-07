# Remote Output-Budget Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split remote collection into multiple SSH pages capped by a conservative stdout budget so large bastion-host collections complete without stdout truncation.

**Architecture:** Extend the remote collect request/response contract with a page budget and opaque cursor, teach the embedded Python collect script to stop before exceeding the page budget and return `next_cursor`, then loop locally in `RemoteFileCollector.collect()` until the remote script reports completion. Keep the existing chunked stdout transport for each page and preserve legacy direct-JSON compatibility when the chunk prefix is absent.

**Tech Stack:** Python, pytest, subprocess, JSON, base64, hashlib

---

### File Map

**Files:**
- Modify: `src/llm_usage/collectors/remote_file.py`
- Modify: `tests/test_remote_file_collector.py`

Responsibilities:

- `src/llm_usage/collectors/remote_file.py`: add page-budget/cursor request fields, implement remote pagination in `_COLLECT_SCRIPT`, validate page payloads, and loop locally across pages with cursor-progress protection.
- `tests/test_remote_file_collector.py`: cover page request fields, remote script page payload shape, multi-page aggregation, JSONL resume, invalid cursor handling, and repeated-cursor protection.

### Task 1: Add Local Page Payload Validation Helpers

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`

- [ ] **Step 1: Write the failing test**

```python
def test_extract_remote_page_payload_accepts_null_cursor():
    payload, discarded, error = remote_file._extract_remote_page_payload(
        remote_file._encode_chunked_stdout_payload(
            {"events": [], "warnings": [], "next_cursor": None},
            chunk_size=80,
        )
    )

    assert error is None
    assert discarded == ""
    assert payload == {"events": [], "warnings": [], "next_cursor": None}


def test_extract_remote_page_payload_rejects_invalid_cursor_shape():
    payload, _discarded, error = remote_file._extract_remote_page_payload(
        remote_file._encode_chunked_stdout_payload(
            {
                "events": [],
                "warnings": [],
                "next_cursor": {"job_index": 0, "pattern_index": "bad", "file_index": 1, "line_index": 2},
            },
            chunk_size=80,
        )
    )

    assert payload is None
    assert error == "remote pagination returned invalid cursor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k "extract_remote_page_payload" -v`
Expected: FAIL because `_extract_remote_page_payload` does not exist yet and current extraction only validates raw dict payloads.

- [ ] **Step 3: Write minimal implementation**

```python
_DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES = 600 * 1024


def _is_valid_remote_cursor(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    required = ("job_index", "pattern_index", "file_index", "line_index")
    return all(isinstance(value.get(key), int) and value.get(key) >= 0 for key in required)


def _extract_remote_page_payload(stdout: str) -> tuple[Optional[dict[str, object]], str, Optional[str]]:
    payload, discarded, chunked_error = _extract_remote_payload_with_fallbacks(stdout)
    if chunked_error is not None:
        return None, discarded, chunked_error
    if payload is None:
        return None, discarded, None
    if not isinstance(payload, dict):
        return None, discarded, "remote command returned invalid JSON payload"
    if not isinstance(payload.get("events"), list):
        return None, discarded, "remote collect returned invalid payload"
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        return None, discarded, "remote collect returned invalid payload"
    if not _is_valid_remote_cursor(payload.get("next_cursor")):
        return None, discarded, "remote pagination returned invalid cursor"
    return payload, discarded, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py -k "extract_remote_page_payload" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "test: validate paged remote payloads"
```

### Task 2: Add Cursor And Budget To The Remote Collect Contract

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_file_collector_builds_collect_payload_with_page_budget_and_cursor():
    collector = RemoteFileCollector(
        "codex",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.codex/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=lambda *args, **kwargs: _Completed(stdout="python3"),
    )
    collector._active_start_value = datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc)
    collector._active_end_value = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)

    first_payload = collector._build_remote_payload()
    cursor_payload = collector._build_remote_payload({"job_index": 0, "pattern_index": 1, "file_index": 2, "line_index": 3})

    assert first_payload["stdout_page_budget_bytes"] == remote_file._DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES
    assert first_payload["cursor"] is None
    assert cursor_payload["cursor"] == {"job_index": 0, "pattern_index": 1, "file_index": 2, "line_index": 3}


def test_remote_collect_script_emits_next_cursor_when_budget_is_tight(tmp_path):
    fake_file = tmp_path / "page.jsonl"
    fake_file.write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": f"2026-03-08T00:{idx:02d}:00Z",
                    "model": "fake_model",
                    "usage": {"input_tokens": 1234, "cache_tokens": 456, "output_tokens": 321},
                }
            )
            for idx in range(3)
        ),
        encoding="utf-8",
    )
    payload = {
        "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
        "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
        "end_ts": datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc).timestamp(),
        "max_files": 400,
        "max_total_bytes": 64 * 1024 * 1024,
        "stdout_page_budget_bytes": 200,
        "cursor": None,
    }
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(
            remote_file._COLLECT_SCRIPT,
            {"PAYLOAD_B64": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"), "__name__": "__main__"},
        )

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
    assert error is None
    assert parsed is not None
    assert parsed["next_cursor"] is not None
    assert parsed["events"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k "page_budget_and_cursor or emits_next_cursor_when_budget_is_tight" -v`
Expected: FAIL because `_build_remote_payload()` does not accept a cursor and `_COLLECT_SCRIPT` does not read `cursor` / `stdout_page_budget_bytes`.

- [ ] **Step 3: Write minimal implementation**

```python
def _build_remote_payload(self, cursor: Optional[dict[str, int]] = None) -> dict[str, object]:
    return {
        "jobs": [{"tool": job.tool, "patterns": job.patterns} for job in self.jobs],
        "start_ts": self._active_start.timestamp(),
        "end_ts": self._active_end.timestamp(),
        "max_files": self.max_files,
        "max_total_bytes": self.max_total_bytes,
        "stdout_page_budget_bytes": _DEFAULT_REMOTE_STDOUT_PAGE_BUDGET_BYTES,
        "cursor": cursor,
    }
```

```python
# inside _COLLECT_SCRIPT
stdout_page_budget_bytes = int(payload.get("stdout_page_budget_bytes", 0) or 0)
cursor = payload.get("cursor") if isinstance(payload.get("cursor"), dict) else None

def _make_cursor(job_index, pattern_index, file_index, line_index):
    return {
        "job_index": job_index,
        "pattern_index": pattern_index,
        "file_index": file_index,
        "line_index": line_index,
    }

def _event_size_bytes(item):
    return len(json.dumps(item, separators=(",", ":")).encode("utf-8"))

def _wrapper_overhead(next_cursor, warnings_count):
    cursor_blob = json.dumps(next_cursor, separators=(",", ":")) if next_cursor is not None else "null"
    return len('{"events":[],"warnings":[],"next_cursor":}'.encode("utf-8")) + len(cursor_blob.encode("utf-8")) + warnings_count * 32
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py -k "page_budget_and_cursor or emits_next_cursor_when_budget_is_tight" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "feat: add remote pagination request contract"
```

### Task 3: Page The Remote Script And Resume Within JSONL Files

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_collect_script_resumes_jsonl_from_line_cursor(tmp_path):
    fake_file = tmp_path / "resume.jsonl"
    fake_file.write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": f"2026-03-08T00:{idx:02d}:00Z",
                    "model": "fake_model",
                    "usage": {"input_tokens": 100 + idx, "cache_tokens": 10, "output_tokens": 5},
                }
            )
            for idx in range(4)
        ),
        encoding="utf-8",
    )
    payload = {
        "jobs": [{"tool": "claude_code", "patterns": [str(fake_file)]}],
        "start_ts": datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc).timestamp(),
        "end_ts": datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc).timestamp(),
        "max_files": 400,
        "max_total_bytes": 64 * 1024 * 1024,
        "stdout_page_budget_bytes": 10_000,
        "cursor": {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 2},
    }
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(
            remote_file._COLLECT_SCRIPT,
            {"PAYLOAD_B64": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"), "__name__": "__main__"},
        )

    parsed, _discarded, error = remote_file._decode_chunked_stdout_payload(stdout.getvalue())
    assert error is None
    assert [item["input_tokens"] for item in parsed["events"]] == [102, 103]
    assert parsed["next_cursor"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k "resumes_jsonl_from_line_cursor" -v`
Expected: FAIL because `_COLLECT_SCRIPT` ignores `cursor` and always scans from the beginning.

- [ ] **Step 3: Write minimal implementation**

```python
cursor_job = cursor.get("job_index", 0) if cursor else 0
cursor_pattern = cursor.get("pattern_index", 0) if cursor else 0
cursor_file = cursor.get("file_index", 0) if cursor else 0
cursor_line = cursor.get("line_index", 0) if cursor else 0

for job_index, spec in enumerate(jobs):
    if job_index < cursor_job:
        continue
    active_tool = spec.get("tool", "unknown")
    patterns = spec.get("patterns", [])
    for pattern_index, pattern in enumerate(patterns):
        if job_index == cursor_job and pattern_index < cursor_pattern:
            continue
        matches = [path for path in glob.glob(os.path.expanduser(pattern), recursive=True) if os.path.isfile(path)]
        for file_index, path in enumerate(matches):
            if job_index == cursor_job and pattern_index == cursor_pattern and file_index < cursor_file:
                continue
            start_line = cursor_line if (job_index, pattern_index, file_index) == (cursor_job, cursor_pattern, cursor_file) else 0
            for idx, raw_line in enumerate(text.splitlines()):
                if idx < start_line:
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                for candidate, parent_key in walk_json_nodes(obj):
                    if parent_key == "usage":
                        continue
                    input_tokens, cache_tokens, output_tokens = extract_usage(candidate)
                    if input_tokens == 0 and cache_tokens == 0 and output_tokens == 0:
                        continue
                    event_time = extract_time(candidate) or fallback_time
                    item = {
                        "tool": active_tool,
                        "model": extract_model(candidate),
                        "event_time": event_time.isoformat(),
                        "input_tokens": input_tokens,
                        "cache_tokens": cache_tokens,
                        "output_tokens": output_tokens,
                        "session_fingerprint": session_fingerprint,
                        "source_ref": path + ":" + str(idx + 1),
                    }
                    events.append(item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote_file_collector.py -k "resumes_jsonl_from_line_cursor" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "feat: resume remote pagination from jsonl cursors"
```

### Task 4: Loop Locally Across Pages And Guard Against Bad Cursors

**Files:**
- Modify: `tests/test_remote_file_collector.py`
- Modify: `src/llm_usage/collectors/remote_file.py`

- [ ] **Step 1: Write the failing test**

```python
def test_remote_file_collector_collect_aggregates_multiple_pages(monkeypatch):
    first_page = remote_file._encode_chunked_stdout_payload(
        {
            "events": [
                {
                    "tool": "claude_code",
                    "model": "fake_model",
                    "event_time": "2026-03-08T00:00:00+00:00",
                    "input_tokens": 100,
                    "cache_tokens": 10,
                    "output_tokens": 5,
                    "session_fingerprint": None,
                    "source_ref": "/tmp/a.jsonl:1",
                }
            ],
            "warnings": ["page-1"],
            "next_cursor": {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 1},
        },
        chunk_size=80,
    )
    second_page = remote_file._encode_chunked_stdout_payload(
        {
            "events": [
                {
                    "tool": "claude_code",
                    "model": "fake_model",
                    "event_time": "2026-03-08T00:01:00+00:00",
                    "input_tokens": 101,
                    "cache_tokens": 10,
                    "output_tokens": 5,
                    "session_fingerprint": None,
                    "source_ref": "/tmp/a.jsonl:2",
                }
            ],
            "warnings": ["page-2"],
            "next_cursor": None,
        },
        chunk_size=80,
    )
    outputs = [first_page, second_page]

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            return _Completed(stdout=outputs.pop(0))
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert [event.input_tokens for event in out.events] == [100, 101]
    assert out.warnings == ["server_a/claude_code: page-1", "server_a/claude_code: page-2"]


def test_remote_file_collector_collect_rejects_non_advancing_cursor():
    repeated_page = remote_file._encode_chunked_stdout_payload(
        {
            "events": [],
            "warnings": [],
            "next_cursor": {"job_index": 0, "pattern_index": 0, "file_index": 0, "line_index": 0},
        },
        chunk_size=80,
    )
    calls = {"count": 0}

    def _runner(cmd, check, capture_output, text, input=None, timeout=None):  # noqa: ANN001, ANN201
        if cmd[:1] == ["ssh"] and "command -v python3" in _remote_command(cmd):
            return _Completed(stdout="python3")
        if cmd[:1] == ["ssh"] and input is not None:
            calls["count"] += 1
            return _Completed(stdout=repeated_page)
        return _Completed()

    collector = RemoteFileCollector(
        "claude_code",
        target=SshTarget(host="host", user="alice", port=22),
        patterns=["~/.claude/**/*.jsonl"],
        source_name="server_a",
        source_host_hash="hash",
        runner=_runner,
    )

    out = collector.collect(
        start=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )

    assert calls["count"] == 1
    assert out.warnings == ["server_a/claude_code: remote pagination cursor did not advance"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote_file_collector.py -k "aggregates_multiple_pages or non_advancing_cursor" -v`
Expected: FAIL because `collect()` only performs one remote call and has no cursor loop or progress guard.

- [ ] **Step 3: Write minimal implementation**

```python
def _run_python_script(
    self,
    python_cmd: str,
    script: str,
    *,
    cursor: Optional[dict[str, int]] = None,
) -> tuple[dict, Optional[str]]:
    command, script_input = self._python_stdin_command(python_cmd, script, cursor=cursor)
    completed, error = self._ssh_run_python_command(command, input_text=script_input)
    if error:
        return {}, error
    payload, discarded, chunked_error = _extract_remote_page_payload(completed.stdout)
    if chunked_error:
        self._log_non_json_debug(completed.stdout, completed.stderr)
        self._log_progress(chunked_error)
        return {}, chunked_error
    if discarded:
        for line in discarded.splitlines():
            text = line.strip()
            if text:
                self._log_progress(f"remote stdout noise: {text}")
    if payload is None:
        return {}, "remote command returned non-JSON output"
    return payload, None


def collect(self, start: datetime, end: datetime) -> CollectOutput:
    warnings: list[str] = []
    events: list[UsageEvent] = []
    self._active_start_value = start
    self._active_end_value = end
    self._log_progress("采集：查找远端 Python")
    python_cmd, error = self._discover_python()
    if error:
        return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: {error}"])
    if not python_cmd:
        return CollectOutput(events=[], warnings=[f"{self.source_name}/{self.name}: no remote python interpreter found"])
    self._log_progress(f"采集：使用远端解释器 {python_cmd}")

    cursor: Optional[dict[str, int]] = None
    seen_cursors: set[str] = set()
    while True:
        payload, error = self._run_python_script(python_cmd, _COLLECT_SCRIPT, cursor=cursor)
        if error:
            return CollectOutput(events=events, warnings=[f"{self.source_name}/{self.name}: {error}"])
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            return CollectOutput(events=events, warnings=[f"{self.source_name}/{self.name}: remote collect returned invalid payload"])
        warnings.extend(
            f"{self.source_name}/{self.name}: {warning}"
            for warning in payload.get("warnings", [])
            if isinstance(warning, str) and warning.strip()
        )
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_time = _parse_datetime_value(item.get("event_time"))
            if event_time is None:
                continue
            if start <= event_time <= end:
                events.append(
                    UsageEvent(
                        tool=str(item.get("tool") or self.name),
                        model=str(item.get("model") or "unknown"),
                        event_time=event_time,
                        input_tokens=_coerce_int(item.get("input_tokens")),
                        cache_tokens=_coerce_int(item.get("cache_tokens")),
                        output_tokens=_coerce_int(item.get("output_tokens")),
                        session_fingerprint=_optional_str(item.get("session_fingerprint")),
                        source_ref=_optional_str(item.get("source_ref")),
                        source_host_hash=self.source_host_hash,
                    )
                )
        cursor = payload.get("next_cursor")
        if cursor is None:
            break
        cursor_key = json.dumps(cursor, sort_keys=True, separators=(",", ":"))
        if cursor_key in seen_cursors:
            return CollectOutput(events=events, warnings=[f"{self.source_name}/{self.name}: remote pagination cursor did not advance"])
        seen_cursors.add(cursor_key)
    if not events:
        warnings.append(f"{self.source_name}/{self.name}: no usage events in selected time range")
    return CollectOutput(events=events, warnings=warnings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_remote_file_collector.py -k "aggregates_multiple_pages or non_advancing_cursor" -v`
Expected: PASS

Run: `pytest tests/test_remote_file_collector.py -v`
Expected: PASS

Run: `pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_remote_file_collector.py src/llm_usage/collectors/remote_file.py
git commit -m "fix: paginate remote collection by stdout budget"
```
