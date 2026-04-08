# Feishu Bitable Multi-Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backward-compatible Feishu multi-target support, `doctor --feishu`, `init --feishu-bitable-schema`, and the associated config/README updates without changing legacy default behavior.

**Architecture:** Introduce two shared concepts in both runtimes: a Feishu target resolver and a richer Feishu schema definition. Then layer command-specific selection/execution logic on top, keeping legacy top-level env keys mapped to a synthesized `default` target. Python remains the interactive configuration surface; Node matches env parsing and command semantics for `doctor`, `init`, and `sync`.

**Tech Stack:** Python CLI (`argparse`), Node CLI (custom argv parser), `.env` parsing helpers, Feishu Bitable HTTP clients, pytest, Node test runner, README docs.

---

### Task 1: Add shared Python Feishu target and schema primitives

**Files:**
- Create: `src/llm_usage/feishu_targets.py`
- Create: `src/llm_usage/feishu_schema.py`
- Modify: `src/llm_usage/privacy.py`
- Modify: `src/llm_usage/interaction.py`
- Test: `tests/test_feishu_targets.py`
- Test: `tests/test_feishu_bitable.py`

- [ ] **Step 1: Write the failing target-resolution and schema tests**

```python
from llm_usage.feishu_schema import REQUIRED_FEISHU_FIELDS, field_names
from llm_usage.feishu_targets import (
    FeishuTargetConfig,
    resolve_feishu_targets_from_env,
    select_feishu_targets,
)


def test_resolve_feishu_targets_keeps_legacy_default_only():
    env = {
        "FEISHU_APP_TOKEN": "app-default",
        "FEISHU_TABLE_ID": "tbl-default",
        "FEISHU_APP_ID": "cli-default",
        "FEISHU_APP_SECRET": "sec-default",
    }

    targets = resolve_feishu_targets_from_env(env)

    assert [item.name for item in targets] == ["default"]
    assert targets[0].app_token == "app-default"
    assert targets[0].table_id == "tbl-default"


def test_resolve_feishu_targets_supports_named_targets_with_auth_inheritance():
    env = {
        "FEISHU_APP_TOKEN": "app-default",
        "FEISHU_APP_ID": "cli-default",
        "FEISHU_APP_SECRET": "sec-default",
        "FEISHU_TARGETS": "team_b,finance",
        "FEISHU_TEAM_B_APP_TOKEN": "app-team-b",
        "FEISHU_TEAM_B_TABLE_ID": "tbl-team-b",
        "FEISHU_FINANCE_APP_TOKEN": "app-finance",
    }

    targets = resolve_feishu_targets_from_env(env)

    assert [item.name for item in targets] == ["default", "team_b", "finance"]
    assert targets[1].app_id == "cli-default"
    assert targets[2].app_secret == "sec-default"


def test_select_feishu_targets_requires_explicit_multi_target_opt_in():
    targets = [
        FeishuTargetConfig(name="default", app_token="app-default"),
        FeishuTargetConfig(name="team_b", app_token="app-team-b"),
    ]

    selected = select_feishu_targets(targets, selected_names=[], select_all=False, default_only=True)

    assert [item.name for item in selected] == ["default"]


def test_feishu_schema_stays_in_sync_with_exported_field_names():
    assert field_names(REQUIRED_FEISHU_FIELDS) == [
        "date_local",
        "user_hash",
        "source_host_hash",
        "tool",
        "model",
        "input_tokens_sum",
        "cache_tokens_sum",
        "output_tokens_sum",
        "row_key",
        "updated_at",
    ]
```

- [ ] **Step 2: Run the Python tests and confirm they fail because the new modules do not exist yet**

Run: `pytest tests/test_feishu_targets.py tests/test_feishu_bitable.py -q`

Expected: `ModuleNotFoundError` or import failures for `llm_usage.feishu_targets` / `llm_usage.feishu_schema`.

- [ ] **Step 3: Add the Python target resolver and schema definition modules**

