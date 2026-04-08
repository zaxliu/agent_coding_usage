# Terminal Host Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the terminal usage summary host-aware and dynamically aligned so users can distinguish `local` and remote hosts without losing readability.

**Architecture:** Keep `AggregateRecord` and storage contracts unchanged. Build a terminal-only `source_host_hash -> host label` map in the CLI layer, pass it into the reporting layer, and regroup terminal rows by `date + source_host_hash + tool + model`. Use a small local width-calculation helper in both Python and Node so the rendered table shape stays aligned without adding dependencies.

**Tech Stack:** Python 3.9+, Node.js 22+, `pytest`, Node built-in test runner, existing CLI/reporting modules

---

## File Structure

- Modify: `src/llm_usage/reporting.py`
  Purpose: regroup terminal rows by host, resolve display labels, compute dynamic column widths, and render the new `Host` column.
- Modify: `src/llm_usage/main.py`
  Purpose: build the host-label map from local and remote config and pass it into `print_terminal_report()`.
- Modify: `tests/test_reporting.py`
  Purpose: cover Python host-aware grouping, resolved labels, fallback labels, and dynamic-width rendering shape.
- Modify: `tests/test_main_identity.py`
  Purpose: cover the host-label map helper so terminal rendering receives readable `local` and remote labels.
- Modify: `node/src/runtime/reporting.js`
  Purpose: add host-aware regrouping, label resolution, and dynamic-width rendering in the Node terminal summary.
- Modify: `node/src/cli/main.js`
  Purpose: pass a host-label map into the Node report renderer so the interface matches Python.
- Modify: `node/test/reporting.test.js`
  Purpose: cover Node host-aware grouping, local label rendering, fallback labels, and dynamic-width table shape.

### Task 1: Lock the Python behavior with failing report tests

**Files:**
- Modify: `tests/test_reporting.py`
- Test: `tests/test_reporting.py`

- [ ] **Step 1: Replace the current grouped-report test with host-aware failing tests**

```python
def test_print_terminal_report_keeps_hosts_separate_and_renders_labels(capsys):
    rows = [
        _row(source_host_hash="local-hash", input_tokens_sum=10, cache_tokens_sum=2, output_tokens_sum=3),
        _row(source_host_hash="remote-hash", input_tokens_sum=5, cache_tokens_sum=7, output_tokens_sum=11),
    ]

    print_terminal_report(
        rows,
        host_labels={"local-hash": "local", "remote-hash": "alice@host-a"},
    )

    captured = capsys.readouterr().out.strip().splitlines()
    assert "Host" in captured[0]
    assert len(captured) == 4
    assert "local" in captured[2]
    assert "alice@host-a" in captured[3]
    assert "10" in captured[2]
    assert "5" in captured[3]


def test_print_terminal_report_uses_hash_prefix_for_unknown_hosts(capsys):
    print_terminal_report([_row(source_host_hash="abcdef1234567890")], host_labels={})

    captured = capsys.readouterr().out.strip().splitlines()
    assert "abcdef12" in captured[2]
```

- [ ] **Step 2: Add a dynamic-width shape test that fails under the current fixed-width renderer**

```python
def test_print_terminal_report_sizes_host_column_from_rendered_content(capsys):
    rows = [
        _row(source_host_hash="short-hash", tool="codex", model="gpt-5"),
        _row(source_host_hash="long-hash", tool="cursor", model="gpt-5.4-ultra-long-name"),
    ]

    print_terminal_report(
        rows,
        host_labels={
            "short-hash": "local",
            "long-hash": "very-long-host-label@example.internal",
        },
    )

    header, divider, first_row, second_row = capsys.readouterr().out.strip().splitlines()
    assert len(header.split(" | ")) == 7
    assert len(divider.split("-+-")) == 7
    assert len(first_row.split(" | ")) == 7
    assert len(second_row.split(" | ")) == 7
    assert "very-long-host-label@example.internal" in second_row
```

- [ ] **Step 3: Run the Python reporting tests to verify they fail**

