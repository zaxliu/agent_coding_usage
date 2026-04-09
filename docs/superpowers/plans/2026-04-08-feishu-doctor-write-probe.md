# Feishu Doctor Write Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `feishu doctor` verify real Feishu Bitable writeability by creating and immediately deleting a minimal probe record.

**Architecture:** Add a focused write-probe method to the Python Feishu client layer, then call it from `run_feishu_doctor` after field checks succeed. Cover the behavior with Python tests for success, create failure, delete failure, and the removal of `link_share_entity` as a writeability verdict.

**Tech Stack:** Python CLI/runtime in `src/llm_usage/`, pytest in `tests/`

---

### Task 1: Lock in Doctor Behavior with Failing Tests

**Files:**
- Modify: `tests/test_feishu_commands.py`
- Test: `tests/test_feishu_commands.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_run_feishu_doctor_reports_write_probe_cleanup_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])

    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            raise RuntimeError("feishu doctor cleanup failed: rec_123")

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))


def test_run_feishu_doctor_uses_write_probe_instead_of_link_share_warning(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])

    called = {"probe": 0}

    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            called["probe"] += 1
            return "rec_123"

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)
    monkeypatch.setattr(main, "fetch_bitable_link_share_entity", lambda app_token, bot_token: "closed")

    rc = main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))

    assert rc == 0
    assert called["probe"] == 1
    output = capsys.readouterr().out
    assert "tenant_editable" not in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_feishu_commands.py -k "write_probe or link_share_mode" -v`
Expected: FAIL because `run_feishu_doctor` does not call `FeishuBitableClient.probe_write_access()` yet

- [ ] **Step 3: Write minimal implementation**

```python
client = FeishuBitableClient(
    app_token=target.app_token.strip(),
    table_id=table_id,
    bot_token=bot_token,
)
client.probe_write_access()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_feishu_commands.py -k "write_probe or link_share_mode" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_feishu_commands.py src/llm_usage/main.py
git commit -m "test: cover feishu doctor write probe flow"
```

### Task 2: Add Feishu Client Write-Probe Support

**Files:**
- Modify: `src/llm_usage/sinks/feishu_bitable.py`
- Modify: `tests/test_feishu_auth.py`
- Modify: `tests/test_feishu_bitable.py`
- Test: `tests/test_feishu_auth.py`
- Test: `tests/test_feishu_bitable.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_fetch_bitable_link_share_entity_success(monkeypatch):
    def _fake_get(url, headers, params, timeout):  # noqa: ANN001, ANN201
        return _Resp({"code": 0, "data": {"permission_public": {"link_share_entity": "closed"}}})

    monkeypatch.setattr(feishu_bitable.requests, "get", _fake_get)
    assert feishu_bitable.fetch_bitable_link_share_entity("app", "token") == "closed"
```

```python
def test_probe_write_access_creates_and_deletes_record(monkeypatch):
    calls = []

    def _fake_request(self, method, url, **kwargs):  # noqa: ANN001, ANN201
        calls.append((method, url, kwargs))
        if url.endswith("/batch_create"):
            return {"data": {"records": [{"record_id": "rec_probe"}]}}
        if url.endswith("/batch_delete"):
            return {"data": {"records": [{"record_id": "rec_probe"}]}}
        raise AssertionError(url)

    monkeypatch.setattr(feishu_bitable.FeishuBitableClient, "_request", _fake_request)
    client = feishu_bitable.FeishuBitableClient("app", "tbl", "bot")

    record_id = client.probe_write_access()

    assert record_id == "rec_probe"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/batch_create")
    assert calls[1][0] == "POST"
    assert calls[1][1].endswith("/batch_delete")
```

```python
def test_probe_write_access_raises_when_delete_fails(monkeypatch):
    def _fake_request(self, method, url, **kwargs):  # noqa: ANN001, ANN201
        if url.endswith("/batch_create"):
            return {"data": {"records": [{"record_id": "rec_probe"}]}}
        if url.endswith("/batch_delete"):
            raise RuntimeError("delete forbidden")
        raise AssertionError(url)

    monkeypatch.setattr(feishu_bitable.FeishuBitableClient, "_request", _fake_request)
    client = feishu_bitable.FeishuBitableClient("app", "tbl", "bot")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        client.probe_write_access()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_feishu_auth.py tests/test_feishu_bitable.py -k "link_share_entity or probe_write_access" -v`