```python
# src/llm_usage/feishu_targets.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class FeishuTargetConfig:
    name: str
    app_token: str
    table_id: str = ""
    app_id: str = ""
    app_secret: str = ""
    bot_token: str = ""
    inherited_auth: bool = False


def normalize_feishu_target_name(raw: str) -> str:
    value = raw.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", value):
        raise RuntimeError(f"invalid feishu target name: {raw}")
    if value == "default":
        raise RuntimeError("feishu target name 'default' is reserved")
    return value


def resolve_feishu_targets_from_env(env: dict[str, str]) -> list[FeishuTargetConfig]:
    out: list[FeishuTargetConfig] = []
    if any(env.get(key, "").strip() for key in (
        "FEISHU_APP_TOKEN",
        "FEISHU_TABLE_ID",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_BOT_TOKEN",
    )):
        out.append(
            FeishuTargetConfig(
                name="default",
                app_token=env.get("FEISHU_APP_TOKEN", "").strip(),
                table_id=env.get("FEISHU_TABLE_ID", "").strip(),
                app_id=env.get("FEISHU_APP_ID", "").strip(),
                app_secret=env.get("FEISHU_APP_SECRET", "").strip(),
                bot_token=env.get("FEISHU_BOT_TOKEN", "").strip(),
            )
        )
    # parse FEISHU_TARGETS and append named targets here
    return out
```

```python
# src/llm_usage/feishu_schema.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeishuFieldSpec:
    name: str
    field_type: str
    warn_only_type_mismatch: bool = True


REQUIRED_FEISHU_FIELDS = [
    FeishuFieldSpec("date_local", "text"),
    FeishuFieldSpec("user_hash", "text"),
    FeishuFieldSpec("source_host_hash", "text"),
    FeishuFieldSpec("tool", "text"),
    FeishuFieldSpec("model", "text"),
    FeishuFieldSpec("input_tokens_sum", "number"),
    FeishuFieldSpec("cache_tokens_sum", "number"),
    FeishuFieldSpec("output_tokens_sum", "number"),
    FeishuFieldSpec("row_key", "text"),
    FeishuFieldSpec("updated_at", "datetime"),
]


def field_names(fields: list[FeishuFieldSpec]) -> list[str]:
    return [item.name for item in fields]
```

```python
# src/llm_usage/privacy.py
from .feishu_schema import REQUIRED_FEISHU_FIELDS, field_names

UPLOAD_FIELDS = set(field_names(REQUIRED_FEISHU_FIELDS))
```

- [ ] **Step 4: Extend the Feishu client tests to cover missing-field and field-completeness helpers**

```python
def test_upsert_warns_for_missing_remote_fields(monkeypatch):
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")
    monkeypatch.setattr(client, "fetch_existing_row_keys", lambda: {})
    monkeypatch.setattr(client, "_fetch_field_type_map", lambda: {"row_key": 1, "updated_at": 5})
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"data": {"records": [{"record_id": "rec-1"}]}})

    row = AggregateRecord(
        date_local="2026-03-08",
        user_hash="u",
        source_host_hash="s",
        tool="codex",
        model="m",
        input_tokens_sum=1,
        cache_tokens_sum=0,
        output_tokens_sum=1,
        row_key="key-1",
        updated_at="2026-03-08T00:00:00+00:00",
    )

    result = client.upsert([row])

    assert "飞书表缺少字段，已跳过：date_local" in result.warning_samples
```

- [ ] **Step 5: Run the focused Python tests until they pass**

Run: `pytest tests/test_feishu_targets.py tests/test_feishu_bitable.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the shared Python foundation**

```bash
git add src/llm_usage/feishu_targets.py src/llm_usage/feishu_schema.py src/llm_usage/privacy.py tests/test_feishu_targets.py tests/test_feishu_bitable.py
git commit -m "feat: add shared feishu target and schema primitives"
```

### Task 2: Wire Python `doctor`, `init`, and `sync` to Feishu targets

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `src/llm_usage/sinks/feishu_bitable.py`
- Modify: `tests/test_cli_help.py`
- Create: `tests/test_feishu_commands.py`
- Modify: `tests/test_feishu_auth.py`

- [ ] **Step 1: Write failing command-level tests for target selection and compatibility**

```python
from argparse import Namespace

import llm_usage.main as main


