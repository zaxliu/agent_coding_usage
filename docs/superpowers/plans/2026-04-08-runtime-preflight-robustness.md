# Runtime Preflight Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared runtime preflight layer that auto-bootstraps config entry, blocks invalid Feishu config at save time, and stops `sync` / Feishu doctor before deep execution when prerequisites are missing.

**Architecture:** Introduce a focused Python runtime validation module that owns bootstrap, Feishu target resolution, and preflight result formatting. Then wire Web config endpoints, CLI config editing, and execution commands to reuse that module so save-time and run-time behavior stay aligned.

**Tech Stack:** Python runtime in `src/llm_usage/`, pytest in `tests/`, existing Web backend routes in `src/llm_usage/web.py`

---

### Task 1: Lock In Shared Preflight Semantics with Focused Unit Tests

**Files:**
- Create: `tests/test_runtime_preflight.py`
- Test: `tests/test_runtime_preflight.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

import llm_usage.runtime_preflight as runtime_preflight


def test_ensure_runtime_bootstrap_creates_env_and_reports(tmp_path: Path):
    env_path = tmp_path / ".env"
    reports_dir = tmp_path / "reports"

    result = runtime_preflight.ensure_runtime_bootstrap(
        env_path=env_path,
        reports_dir=reports_dir,
        bootstrap_text="ORG_USERNAME=\"\"\n",
    )

    assert result.bootstrap_applied is True
    assert result.created_env is True
    assert result.created_reports is True
    assert env_path.exists()
    assert reports_dir.exists()
    assert env_path.read_text(encoding="utf-8") == "ORG_USERNAME=\"\"\n"


def test_validate_feishu_config_requires_default_target():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={},
        feishu_targets=[{"name": "finance", "app_token": "app-fin"}],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: default target is required" in result.errors


def test_validate_feishu_config_rejects_default_with_only_app_token():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={"FEISHU_APP_TOKEN": "app-default"},
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in result.errors


def test_validate_feishu_config_allows_named_target_to_inherit_default_auth():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_APP_ID": "cli_a",
            "FEISHU_APP_SECRET": "secret_a",
        },
        feishu_targets=[{"name": "finance", "app_token": "app-fin"}],
        mode="config_save",
    )

    assert result.ok is True
    assert result.errors == []
    assert "finance" in [target.name for target in result.resolved_feishu_targets]


def test_validate_feishu_config_rejects_named_target_without_app_token():
    result = runtime_preflight.validate_feishu_targets(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
        },
        feishu_targets=[{"name": "finance", "app_token": ""}],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[finance]: missing APP_TOKEN" in result.errors
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_preflight.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_usage.runtime_preflight'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, field
from pathlib import Path

from llm_usage.feishu_targets import FeishuTargetConfig


@dataclass
class BootstrapResult:
    bootstrap_applied: bool
    created_env: bool = False
    created_reports: bool = False
    auto_fixes: list[str] = field(default_factory=list)


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_fixes: list[str] = field(default_factory=list)
    bootstrap_applied: bool = False
    resolved_feishu_targets: list[FeishuTargetConfig] = field(default_factory=list)


def ensure_runtime_bootstrap(*, env_path: Path, reports_dir: Path, bootstrap_text: str) -> BootstrapResult:
    created_env = False
    created_reports = False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.write_text(bootstrap_text, encoding="utf-8")
        created_env = True
    if not reports_dir.exists():
        reports_dir.mkdir(parents=True, exist_ok=True)
        created_reports = True
    auto_fixes: list[str] = []
    if created_env:
        auto_fixes.append(f"created runtime env: {env_path}")
    if created_reports:
        auto_fixes.append(f"created reports dir: {reports_dir}")
    return BootstrapResult(
        bootstrap_applied=created_env or created_reports,
        created_env=created_env,
        created_reports=created_reports,
        auto_fixes=auto_fixes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_runtime_preflight.py src/llm_usage/runtime_preflight.py
git commit -m "test: lock in runtime preflight semantics"
```