Expected: FAIL because `probe_write_access` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
def probe_write_access(self) -> str:
    probe_key = f"__llm_usage_doctor_probe__{int(time.time() * 1000)}"
    payload = {
        "records": [
            {
                "fields": {
                    "row_key": probe_key,
                    "date_local": "1970-01-01",
                    "tool": "__doctor_probe__",
                    "model": "__doctor_probe__",
                    "input_tokens_sum": 0,
                    "cache_tokens_sum": 0,
                    "output_tokens_sum": 0,
                }
            }
        ]
    }
    created = self._request("POST", f"{self.base_url}/batch_create", json=payload)
    record_id = created.get("data", {}).get("records", [{}])[0].get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise RuntimeError("feishu doctor write probe did not return record_id")
    try:
        self._request("POST", f"{self.base_url}/batch_delete", json={"records": [record_id]})
    except Exception as exc:
        raise RuntimeError(f"feishu doctor cleanup failed: {record_id}: {exc}") from exc
    return record_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_feishu_auth.py tests/test_feishu_bitable.py -k "link_share_entity or probe_write_access" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/sinks/feishu_bitable.py tests/test_feishu_auth.py tests/test_feishu_bitable.py
git commit -m "feat: add feishu doctor write probe"
```

### Task 3: Wire the Write Probe into `run_feishu_doctor`

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `tests/test_feishu_commands.py`
- Test: `tests/test_feishu_commands.py`

- [ ] **Step 1: Write the failing test**

```python
def test_run_feishu_doctor_reports_write_probe_create_failure(monkeypatch):
    monkeypatch.setattr(
        main,
        "_resolve_feishu_sync_selection",
        lambda args: [
            main.FeishuTargetConfig(name="team_a", app_token="app", table_id="tbl", bot_token="bot"),
        ],
    )
    monkeypatch.setattr(main, "fetch_bitable_field_type_map", lambda app_token, table_id, bot_token: {})
    monkeypatch.setattr(main, "feishu_schema_warnings", lambda field_map: [])

    class _Client:
        def __init__(self, app_token, table_id, bot_token, request_timeout_sec=20):  # noqa: ANN001
            pass

        def probe_write_access(self):  # noqa: ANN201
            raise RuntimeError("create forbidden")

    monkeypatch.setattr(main, "FeishuBitableClient", _Client)

    with pytest.raises(RuntimeError, match="team_a: create forbidden"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=["team_a"], all_feishu_targets=False))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_feishu_commands.py -k "write_probe" -v`
Expected: FAIL because `run_feishu_doctor` does not yet wrap write-probe failures with target context

- [ ] **Step 3: Write minimal implementation**

```python
client = FeishuBitableClient(
    app_token=target.app_token.strip(),
    table_id=table_id,
    bot_token=bot_token,
)
try:
    client.probe_write_access()
except RuntimeError as exc:
    raise RuntimeError(f"target {target.name}: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_feishu_commands.py -k "write_probe" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/main.py tests/test_feishu_commands.py
git commit -m "fix: use real write probe in feishu doctor"
```

### Task 4: Run the Relevant Test Suite

**Files:**
- Verify: `src/llm_usage/main.py`
- Verify: `src/llm_usage/sinks/feishu_bitable.py`
- Verify: `tests/test_feishu_commands.py`
- Verify: `tests/test_feishu_auth.py`
- Verify: `tests/test_feishu_bitable.py`

- [ ] **Step 1: Run the focused Python tests**

Run: `pytest tests/test_feishu_commands.py tests/test_feishu_auth.py tests/test_feishu_bitable.py -v`
Expected: PASS

- [ ] **Step 2: Summarize any remaining manual verification gap**

Run: no command
Expected: note that real Feishu API manual verification still depends on user credentials and live target access