def test_sync_without_target_flags_uses_default_target(monkeypatch):
    selected = {}
    monkeypatch.setattr(main, "_build_aggregates", lambda args: ([], [], {}))
    monkeypatch.setattr(main, "_sync_rows_to_feishu_targets", lambda rows, target_names, select_all, dry_run: selected.update({
        "target_names": target_names,
        "select_all": select_all,
        "dry_run": dry_run,
    }) or 0)

    exit_code = main.cmd_sync(Namespace(
        from_bundle="",
        lookback_days=7,
        ui="none",
        dry_run=True,
        cursor_login_mode="auto",
        cursor_login_browser="default",
        cursor_login_user_data_dir="",
        cursor_login_timeout_sec=600,
        feishu=False,
        feishu_targets=[],
        all_feishu_targets=False,
    ))

    assert exit_code == 0
    assert selected == {"target_names": [], "select_all": False, "dry_run": True}


def test_doctor_feishu_checks_named_target(monkeypatch, capsys):
    monkeypatch.setattr(main, "run_feishu_doctor", lambda target_names, select_all: 0)
    parser = main.build_parser()

    args = parser.parse_args(["doctor", "--feishu", "--feishu-target", "team_b"])

    assert args.feishu is True
    assert args.feishu_targets == ["team_b"]
```

- [ ] **Step 2: Run the command and help tests to confirm the new flags are missing**

Run: `pytest tests/test_cli_help.py tests/test_feishu_commands.py -q`

Expected: failures for missing parser flags and missing helper functions.

- [ ] **Step 3: Add Python command parsing and target-aware execution helpers**

```python
# src/llm_usage/main.py
def _add_feishu_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--feishu-target",
        dest="feishu_targets",
        action="append",
        default=[],
        metavar="NAME",
        help="Select a named Feishu target; may be repeated",
    )
    parser.add_argument(
        "--all-feishu-targets",
        action="store_true",
        help="Select default plus all configured named Feishu targets",
    )


def _sync_rows_to_feishu_targets(
    rows: list,
    *,
    target_names: list[str],
    select_all: bool,
    dry_run: bool = False,
) -> int:
    targets = resolve_feishu_targets_from_env(dict(os.environ))
    selected = select_feishu_targets(
        targets,
        selected_names=target_names,
        select_all=select_all,
        default_only=not target_names and not select_all,
    )
    return sync_rows_to_targets(rows, selected, dry_run=dry_run)
```

```python
# src/llm_usage/sinks/feishu_bitable.py
def create_field(self, *, field_name: str, field_type: int) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields"
    self._request("POST", url, json={"field_name": field_name, "type": field_type})


def check_schema(self, required_fields: list[FeishuFieldSpec]) -> tuple[list[str], list[str]]:
    field_type_map = self._fetch_field_type_map()
    missing = [item.name for item in required_fields if item.name not in field_type_map]
    type_warnings: list[str] = []
    return missing, type_warnings
```

- [ ] **Step 4: Extend the parser help text to document the new Feishu-specific flags**

```python
doctor_parser.add_argument(
    "--feishu",
    action="store_true",
    help="Additionally validate configured Feishu target tables and schema completeness",
)
_add_feishu_target_arguments(doctor_parser)

init_parser.add_argument(
    "--feishu-bitable-schema",
    action="store_true",
    help="Create missing standard fields in the selected Feishu target tables",
)
_add_feishu_target_arguments(init_parser)

_add_feishu_target_arguments(sync_parser)
```

- [ ] **Step 5: Add focused tests for hard-error vs warning behavior**

```python
def test_run_feishu_doctor_returns_warning_exit_zero(monkeypatch, capsys):
    monkeypatch.setattr(main, "doctor_feishu_targets", lambda *args, **kwargs: [
        ("default", "WARN", "missing fields: updated_at")
    ])

    exit_code = main.run_feishu_doctor(target_names=[], select_all=False)

    assert exit_code == 0


def test_run_feishu_doctor_returns_two_on_auth_error(monkeypatch):
    monkeypatch.setattr(main, "doctor_feishu_targets", lambda *args, **kwargs: [
        ("finance", "ERROR", "auth failed")
    ])

    assert main.run_feishu_doctor(target_names=["finance"], select_all=False) == 2
```

- [ ] **Step 6: Run the Python command/help/auth test suite**

Run: `pytest tests/test_cli_help.py tests/test_feishu_auth.py tests/test_feishu_commands.py -q`

Expected: all tests pass, including help output checks for `--feishu`, `--feishu-target`, `--all-feishu-targets`, and `--feishu-bitable-schema`.

- [ ] **Step 7: Commit the Python command work**

```bash
git add src/llm_usage/main.py src/llm_usage/sinks/feishu_bitable.py tests/test_cli_help.py tests/test_feishu_auth.py tests/test_feishu_commands.py
git commit -m "feat: add python feishu doctor init and multi-target sync"
```

### Task 3: Extend Python `config` to manage Feishu targets safely

**Files:**
- Modify: `src/llm_usage/interaction.py`
- Modify: `src/llm_usage/main.py`
- Create: `tests/test_config_feishu_targets.py`
- Modify: `tests/test_cli_help.py`

- [ ] **Step 1: Write failing tests for config target CRUD and validation**

```python
from pathlib import Path