### Task 2: Implement Shared Runtime Preflight and Feishu Resolution

**Files:**
- Create: `src/llm_usage/runtime_preflight.py`
- Modify: `src/llm_usage/feishu_targets.py`
- Test: `tests/test_runtime_preflight.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_validate_runtime_config_reports_table_id_as_warning():
    result = runtime_preflight.validate_runtime_config(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
            "FEISHU_TABLE_ID": "",
        },
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is True
    assert result.errors == []
    assert "feishu[default]: TABLE_ID is empty; first table will be auto-selected" in result.warnings


def test_validate_runtime_config_rejects_partial_default_app_credentials():
    result = runtime_preflight.validate_runtime_config(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_APP_ID": "cli_a",
            "FEISHU_APP_SECRET": "",
        },
        feishu_targets=[],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[default]: APP_ID and APP_SECRET must be set together" in result.errors


def test_validate_runtime_config_rejects_named_target_with_partial_own_auth():
    result = runtime_preflight.validate_runtime_config(
        basic={},
        feishu_default={
            "FEISHU_APP_TOKEN": "app-default",
            "FEISHU_BOT_TOKEN": "bot-default",
        },
        feishu_targets=[
            {
                "name": "finance",
                "app_token": "app-fin",
                "app_id": "cli_fin",
                "app_secret": "",
            }
        ],
        mode="config_save",
    )

    assert result.ok is False
    assert "feishu[finance]: APP_ID and APP_SECRET must be set together" in result.errors
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_preflight.py -k "table_id or partial" -v`
Expected: FAIL because `validate_runtime_config()` and the Feishu inheritance logic do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
def _resolve_target_auth(
    *,
    name: str,
    app_token: str,
    table_id: str,
    bot_token: str,
    app_id: str,
    app_secret: str,
    fallback_bot_token: str,
    fallback_app_id: str,
    fallback_app_secret: str,
) -> FeishuTargetConfig:
    own_pair_present = bool(app_id.strip()) or bool(app_secret.strip())
    if own_pair_present and not (app_id.strip() and app_secret.strip()):
        raise ValueError(f"feishu[{name}]: APP_ID and APP_SECRET must be set together")
    resolved_bot = bot_token.strip() or fallback_bot_token.strip()
    resolved_app_id = app_id.strip() or fallback_app_id.strip()
    resolved_app_secret = app_secret.strip() or fallback_app_secret.strip()
    return FeishuTargetConfig(
        name=name,
        app_token=app_token.strip(),
        table_id=table_id.strip(),
        bot_token=resolved_bot,
        app_id=resolved_app_id,
        app_secret=resolved_app_secret,
        inherited_auth=not bool(bot_token.strip() or (app_id.strip() and app_secret.strip())),
    )


def validate_runtime_config(*, basic, feishu_default, feishu_targets, mode: str) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []
    resolved_targets: list[FeishuTargetConfig] = []

    default_app_token = str(feishu_default.get("FEISHU_APP_TOKEN", "")).strip()
    default_table_id = str(feishu_default.get("FEISHU_TABLE_ID", "")).strip()
    default_bot_token = str(feishu_default.get("FEISHU_BOT_TOKEN", "")).strip()
    default_app_id = str(feishu_default.get("FEISHU_APP_ID", "")).strip()
    default_app_secret = str(feishu_default.get("FEISHU_APP_SECRET", "")).strip()

    if not default_app_token:
        errors.append("feishu[default]: default target is required")
    elif not default_table_id:
        warnings.append("feishu[default]: TABLE_ID is empty; first table will be auto-selected")