Run: `pytest tests/test_reporting.py -v`
Expected: FAIL because `print_terminal_report()` does not accept `host_labels`, still merges different hosts, and does not render a `Host` column.

- [ ] **Step 4: Commit the failing-test checkpoint**

```bash
git add tests/test_reporting.py
git commit -m "test: define host-aware terminal report behavior"
```

### Task 2: Implement Python host-label mapping and dynamic table rendering

**Files:**
- Modify: `src/llm_usage/reporting.py`
- Modify: `src/llm_usage/main.py`
- Modify: `tests/test_main_identity.py`
- Test: `tests/test_reporting.py`
- Test: `tests/test_main_identity.py`

- [ ] **Step 1: Add a failing test for the host-label map helper in `tests/test_main_identity.py`**

```python
def test_build_terminal_host_labels_maps_local_and_remote_hashes(monkeypatch):
    config = main.parse_remote_configs_from_env(
        {
            "REMOTE_HOSTS": "SERVER_A",
            "REMOTE_SERVER_A_SSH_HOST": "host-a",
            "REMOTE_SERVER_A_SSH_USER": "alice",
            "REMOTE_SERVER_A_LABEL": "alice@host-a",
        }
    )[0]

    labels = main._build_terminal_host_labels(
        username="alice",
        salt="team-salt",
        remote_configs=[config],
    )

    assert labels[main.hash_source_host("alice", "local", "team-salt")] == "local"
    assert labels[main.hash_source_host("alice", "alice@host-a", "team-salt")] == "alice@host-a"
```

- [ ] **Step 2: Run the focused identity/report test subset to verify the new helper test fails**

Run: `pytest tests/test_main_identity.py::test_build_terminal_host_labels_maps_local_and_remote_hashes tests/test_reporting.py -v`
Expected: FAIL with `AttributeError` for `_build_terminal_host_labels` and existing terminal-report assertions still expecting the old output.

- [ ] **Step 3: Add the host-label map helper in `src/llm_usage/main.py` and pass it into every terminal report call**

```python
def _build_terminal_host_labels(username: str, salt: str, remote_configs: list) -> dict[str, str]:
    labels = {
        hash_source_host(username, "local", salt): "local",
    }
    for config in remote_configs:
        labels[hash_source_host(username, config.source_label, salt)] = config.source_label
    return labels


def _build_aggregates(args: argparse.Namespace) -> tuple[list, list[str], dict[str, str]]:
    # existing setup omitted for brevity
    configured_remotes = parse_remote_configs_from_env()
    host_labels = _build_terminal_host_labels(username=username, salt=salt, remote_configs=configured_remotes)
    # existing aggregation logic
    return rows, warnings, host_labels


def cmd_collect(args: argparse.Namespace) -> int:
    rows, warnings, host_labels = _build_aggregates(args)
    print_terminal_report(rows, host_labels=host_labels)


def cmd_sync(args: argparse.Namespace) -> int:
    rows, warnings, host_labels = _build_aggregates(args)
    print_terminal_report(rows, host_labels=host_labels)
```

- [ ] **Step 4: Update `src/llm_usage/reporting.py` to regroup by host, resolve labels, and calculate widths from rendered cells**

```python
def _host_label(source_host_hash: str, host_labels: dict[str, str]) -> str:
    if not source_host_hash:
        return "local"
    return host_labels.get(source_host_hash) or source_host_hash[:8]


def _group_terminal_rows(rows: list[AggregateRecord]) -> list[AggregateRecord]:
    buckets: dict[tuple[str, str, str, str], dict[str, Union[int, AggregateRecord]]] = defaultdict(...)
    for row in rows:
        key = (row.date_local, row.source_host_hash, row.tool, row.model)
        # accumulate exactly as before


def print_terminal_report(rows: list[AggregateRecord], host_labels: dict[str, str] | None = None) -> None:
    host_labels = host_labels or {}
    headers = ["日期", "Host", "工具", "模型", "输入", "缓存", "输出"]
    rendered_rows = [
        [
            row.date_local,
            _host_label(row.source_host_hash, host_labels),
            row.tool,
            row.model,
            str(row.input_tokens_sum),
            str(row.cache_tokens_sum),
            str(row.output_tokens_sum),
        ]
        for row in _group_terminal_rows(rows)
    ]
    widths = [
        max(len(headers[index]), *(len(items[index]) for items in rendered_rows))
        for index in range(len(headers))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for items in rendered_rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(items)))
```