from llm_usage.interaction import ConfigDraft, run_config_editor


def test_config_editor_persists_named_feishu_targets(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FEISHU_APP_TOKEN=app-default\n", encoding="utf-8")

    stdin_text = "2\n2\na\nteam_b\napp-team-b\ntbl-team-b\n\n\n\nb\ns\n"
    exit_code = run_config_editor(env_path, stdin=io.StringIO(stdin_text), stdout=io.StringIO())

    saved = env_path.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "FEISHU_TARGETS=team_b" in saved
    assert "FEISHU_TEAM_B_APP_TOKEN=app-team-b" in saved


def test_config_rejects_reserved_default_target_name(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")

    stdin_text = "2\n2\na\ndefault\nb\nd\n"
    stdout = io.StringIO()
    run_config_editor(env_path, stdin=io.StringIO(stdin_text), stdout=stdout)

    assert "reserved" in stdout.getvalue().lower()
```

- [ ] **Step 2: Run the config-focused tests and confirm the new menu flow does not exist yet**

Run: `pytest tests/test_config_feishu_targets.py tests/test_cli_help.py -q`

Expected: failures because the Feishu submenu and non-interactive options are not implemented.

- [ ] **Step 3: Refactor `ConfigDraft` to track named Feishu targets separately from raw env values**

```python
@dataclass
class FeishuTargetDraft:
    name: str
    app_token: str
    table_id: str = ""
    app_id: str = ""
    app_secret: str = ""
    bot_token: str = ""


@dataclass
class ConfigDraft:
    document: EnvDocument
    values: dict[str, str]
    remotes: list[RemoteDraft]
    feishu_targets: list[FeishuTargetDraft]
    dirty: bool = False
```

```python
def _save_config_draft(env_path: Path, draft: ConfigDraft) -> None:
    # keep legacy FEISHU_* keys for default target editing
    # then rewrite FEISHU_TARGETS and FEISHU_<TARGET>_* blocks from draft.feishu_targets
    apply_feishu_target_drafts_to_document(draft.document, draft.feishu_targets)
    save_env_document(env_path, draft.document)
```

- [ ] **Step 4: Replace the flat Feishu key editor with a submenu**

```python
def _edit_feishu_menu(draft: ConfigDraft, stdin: TextIO, stdout: TextIO) -> None:
    while True:
        stdout.write("Feishu\n")
        stdout.write("  1. Edit default target\n")
        stdout.write("  2. Manage named targets\n")
        stdout.write("  3. Doctor current Feishu targets\n")
        stdout.write("  4. Initialize current Feishu schema\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer in {"b", ""}:
            return
        if answer == "1":
            _edit_key_menu(draft, "Feishu Default", FEISHU_KEYS, stdin=stdin, stdout=stdout)
        elif answer == "2":
            _edit_feishu_target_list(draft, stdin=stdin, stdout=stdout)
```

- [ ] **Step 5: Add optional non-interactive config shortcuts in `main.py`**

```python
config_parser.add_argument("--list-feishu-targets", action="store_true")
config_parser.add_argument("--show-feishu-target", metavar="NAME")
config_parser.add_argument("--add-feishu-target", metavar="NAME")
config_parser.add_argument("--delete-feishu-target", metavar="NAME")
config_parser.add_argument("--set-feishu-target", metavar="NAME")
config_parser.add_argument("--app-token", dest="feishu_app_token", default="")
config_parser.add_argument("--table-id", dest="feishu_table_id", default="")
```

- [ ] **Step 6: Run the config and help tests until they pass**

Run: `pytest tests/test_config_feishu_targets.py tests/test_cli_help.py -q`

Expected: all tests pass, and `config --help` mentions Feishu target management.

- [ ] **Step 7: Commit the config changes**

```bash
git add src/llm_usage/interaction.py src/llm_usage/main.py tests/test_config_feishu_targets.py tests/test_cli_help.py
git commit -m "feat: extend config with feishu target management"
```

### Task 4: Add Node Feishu target parsing, schema logic, and command parity

**Files:**
- Create: `node/src/runtime/feishu-targets.js`
- Create: `node/src/runtime/feishu-schema.js`
- Modify: `node/src/runtime/feishu.js`
- Modify: `node/src/cli/main.js`
- Create: `node/test/feishu-targets.test.js`
- Modify: `node/test/cli.test.js`

- [ ] **Step 1: Write failing Node tests for env parsing and CLI flag handling**

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import { resolveFeishuTargetsFromEnv } from "../src/runtime/feishu-targets.js";

test("resolveFeishuTargetsFromEnv keeps legacy default target", () => {
  const targets = resolveFeishuTargetsFromEnv({
    FEISHU_APP_TOKEN: "app-default",
    FEISHU_TABLE_ID: "tbl-default",
    FEISHU_APP_ID: "cli-default",
    FEISHU_APP_SECRET: "sec-default",
  });

  assert.deepEqual(targets.map((item) => item.name), ["default"]);
});

test("resolveFeishuTargetsFromEnv supports named targets", () => {
  const targets = resolveFeishuTargetsFromEnv({
    FEISHU_APP_TOKEN: "app-default",
    FEISHU_APP_ID: "cli-default",
    FEISHU_APP_SECRET: "sec-default",
    FEISHU_TARGETS: "team_b",
    FEISHU_TEAM_B_APP_TOKEN: "app-team-b",
  });

  assert.deepEqual(targets.map((item) => item.name), ["default", "team_b"]);
  assert.equal(targets[1].appId, "cli-default");
});
```

- [ ] **Step 2: Run the Node tests and confirm they fail before implementation**

Run: `cd node && npm test -- --test-name-pattern="Feishu|sync help shows from-bundle support"`

Expected: import or assertion failures because `feishu-targets.js` and the new flags do not exist.

- [ ] **Step 3: Add the Node runtime target resolver and schema module**

```javascript
// node/src/runtime/feishu-targets.js
export function normalizeFeishuTargetName(raw) {
  const value = String(raw || "").trim().toLowerCase();
  if (!/^[a-z0-9_]+$/u.test(value)) {
    throw new Error(`invalid feishu target name: ${raw}`);
  }
  if (value === "default") {
    throw new Error("feishu target name 'default' is reserved");
  }
  return value;
}

export function resolveFeishuTargetsFromEnv(env = process.env) {
  const out = [];
  if (["FEISHU_APP_TOKEN", "FEISHU_TABLE_ID", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BOT_TOKEN"].some((key) =>
    String(env[key] || "").trim(),
  )) {
    out.push({
      name: "default",
      appToken: String(env.FEISHU_APP_TOKEN || "").trim(),
      tableId: String(env.FEISHU_TABLE_ID || "").trim(),
      appId: String(env.FEISHU_APP_ID || "").trim(),
      appSecret: String(env.FEISHU_APP_SECRET || "").trim(),
      botToken: String(env.FEISHU_BOT_TOKEN || "").trim(),
    });
  }
  return out;
}
```

```javascript
// node/src/runtime/feishu-schema.js
export const REQUIRED_FEISHU_FIELDS = [
  { name: "date_local", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "user_hash", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "source_host_hash", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "tool", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "model", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "input_tokens_sum", fieldType: "number", warnOnlyTypeMismatch: true },
  { name: "cache_tokens_sum", fieldType: "number", warnOnlyTypeMismatch: true },
  { name: "output_tokens_sum", fieldType: "number", warnOnlyTypeMismatch: true },
  { name: "row_key", fieldType: "text", warnOnlyTypeMismatch: true },
  { name: "updated_at", fieldType: "datetime", warnOnlyTypeMismatch: true },
];
```

- [ ] **Step 4: Extend Node CLI argument parsing and execution**

```javascript
if (value === "--feishu") {
  options.feishu = true;
  continue;
}
if (value === "--feishu-target") {
  options.feishuTargets.push(argv[index + 1] || "");
  index += 1;
  continue;
}
if (value === "--all-feishu-targets") {
  options.allFeishuTargets = true;
  continue;
}
if (value === "--feishu-bitable-schema") {
  options.feishuBitableSchema = true;
  continue;
}
```

```javascript
async function syncRowsToFeishuTargets(rows, options) {
  const targets = resolveFeishuTargetsFromEnv(process.env);
  const selected = selectFeishuTargets(targets, {
    names: options.feishuTargets,
    all: options.allFeishuTargets,
    defaultOnly: !options.feishuTargets.length && !options.allFeishuTargets,
  });
  // iterate sequentially and print per-target summaries
}
```

- [ ] **Step 5: Add Node-side schema inspection and field creation helpers**

```javascript
async fetchFieldTypeMap() {
  // keep existing method
}

async createField({ fieldName, type }) {
  const url = `https://open.feishu.cn/open-apis/bitable/v1/apps/${this.appToken}/tables/${this.tableId}/fields`;
  await requestJson("POST", url, {
    token: this.botToken,
    body: { field_name: fieldName, type },
    timeoutMs: this.timeoutMs,
  });
}
```

- [ ] **Step 6: Run the Node test suite for the affected CLI/runtime files**

Run: `cd node && npm test`

Expected: all existing tests pass plus the new Feishu target tests.

- [ ] **Step 7: Commit the Node parity work**

```bash
git add node/src/runtime/feishu-targets.js node/src/runtime/feishu-schema.js node/src/runtime/feishu.js node/src/cli/main.js node/test/feishu-targets.test.js node/test/cli.test.js
git commit -m "feat: add node feishu multi-target command parity"
```

### Task 5: Update bootstrap env templates and README

**Files:**
- Modify: `.env.example`
- Modify: `src/llm_usage/resources/bootstrap.env`
- Modify: `node/resources/bootstrap.env`
- Modify: `README.md`
- Test: `tests/test_cli_help.py`
- Test: `node/test/cli.test.js`

- [ ] **Step 1: Update env templates to advertise the new multi-target syntax without breaking old examples**

```dotenv
# Feishu Bitable
FEISHU_APP_TOKEN=
# Optional: if empty, sync/doctor/init use named target selection or skip default target behavior.
FEISHU_TABLE_ID=
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BOT_TOKEN=

# Optional named targets. Example: team_b,finance
FEISHU_TARGETS=
# FEISHU_TEAM_B_APP_TOKEN=
# FEISHU_TEAM_B_TABLE_ID=
# FEISHU_TEAM_B_APP_ID=
# FEISHU_TEAM_B_APP_SECRET=
# FEISHU_TEAM_B_BOT_TOKEN=
```

- [ ] **Step 2: Rewrite the README Feishu section around compatibility-first subsections**

```md
## 飞书多维表格同步

兼容性说明：

- 旧版单目标 `.env` 配置无需修改即可继续使用
- 不带新的目标选择参数时，`sync` 仍只上传到默认目标

### 单目标配置（兼容旧版）

```dotenv
FEISHU_APP_TOKEN=app_default
FEISHU_TABLE_ID=tbl_default
FEISHU_APP_ID=cli_default
FEISHU_APP_SECRET=sec_default
```

### 多目标配置

```dotenv
FEISHU_APP_TOKEN=app_default
FEISHU_TABLE_ID=tbl_default
FEISHU_APP_ID=cli_default
FEISHU_APP_SECRET=sec_default

FEISHU_TARGETS=team_b,finance

FEISHU_TEAM_B_APP_TOKEN=app_team_b
FEISHU_TEAM_B_TABLE_ID=tbl_team_b

FEISHU_FINANCE_APP_TOKEN=app_finance
FEISHU_FINANCE_TABLE_ID=
```
```

- [ ] **Step 3: Add explicit command examples for doctor/init/sync**

```md
### 检查目标表结构

```bash
llm-usage doctor --feishu
llm-usage doctor --feishu --feishu-target team_b
llm-usage doctor --feishu --all-feishu-targets
```

### 初始化目标表结构

```bash
llm-usage init --feishu-bitable-schema --dry-run
llm-usage init --feishu-bitable-schema --feishu-target finance
llm-usage init --feishu-bitable-schema --all-feishu-targets
```

### 同步到一个或多个目标表

```bash
llm-usage sync
llm-usage sync --feishu-target team_b
llm-usage sync --feishu-target team_b --feishu-target finance
llm-usage sync --all-feishu-targets
```
```

- [ ] **Step 4: Add a compact field reference table to README**

```md
| 字段名 | 作用 | 推荐类型 | 说明 |
| --- | --- | --- | --- |
| `date_local` | 聚合日期 | 文本 | 保持与现有导出兼容 |
| `updated_at` | 最近更新时间 | 日期时间 | sync 会按毫秒时间戳归一化 |
```

- [ ] **Step 5: Run help-text tests to ensure docs and parser examples stay aligned**

Run: `pytest tests/test_cli_help.py -q && cd node && npm test -- --test-name-pattern="help"`

Expected: both Python and Node help tests pass.

- [ ] **Step 6: Commit the docs and template changes**

```bash
git add .env.example src/llm_usage/resources/bootstrap.env node/resources/bootstrap.env README.md tests/test_cli_help.py node/test/cli.test.js
git commit -m "docs: document feishu multi-target and schema workflows"
```

### Task 6: Run compatibility and regression verification

**Files:**
- Modify: `tests/test_main_identity.py`
- Modify: `tests/test_import_config.py`
- Modify: `node/test/cli.test.js`
- Test: `tests/test_feishu_targets.py`
- Test: `tests/test_feishu_commands.py`
- Test: `node/test/feishu-targets.test.js`

- [ ] **Step 1: Add regression tests that lock legacy behavior**

```python
def test_legacy_env_plain_sync_still_uses_single_default_target(monkeypatch):
    env = {
        "FEISHU_APP_TOKEN": "app-default",
        "FEISHU_TABLE_ID": "tbl-default",
        "FEISHU_APP_ID": "cli-default",
        "FEISHU_APP_SECRET": "sec-default",
    }

    targets = resolve_feishu_targets_from_env(env)
    selected = select_feishu_targets(targets, selected_names=[], select_all=False, default_only=True)

    assert [item.name for item in selected] == ["default"]
```

```javascript
test("plain sync does not fan out when FEISHU_TARGETS is present", () => {
  const { result } = runCli(["sync", "--dry-run"], {
    FEISHU_APP_TOKEN: "app-default",
    FEISHU_APP_ID: "cli-default",
    FEISHU_APP_SECRET: "sec-default",
    FEISHU_TARGETS: "team_b",
    FEISHU_TEAM_B_APP_TOKEN: "app-team-b",
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.doesNotMatch(result.stdout, /team_b/u);
});
```

- [ ] **Step 2: Run the full Python test suite**

Run: `pytest`

Expected: full Python suite passes with no regressions in legacy help, import-config, remote handling, or Feishu upload behavior.

- [ ] **Step 3: Run the full Node test suite**

Run: `cd node && npm test`

Expected: full Node suite passes, including old CLI behavior and new Feishu target coverage.

- [ ] **Step 4: Run one manual smoke check against a temporary env file**

Run: `python -m llm_usage.main doctor --help`

Expected: help output includes `--feishu`, `--feishu-target`, and `--all-feishu-targets`.

Run: `python -m llm_usage.main init --help`

Expected: help output includes `--feishu-bitable-schema`.

Run: `node bin/llm-usage-node.js sync --help`

Expected: help output includes `--feishu-target` and `--all-feishu-targets`.

- [ ] **Step 5: Commit the compatibility regression coverage**

```bash
git add tests/test_main_identity.py tests/test_import_config.py tests/test_feishu_targets.py tests/test_feishu_commands.py node/test/cli.test.js node/test/feishu-targets.test.js
git commit -m "test: lock legacy feishu compatibility behavior"
```

## Self-Review Checklist

- Spec coverage:
  - multi-target `.env` parsing: Task 1 and Task 4
  - `doctor --feishu`: Task 2 and Task 4
  - `init --feishu-bitable-schema`: Task 2 and Task 4
  - `sync` explicit multi-target fan-out: Task 2 and Task 4
  - Python `config` changes: Task 3
  - bootstrap env + README changes: Task 5
  - compatibility regressions: Task 6

- Placeholder scan:
  - no `TODO` / `TBD`
  - every task includes exact files, concrete commands, and representative code snippets

- Type consistency:
  - Python uses `FeishuTargetConfig` / `FeishuFieldSpec`
  - Node uses `resolveFeishuTargetsFromEnv` / `REQUIRED_FEISHU_FIELDS`
  - both runtimes preserve the `default` target name and explicit `--feishu-target` / `--all-feishu-targets` semantics

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-01-feishu-bitable-multi-target.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