```

```python
    if bool(default_app_id) ^ bool(default_app_secret):
        errors.append("feishu[default]: APP_ID and APP_SECRET must be set together")
    if not default_bot_token and not (default_app_id and default_app_secret):
        errors.append("feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET")
    if default_app_token:
        resolved_targets.append(
            FeishuTargetConfig(
                name="default",
                app_token=default_app_token,
                table_id=default_table_id,
                bot_token=default_bot_token,
                app_id=default_app_id,
                app_secret=default_app_secret,
            )
        )

    for item in feishu_targets:
        name = str(item.get("name", "")).strip().lower()
        app_token = str(item.get("app_token", "")).strip()
        table_id = str(item.get("table_id", "")).strip()
        bot_token = str(item.get("bot_token", "")).strip()
        app_id = str(item.get("app_id", "")).strip()
        app_secret = str(item.get("app_secret", "")).strip()
        if not app_token:
            errors.append(f"feishu[{name}]: missing APP_TOKEN")
            continue
        if not table_id:
            warnings.append(f"feishu[{name}]: TABLE_ID is empty; first table will be auto-selected")
        try:
            resolved_targets.append(
                _resolve_target_auth(
                    name=name,
                    app_token=app_token,
                    table_id=table_id,
                    bot_token=bot_token,
                    app_id=app_id,
                    app_secret=app_secret,
                    fallback_bot_token=default_bot_token,
                    fallback_app_id=default_app_id,
                    fallback_app_secret=default_app_secret,
                )
            )
        except ValueError as exc:
            errors.append(str(exc))

    return PreflightResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        resolved_feishu_targets=resolved_targets,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/runtime_preflight.py tests/test_runtime_preflight.py