- [ ] **Step 5: Run the focused Python tests until they pass**

Run: `pytest tests/test_reporting.py tests/test_main_identity.py -v`
Expected: PASS with separate terminal rows for `local` and `alice@host-a`, plus `abcdef12` fallback when no label exists.

- [ ] **Step 6: Commit the Python implementation**

```bash
git add src/llm_usage/reporting.py src/llm_usage/main.py tests/test_reporting.py tests/test_main_identity.py
git commit -m "feat: show host-aware terminal report in python cli"
```

### Task 3: Lock the Node behavior with failing host-aware report tests

**Files:**
- Modify: `node/test/reporting.test.js`
- Test: `node/test/reporting.test.js`

- [ ] **Step 1: Replace the current Node report test with host-aware expectations**

```javascript
test("printTerminalReport keeps host rows separate and renders host labels", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({ source_host_hash: "local-hash", input_tokens_sum: 10, cache_tokens_sum: 6, output_tokens_sum: 8 }),
        row({ source_host_hash: "remote-hash", input_tokens_sum: 5, cache_tokens_sum: 3, output_tokens_sum: 6 }),
      ],
      { hostLabels: { "local-hash": "local", "remote-hash": "alice@host-a" } },
    ),
  );

  assert.match(lines[0], /Host/u);
  assert.equal(lines.length, 4);
  assert.match(lines[2], /local/u);
  assert.match(lines[3], /alice@host-a/u);
  assert.match(lines[2], /10/u);
  assert.match(lines[3], /5/u);
});


test("printTerminalReport falls back to the first 8 hash characters", () => {
  const lines = captureLogs(() =>
    printTerminalReport([row({ source_host_hash: "abcdef1234567890" })], { hostLabels: {} }),
  );

  assert.match(lines[2], /abcdef12/u);
});
```

- [ ] **Step 2: Add a table-shape assertion for dynamic widths**

```javascript
test("printTerminalReport computes seven columns from rendered content", () => {
  const lines = captureLogs(() =>
    printTerminalReport(
      [
        row({ source_host_hash: "a", tool: "codex", model: "gpt-5" }),
        row({ source_host_hash: "b", tool: "cursor", model: "gpt-5.4-ultra-long-name" }),
      ],
      {
        hostLabels: {
          a: "local",
          b: "very-long-host-label@example.internal",
        },
      },
    ),
  );

  assert.equal(lines[0].split(" | ").length, 7);
  assert.equal(lines[1].split("-+-").length, 7);
  assert.equal(lines[3].split(" | ").length, 7);
  assert.match(lines[3], /very-long-host-label@example\.internal/u);
});
```

- [ ] **Step 3: Run the Node reporting test to verify it fails**

Run: `npm test -- test/reporting.test.js`
Expected: FAIL because `printTerminalReport()` does not accept a `hostLabels` option, still merges hosts, and still renders only six columns.

- [ ] **Step 4: Commit the failing-test checkpoint**

```bash
git add node/test/reporting.test.js
git commit -m "test: define host-aware node terminal report behavior"
```

### Task 4: Implement the Node renderer and wire it through the CLI

**Files:**
- Modify: `node/src/runtime/reporting.js`
- Modify: `node/src/cli/main.js`
- Test: `node/test/reporting.test.js`

- [ ] **Step 1: Update the Node report grouping and rendering to match Python**

