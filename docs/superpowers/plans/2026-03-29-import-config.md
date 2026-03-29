# Import Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `llm-usage import-config` command that copies legacy repo-root config/state files into the runtime config/data directories with safe overwrite behavior.

**Architecture:** Keep the command handler in `src/llm_usage/main.py` and reuse `resolve_runtime_paths()` to determine destination paths. Add a small helper layer for file import decisions so output and overwrite rules are testable without running the full CLI.

**Tech Stack:** Python, argparse, pathlib, shutil, pytest

---

### Task 1: Add failing tests for import behavior

**Files:**
- Create: `tests/test_import_config.py`
- Modify: `tests/test_main_identity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_config_copies_legacy_env_and_runtime_state(...):
    ...


def test_import_config_dry_run_does_not_write(...):
    ...


def test_import_config_force_overwrites_existing_targets(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_config.py -q`
Expected: FAIL because `cmd_import_config` and parser wiring do not exist yet

- [ ] **Step 3: Write minimal implementation**

```text
Add parser wiring and a command handler that can import `.env` and
`reports/runtime_state.json` from a source repo path into runtime destinations.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_import_config.py -q`
Expected: PASS

### Task 2: Implement import planning and overwrite policy

**Files:**
- Modify: `src/llm_usage/main.py`
- Optionally Modify: `src/llm_usage/paths.py`
- Test: `tests/test_import_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_import_config_noninteractive_skips_conflicts_without_force(...):
    ...


def test_import_config_uses_explicit_from_path(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_config.py -q`
Expected: FAIL on conflict handling and `--from` path behavior

- [ ] **Step 3: Write minimal implementation**

```python
def cmd_import_config(args: argparse.Namespace) -> int:
    ...
```

Implement:
- source discovery
- per-file status calculation
- interactive overwrite prompt
- `--force`
- `--dry-run`
- exit code policy

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_import_config.py -q`
Expected: PASS

### Task 3: Wire CLI help text and legacy guidance

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `src/llm_usage/paths.py`
- Modify: `README.md`
- Test: `tests/test_import_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_parser_includes_import_config_command():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_config.py -q`
Expected: FAIL because parser help/subcommand wiring is incomplete

- [ ] **Step 3: Write minimal implementation**

```text
Add `import-config` subparser with `--from`, `--force`, and `--dry-run`.
Update legacy-path warning text to recommend `llm-usage import-config`.
Document the migration command in README.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_import_config.py -q`
Expected: PASS

### Task 4: Final verification

**Files:**
- Modify: `src/llm_usage/main.py`
- Modify: `src/llm_usage/paths.py`
- Modify: `README.md`
- Create: `tests/test_import_config.py`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_import_config.py tests/test_paths.py tests/test_main_identity.py -q`
Expected: PASS

- [ ] **Step 2: Run packaging sanity for unchanged CLI project state**

Run: `pytest tests/test_packaging.py -q`
Expected: PASS