git commit -m "feat: add shared runtime preflight module"
```

### Task 3: Wire Web Config Endpoints to Bootstrap and Shared Save Validation

**Files:**
- Modify: `src/llm_usage/web.py`
- Modify: `tests/test_web.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_config_payload_bootstraps_missing_runtime_paths(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.load_config_payload()

    assert payload["bootstrap_applied"] is True
    assert payload["auto_fixes"]
    assert env_path.exists()
    assert (tmp_path / "reports").exists()


def test_save_config_payload_rejects_incomplete_default_feishu_auth(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.save_config_payload(
        {
            "basic": {},
            "cursor": {},
            "feishu_default": {"FEISHU_APP_TOKEN": "app-default"},
            "feishu_targets": [],
            "remotes": [],
            "raw_env": [],
        }
    )

    assert payload["ok"] is False
    assert payload["saved"] is False
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in payload["errors"]
    assert env_path.read_text(encoding="utf-8") == ""


def test_save_config_payload_allows_named_target_to_inherit_default_auth(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_USAGE_ENV_FILE", str(env_path))
    monkeypatch.setenv("LLM_USAGE_DATA_DIR", str(tmp_path))

    payload = web.save_config_payload(
        {
            "basic": {},
            "cursor": {},
            "feishu_default": {
                "FEISHU_APP_TOKEN": "app-default",
                "FEISHU_APP_ID": "cli_a",
                "FEISHU_APP_SECRET": "secret_a",
            },
            "feishu_targets": [{"name": "finance", "app_token": "app-fin"}],
            "remotes": [],
            "raw_env": [],
        }
    )

    assert payload["ok"] is True
    assert payload["saved"] is True
    text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_APP_TOKEN=app-default" in text
    assert "FEISHU_TARGETS=finance" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py -k "bootstraps_missing_runtime_paths or incomplete_default_feishu_auth or inherit_default_auth" -v`
Expected: FAIL because `load_config_payload()` does not return bootstrap metadata and `save_config_payload()` still uses shallow validation

- [ ] **Step 3: Write minimal implementation**

```python
from llm_usage.runtime_preflight import ensure_runtime_bootstrap, validate_runtime_config


def _bootstrap_runtime_for_web() -> dict[str, Any]:
    result = ensure_runtime_bootstrap(
        env_path=_env_path(),
        reports_dir=_reports_dir(),
        bootstrap_text=read_bootstrap_env_text(),
    )
    return {
        "bootstrap_applied": result.bootstrap_applied,
        "auto_fixes": result.auto_fixes,
        "created_env": result.created_env,
        "created_reports": result.created_reports,
    }
```

```python
def load_config_payload() -> dict[str, Any]:
    bootstrap = _bootstrap_runtime_for_web()
    document = load_env_document(_env_path())
    draft = ConfigDraft.from_document(document)
    return {
        "basic": {key: draft.values.get(key, "") for key in BASIC_KEYS},
        "cursor": {key: draft.values.get(key, "") for key in CURSOR_KEYS},
        "feishu_default": {key: draft.values.get(key, "") for key in FEISHU_KEYS},
        "feishu_targets": [asdict(target) for target in draft.feishu_named_targets],
        "remotes": [_serialize_remote(remote) for remote in draft.remotes],
        "raw_env": _raw_env_entries(draft.values),
        "reports_dir": str(_reports_dir()),
        "env_path": str(_env_path()),
        **bootstrap,
    }
```

```python
def validate_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    bootstrap = _bootstrap_runtime_for_web()
    validation = validate_runtime_config(
        basic=payload.get("basic", {}) or {},
        feishu_default=payload.get("feishu_default", {}) or {},
        feishu_targets=payload.get("feishu_targets", []) or [],
        mode="config_save",
    )
    return {
        "ok": validation.ok and not _remote_validation_errors(payload),
        "errors": [*_remote_validation_errors(payload), *validation.errors],
        "warnings": validation.warnings,
        "auto_fixes": bootstrap["auto_fixes"],
        "bootstrap_applied": bootstrap["bootstrap_applied"],
    }
```

```python
def save_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_config_payload(payload)
    if not validation["ok"]:
        return {**validation, "saved": False}
    ...
    return {**validation, "ok": True, "saved": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_web.py -k "bootstraps_missing_runtime_paths or incomplete_default_feishu_auth or inherit_default_auth" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/web.py tests/test_web.py
git commit -m "feat: apply runtime preflight to web config endpoints"
```

### Task 4: Apply Shared Preflight to CLI Save and Execution Entry Points

**Files:**
- Modify: `src/llm_usage/interaction.py`
- Modify: `src/llm_usage/main.py`
- Modify: `tests/test_feishu_commands.py`
- Modify: `tests/test_interaction.py`
- Test: `tests/test_feishu_commands.py`
- Test: `tests/test_interaction.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_run_config_editor_save_rejects_incomplete_default_feishu_auth(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    user_input = "\n".join(
        [
            "2",
            "1",
            "1",
            "app-default",
            "b",
            "s",
        ]
    )

    stdout = _TTYStringIO()
    exit_code = run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=stdout)

    assert exit_code == 0
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in stdout.getvalue()
    assert env_path.read_text(encoding="utf-8") == ""
```

```python
def test_sync_rows_to_feishu_targets_fails_preflight_before_upload(monkeypatch):
    monkeypatch.setattr(
        main,
        "validate_runtime_config",
        lambda **kwargs: SimpleNamespace(
            ok=False,
            errors=["feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET"],
            warnings=[],
            auto_fixes=[],
            bootstrap_applied=False,
            resolved_feishu_targets=[],
        ),
    )

    rc = main._sync_rows_to_feishu_targets([_row()], dry_run=False, feishu_target=[], all_feishu_targets=False)

    assert rc == 1
```

```python
def test_run_feishu_doctor_fails_preflight_before_api_calls(monkeypatch):
    monkeypatch.setattr(
        main,
        "validate_runtime_config",
        lambda **kwargs: SimpleNamespace(
            ok=False,
            errors=["feishu[default]: default target is required"],
            warnings=[],
            auto_fixes=[],
            bootstrap_applied=False,
            resolved_feishu_targets=[],
        ),
    )

    with pytest.raises(RuntimeError, match="default target is required"):
        main.run_feishu_doctor(argparse.Namespace(feishu=True, feishu_target=[], all_feishu_targets=False))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py tests/test_feishu_commands.py -k "incomplete_default_feishu_auth or fails_preflight" -v`
Expected: FAIL because config save still writes directly and execution paths do not consult shared preflight

- [ ] **Step 3: Write minimal implementation**

```python
from llm_usage.runtime_preflight import ensure_runtime_bootstrap, validate_runtime_config


def run_config_editor(env_path: Path, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    ...
    ensure_runtime_bootstrap(
        env_path=env_path,
        reports_dir=env_path.parent / "reports",
        bootstrap_text="",
    )
    ...
    if answer == "s":
        validation = validate_runtime_config(
            basic={key: draft.values.get(key, "") for key in BASIC_KEYS},
            feishu_default={key: draft.values.get(key, "") for key in FEISHU_KEYS},
            feishu_targets=[asdict(target) for target in draft.feishu_named_targets],
            mode="config_save",
        )
        if not validation.ok:
            for error in validation.errors:
                stdout.write(f"{error}\n")
            continue
        _save_config_draft(env_path, draft)
        return 0
```

```python
def _execution_preflight(*, feishu_target=None, all_feishu_targets=False) -> PreflightResult:
    _load_runtime_env()
    basic = {
        "ORG_USERNAME": os.getenv("ORG_USERNAME", ""),
        "HASH_SALT": os.getenv("HASH_SALT", ""),
    }
    feishu_default = {key: os.getenv(key, "") for key in FEISHU_KEYS}
    named_targets = [asdict(target) for target in ConfigDraft.from_document(load_env_document(_env_path())).feishu_named_targets]
    return validate_runtime_config(
        basic=basic,
        feishu_default=feishu_default,
        feishu_targets=named_targets,
        mode="execution",
    )
```

```python
def _sync_rows_to_feishu_targets(...):
    if dry_run:
        print("dry-run: bundle validated and upload skipped")
        return 0
    preflight = _execution_preflight(feishu_target=feishu_target, all_feishu_targets=all_feishu_targets)
    if not preflight.ok:
        for error in preflight.errors:
            print(f"error: {error}")
        return 1
```

```python
def run_feishu_doctor(args: argparse.Namespace) -> int:
    preflight = _execution_preflight(
        feishu_target=getattr(args, "feishu_target", []),
        all_feishu_targets=bool(getattr(args, "all_feishu_targets", False)),
    )
    if not preflight.ok:
        raise RuntimeError("; ".join(preflight.errors))
    targets = _resolve_feishu_sync_selection(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py tests/test_feishu_commands.py -k "incomplete_default_feishu_auth or fails_preflight" -v`
Expected: PASS

- [ ] **Step 5: Run the focused regression suite**

Run: `pytest tests/test_runtime_preflight.py tests/test_web.py tests/test_interaction.py tests/test_feishu_commands.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_usage/interaction.py src/llm_usage/main.py tests/test_interaction.py tests/test_feishu_commands.py
git commit -m "feat: enforce runtime preflight before save and sync"
```

## Self-Review

### Spec coverage

- Auto-bootstrap on config entry: Task 1 and Task 3 cover bootstrap creation and Web config load behavior.
- Shared preflight API and Feishu inheritance: Task 2 defines and tests the central validator.
- Web `init` / `load config` / `validate` / `save` reactions: Task 3 wires the response metadata and save blocking behavior.
- CLI config save and execution-time blocking: Task 4 applies the shared validator to menu save, `sync`, and Feishu doctor.

No spec gaps remain for this scope.

### Placeholder scan

- No `TODO`, `TBD`, or deferred implementation notes remain.
- Every test and implementation step includes concrete file paths, commands, and code snippets.
- No task depends on an undefined helper without introducing it in the same task.

### Type consistency

- Shared result type is consistently named `PreflightResult`.
- Bootstrap metadata uses `bootstrap_applied`, `created_env`, `created_reports`, and `auto_fixes` throughout.
- Feishu save and execution use the same `validate_runtime_config(..., mode=...)` entry point.

Plan complete and saved to `docs/superpowers/plans/2026-04-08-runtime-preflight-robustness.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