```javascript
function hostLabel(sourceHostHash, hostLabels = {}) {
  if (!sourceHostHash) {
    return "local";
  }
  return hostLabels[sourceHostHash] || sourceHostHash.slice(0, 8);
}

function groupTerminalRows(rows) {
  const buckets = new Map();
  for (const row of rows) {
    const key = JSON.stringify([row.date_local, row.source_host_hash, row.tool, row.model]);
    const current = buckets.get(key) || {
      input_tokens_sum: 0,
      cache_tokens_sum: 0,
      output_tokens_sum: 0,
      sample: row,
    };
    current.input_tokens_sum += Number(row.input_tokens_sum || 0);
    current.cache_tokens_sum += Number(row.cache_tokens_sum || 0);
    current.output_tokens_sum += Number(row.output_tokens_sum || 0);
    buckets.set(key, current);
  }
  return [...buckets.values()].map((bucket) => ({
    ...bucket.sample,
    input_tokens_sum: bucket.input_tokens_sum,
    cache_tokens_sum: bucket.cache_tokens_sum,
    output_tokens_sum: bucket.output_tokens_sum,
  }));
}

export function printTerminalReport(rows, { hostLabels = {} } = {}) {
  const headers = ["日期", "Host", "工具", "模型", "输入", "缓存", "输出"];
  const tableRows = groupTerminalRows(rows).map((row) => [
    row.date_local,
    hostLabel(row.source_host_hash, hostLabels),
    row.tool,
    row.model,
    String(row.input_tokens_sum),
    String(row.cache_tokens_sum),
    String(row.output_tokens_sum),
  ]);
  const widths = headers.map((header, index) =>
    Math.max(header.length, ...tableRows.map((items) => items[index].length)),
  );
  console.log(headers.map((value, index) => value.padEnd(widths[index])).join(" | "));
  console.log(widths.map((width) => "-".repeat(width)).join("-+-"));
  for (const items of tableRows) {
    console.log(items.map((value, index) => value.padEnd(widths[index])).join(" | "));
  }
}
```

- [ ] **Step 2: Pass a host-label map from the Node CLI so the API shape matches Python**

```javascript
function buildTerminalHostLabels(rows) {
  const labels = {};
  for (const row of rows) {
    if (!row.source_host_hash) {
      continue;
    }
    labels[row.source_host_hash] = "local";
  }
  return labels;
}

async function runCollect(lookbackDays, uiMode, options) {
  const { rows, warnings } = await buildAggregates(lookbackDays, uiMode);
  printWarnings(warnings);
  printTerminalReport(rows, { hostLabels: buildTerminalHostLabels(rows) });
}

async function runSync(lookbackDays, uiMode, options) {
  const { rows, warnings } = await buildAggregates(lookbackDays, uiMode);
  printWarnings(warnings);
  printTerminalReport(rows, { hostLabels: buildTerminalHostLabels(rows) });
}
```

- [ ] **Step 3: Run the Node reporting tests until they pass**

Run: `npm test -- test/reporting.test.js`
Expected: PASS with a seven-column header, separate `local` and `alice@host-a` rows when labels are supplied by tests, and `abcdef12` fallback when they are not.

- [ ] **Step 4: Commit the Node implementation**

```bash
git add node/src/runtime/reporting.js node/src/cli/main.js node/test/reporting.test.js
git commit -m "feat: align node terminal report with host-aware table"
```

### Task 5: Run cross-runtime verification before merging

**Files:**
- Modify: none
- Test: `tests/test_reporting.py`
- Test: `tests/test_main_identity.py`
- Test: `node/test/reporting.test.js`

- [ ] **Step 1: Run the Python regression subset**

Run: `pytest tests/test_reporting.py tests/test_main_identity.py -v`
Expected: PASS

- [ ] **Step 2: Run the Node regression subset**

Run: `npm test -- test/reporting.test.js`
Expected: PASS

- [ ] **Step 3: Run one broader smoke check per runtime**

Run: `pytest tests/test_cli_help.py tests/test_reporting.py tests/test_main_identity.py -v`
Expected: PASS

Run: `npm test -- test/cli.test.js test/reporting.test.js`
Expected: PASS

- [ ] **Step 4: Record the final integration commit**

```bash
git add src/llm_usage/main.py src/llm_usage/reporting.py tests/test_main_identity.py tests/test_reporting.py node/src/cli/main.js node/src/runtime/reporting.js node/test/reporting.test.js
git commit -m "feat: make terminal usage report host-aware"
```
