# Config Menu Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `llm-usage config` command that opens a menu-driven configuration editor with in-memory draft changes, structured remote editing, and explicit `Save` / `Discard` behavior.

**Architecture:** Keep the CLI entrypoint in Python and add a dedicated config-editing layer rather than scattering prompt logic through `main.py`. Parse the active `.env` into a draft model, edit the draft through menu helpers in `interaction.py`, serialize once on save, and continue using the existing `REMOTE_*` wire format so the runtime collectors and doctor/sync flows stay compatible.

**Tech Stack:** Python 3.11+, `argparse`, existing terminal interaction helpers, `pytest`

---

## File Structure

- Modify: `src/llm_usage/main.py`
  Purpose: register the new `config` subcommand, wire it to the interactive editor, and update top-level help/examples.
- Modify: `src/llm_usage/env.py`
  Purpose: add full-file `.env` parsing and serialization helpers that preserve comments/order where practical and support one-shot save from a draft model.
- Modify: `src/llm_usage/remotes.py`
  Purpose: add bidirectional conversion between `REMOTE_*` env entries and structured remote draft objects, plus helpers for add/edit/delete/reorder-safe serialization.
- Modify: `src/llm_usage/interaction.py`
  Purpose: implement menu-driven config editor flows, grouped config screens, structured remote editor screens, list editing for path arrays, dirty-state prompts, and save/discard confirmation.
- Modify: `tests/test_cli_help.py`
  Purpose: cover `config` help text and top-level help examples.
- Modify: `tests/test_remotes.py`
  Purpose: cover remote draft round-trip parsing/serialization and alias handling after edit/delete.
- Modify: `tests/test_interaction.py`
  Purpose: cover config editor CLI flows, draft save/discard, remote editing, and structured path list editing.
- Add: `tests/test_env.py`
  Purpose: cover `.env` document parsing, save behavior, raw key preservation, and one-shot serialization.
- Modify: `README.md`
  Purpose: document `llm-usage config` as the preferred way to edit `.env`, especially for remotes.

### Task 1: Add `.env` document parsing and one-shot save primitives

**Files:**
- Modify: `src/llm_usage/env.py`
- Test: `tests/test_env.py`

- [ ] **Step 1: Write the failing tests for `.env` document parsing and save**

```python
from pathlib import Path

from llm_usage.env import EnvDocument, load_env_document, save_env_document


def test_load_env_document_preserves_comments_and_blank_lines(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# Identity\nORG_USERNAME=alice\n\n# Extra\nCUSTOM_FLAG=1\n",
        encoding="utf-8",
    )

    document = load_env_document(env_path)

    assert document.get("ORG_USERNAME") == "alice"
    assert document.get("CUSTOM_FLAG") == "1"
    assert document.render().startswith("# Identity\nORG_USERNAME=alice\n")


def test_save_env_document_updates_known_key_and_keeps_unknown_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\nCUSTOM_FLAG=1\n", encoding="utf-8")
    document = load_env_document(env_path)

    document.set("ORG_USERNAME", "bob")
    document.set("LOOKBACK_DAYS", "30")
    save_env_document(env_path, document)

    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=bob" in text
    assert "CUSTOM_FLAG=1" in text
    assert "LOOKBACK_DAYS=30" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_env.py -v`
Expected: FAIL with `ImportError` or missing `EnvDocument` / `load_env_document` / `save_env_document`

- [ ] **Step 3: Write the minimal `.env` document model and serializer**

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EnvLine:
    kind: str
    raw: str = ""
    key: str = ""
    value: str = ""


@dataclass
class EnvDocument:
    lines: list[EnvLine]

    def get(self, key: str, default: str = "") -> str:
        for line in self.lines:
            if line.kind == "entry" and line.key == key:
                return line.value
        return default

    def set(self, key: str, value: str) -> None:
        for line in self.lines:
            if line.kind == "entry" and line.key == key:
                line.value = value
                return
        self.lines.append(EnvLine(kind="entry", key=key, value=value))

    def delete(self, key: str) -> None:
        self.lines = [line for line in self.lines if not (line.kind == "entry" and line.key == key)]

    def render(self) -> str:
        rendered = []
        for line in self.lines:
            if line.kind == "entry":
                rendered.append(f"{line.key}={line.value}")
            else:
                rendered.append(line.raw)
        return "\n".join(rendered).rstrip("\n") + "\n"


def load_env_document(path: Path) -> EnvDocument:
    if not path.exists():
        return EnvDocument(lines=[])
    parsed: list[EnvLine] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            parsed.append(EnvLine(kind="raw", raw=raw))
            continue
        key, value = raw.split("=", 1)
        parsed.append(EnvLine(kind="entry", key=key.strip(), value=value.strip()))
    return EnvDocument(lines=parsed)


def save_env_document(path: Path, document: EnvDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.render(), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_env.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/env.py tests/test_env.py
git commit -m "feat: add env document editing primitives"
```

### Task 2: Add structured remote draft conversion helpers

**Files:**
- Modify: `src/llm_usage/remotes.py`
- Test: `tests/test_remotes.py`

- [ ] **Step 1: Write the failing tests for remote draft round-trip behavior**

```python
from llm_usage.remotes import (
    RemoteDraft,
    drafts_from_env_document,
    apply_remote_drafts_to_document,
)
from llm_usage.env import EnvDocument, EnvLine


def test_drafts_from_env_document_reads_structured_remote_fields():
    document = EnvDocument(
        lines=[
            EnvLine(kind="entry", key="REMOTE_HOSTS", value="SERVER_A"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_HOST", value="host-a"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_USER", value="alice"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_SSH_PORT", value="2200"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_LABEL", value="prod-a"),
            EnvLine(kind="entry", key="REMOTE_SERVER_A_CLAUDE_LOG_PATHS", value="/a,/b"),
        ]
    )

    drafts = drafts_from_env_document(document)

    assert len(drafts) == 1
    assert drafts[0].alias == "SERVER_A"
    assert drafts[0].ssh_port == 2200
    assert drafts[0].claude_log_paths == ["/a", "/b"]


def test_apply_remote_drafts_to_document_rewrites_remote_section():
    document = EnvDocument(lines=[EnvLine(kind="entry", key="ORG_USERNAME", value="alice")])
    drafts = [
        RemoteDraft(
            alias="SERVER_A",
            ssh_host="host-a",
            ssh_user="alice",
            ssh_port=22,
            source_label="alice@host-a",
            claude_log_paths=["/a"],
            codex_log_paths=["/b"],
            copilot_cli_log_paths=["/c"],
            copilot_vscode_session_paths=["/d"],
            use_sshpass=False,
        )
    ]

    apply_remote_drafts_to_document(document, drafts)

    assert document.get("REMOTE_HOSTS") == "SERVER_A"
    assert document.get("REMOTE_SERVER_A_SSH_HOST") == "host-a"
    assert document.get("REMOTE_SERVER_A_COPILOT_VSCODE_SESSION_PATHS") == "/d"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remotes.py -v`
Expected: FAIL with missing `RemoteDraft` / `drafts_from_env_document` / `apply_remote_drafts_to_document`

- [ ] **Step 3: Write the minimal remote draft helpers**

```python
from dataclasses import dataclass


@dataclass
class RemoteDraft:
    alias: str
    ssh_host: str
    ssh_user: str
    ssh_port: int
    source_label: str
    claude_log_paths: list[str]
    codex_log_paths: list[str]
    copilot_cli_log_paths: list[str]
    copilot_vscode_session_paths: list[str]
    use_sshpass: bool = False


def drafts_from_env_document(document: EnvDocument) -> list[RemoteDraft]:
    env = {line.key: line.value for line in document.lines if line.kind == "entry"}
    configs = parse_remote_configs_from_env(env)
    return [
        RemoteDraft(
            alias=config.alias,
            ssh_host=config.ssh_host,
            ssh_user=config.ssh_user,
            ssh_port=config.ssh_port,
            source_label=config.source_label,
            claude_log_paths=list(config.claude_log_paths),
            codex_log_paths=list(config.codex_log_paths),
            copilot_cli_log_paths=list(config.copilot_cli_log_paths),
            copilot_vscode_session_paths=list(config.copilot_vscode_session_paths),
            use_sshpass=config.use_sshpass,
        )
        for config in configs
    ]


def apply_remote_drafts_to_document(document: EnvDocument, drafts: list[RemoteDraft]) -> None:
    remote_keys = [line.key for line in document.lines if line.kind == "entry" and line.key.startswith("REMOTE_")]
    for key in remote_keys:
        document.delete(key)

    if not drafts:
        return

    document.set("REMOTE_HOSTS", ",".join(draft.alias for draft in drafts))
    for draft in drafts:
        prefix = f"REMOTE_{draft.alias}_"
        document.set(prefix + "SSH_HOST", draft.ssh_host)
        document.set(prefix + "SSH_USER", draft.ssh_user)
        document.set(prefix + "SSH_PORT", str(draft.ssh_port))
        document.set(prefix + "LABEL", draft.source_label)
        document.set(prefix + "CLAUDE_LOG_PATHS", ",".join(draft.claude_log_paths))
        document.set(prefix + "CODEX_LOG_PATHS", ",".join(draft.codex_log_paths))
        document.set(prefix + "COPILOT_CLI_LOG_PATHS", ",".join(draft.copilot_cli_log_paths))
        document.set(prefix + "COPILOT_VSCODE_SESSION_PATHS", ",".join(draft.copilot_vscode_session_paths))
        document.set(prefix + "USE_SSHPASS", "1" if draft.use_sshpass else "0")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remotes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/remotes.py tests/test_remotes.py
git commit -m "feat: add remote draft serialization helpers"
```

### Task 3: Add grouped config draft model and save/discard flow

**Files:**
- Modify: `src/llm_usage/interaction.py`
- Test: `tests/test_interaction.py`

- [ ] **Step 1: Write the failing tests for config session save/discard**

```python
from io import StringIO
from pathlib import Path

from llm_usage.interaction import run_config_editor


class _TTYStringIO(StringIO):
    def isatty(self):  # noqa: ANN201
        return True


def test_run_config_editor_discards_unsaved_changes(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\nq\nd\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert env_path.read_text(encoding="utf-8") == "ORG_USERNAME=alice\n"


def test_run_config_editor_saves_draft_changes(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    assert "ORG_USERNAME=bob" in env_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py::test_run_config_editor_discards_unsaved_changes tests/test_interaction.py::test_run_config_editor_saves_draft_changes -v`
Expected: FAIL with missing `run_config_editor`

- [ ] **Step 3: Write the minimal config session editor loop**

```python
def run_config_editor(
    env_path: Path,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    document = load_env_document(env_path)
    draft = ConfigDraft.from_document(document)

    while True:
        stdout.write("Config\n")
        stdout.write("  1. Basic\n")
        stdout.write("  2. Feishu\n")
        stdout.write("  3. Cursor\n")
        stdout.write("  4. Remotes\n")
        stdout.write("  5. Advanced / Raw Env\n")
        stdout.write("  s. Save\n")
        stdout.write("  d. Discard\n")
        stdout.write("  q. Quit\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()

        if answer == "1":
            _edit_key_menu(draft, BASIC_KEYS, stdin=stdin, stdout=stdout)
            continue
        if answer == "4":
            _edit_remotes_menu(draft, stdin=stdin, stdout=stdout)
            continue
        if answer == "s":
            save_config_draft(env_path, draft)
            return 0
        if answer == "d":
            return 0
        if answer == "q":
            if not draft.dirty or _confirm_discard(stdin=stdin, stdout=stdout):
                return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py::test_run_config_editor_discards_unsaved_changes tests/test_interaction.py::test_run_config_editor_saves_draft_changes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/interaction.py tests/test_interaction.py
git commit -m "feat: add config editor session flow"
```

### Task 4: Add structured remote menu editing and path-list editing

**Files:**
- Modify: `src/llm_usage/interaction.py`
- Test: `tests/test_interaction.py`

- [ ] **Step 1: Write the failing tests for remote editing**

```python
from pathlib import Path

from llm_usage.interaction import run_config_editor


def test_run_config_editor_adds_remote_and_path_entries(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")

    user_input = "\n".join(
        [
            "4",      # Remotes
            "a",      # Add remote
            "prod-a", # alias
            "host-a", # host
            "alice",  # user
            "22",     # port
            "",       # label defaults
            "n",      # use_sshpass
            "p",      # edit paths
            "1",      # claude paths
            "a",      # add path
            "/logs/claude.jsonl",
            "b",      # back path list
            "b",      # back path groups
            "b",      # back remote edit
            "b",      # back remotes
            "s",      # save
        ]
    ) + "\n"

    run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=_TTYStringIO())

    text = env_path.read_text(encoding="utf-8")
    assert "REMOTE_HOSTS=PROD_A" in text
    assert "REMOTE_PROD_A_SSH_HOST=host-a" in text
    assert "REMOTE_PROD_A_CLAUDE_LOG_PATHS=/logs/claude.jsonl" in text


def test_run_config_editor_deletes_remote_without_touching_other_keys(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=alice\nREMOTE_HOSTS=SERVER_A\nREMOTE_SERVER_A_SSH_HOST=host-a\nREMOTE_SERVER_A_SSH_USER=alice\n",
        encoding="utf-8",
    )

    user_input = "\n".join(["4", "d", "1", "b", "s"]) + "\n"
    run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=_TTYStringIO())

    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice" in text
    assert "REMOTE_HOSTS" not in text
    assert "REMOTE_SERVER_A_SSH_HOST" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interaction.py::test_run_config_editor_adds_remote_and_path_entries tests/test_interaction.py::test_run_config_editor_deletes_remote_without_touching_other_keys -v`
Expected: FAIL because the remotes menu and path editing flow do not exist yet

- [ ] **Step 3: Write the minimal structured remote and path editors**

```python
def _edit_remotes_menu(draft: ConfigDraft, stdin: TextIO, stdout: TextIO) -> None:
    while True:
        stdout.write("Remotes\n")
        for index, remote in enumerate(draft.remotes, start=1):
            stdout.write(f"  {index}. {remote.alias} {remote.ssh_user}@{remote.ssh_host}:{remote.ssh_port}\n")
        stdout.write("  a. Add remote\n")
        stdout.write("  e. Edit remote\n")
        stdout.write("  d. Delete remote\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "a":
            draft.remotes.append(_prompt_remote(stdin=stdin, stdout=stdout, existing_aliases=[item.alias for item in draft.remotes]))
            draft.dirty = True
        elif answer == "d":
            index = int(_read_line("Delete which remote: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False)) - 1
            draft.remotes.pop(index)
            draft.dirty = True
        elif answer == "e":
            index = int(_read_line("Edit which remote: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False)) - 1
            _edit_remote_detail(draft.remotes[index], stdin=stdin, stdout=stdout)
            draft.dirty = True
        elif answer == "b":
            return


def _edit_path_list(values: list[str], stdin: TextIO, stdout: TextIO) -> list[str]:
    while True:
        for index, value in enumerate(values, start=1):
            stdout.write(f"  {index}. {value}\n")
        stdout.write("  a. Add path\n")
        stdout.write("  d. Delete path\n")
        stdout.write("  b. Back\n")
        answer = _read_line("> ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip().lower()
        if answer == "a":
            new_value = _read_line("Path: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False).strip()
            if new_value:
                values.append(new_value)
        elif answer == "d":
            index = int(_read_line("Delete which path: ", stdin=stdin, stdout=stdout, use_prompt_toolkit=False)) - 1
            values.pop(index)
        elif answer == "b":
            return values
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_interaction.py::test_run_config_editor_adds_remote_and_path_entries tests/test_interaction.py::test_run_config_editor_deletes_remote_without_touching_other_keys -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/interaction.py tests/test_interaction.py
git commit -m "feat: add structured remote config editor"
```

### Task 5: Wire the `config` subcommand into the CLI

**Files:**
- Modify: `src/llm_usage/main.py`
- Test: `tests/test_cli_help.py`

- [ ] **Step 1: Write the failing tests for CLI help and command dispatch**

```python
from __future__ import annotations

import pytest

import llm_usage.main as main


def test_top_level_help_includes_config_command(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "config" in help_text
    assert "llm-usage config" in help_text


def test_config_help_describes_menu_editor(capsys):
    parser = main.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["config", "--help"])

    help_text = capsys.readouterr().out
    assert "interactive menu editor" in help_text.lower()
    assert "save or discard" in help_text.lower()
    assert "remote" in help_text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_help.py::test_top_level_help_includes_config_command tests/test_cli_help.py::test_config_help_describes_menu_editor -v`
Expected: FAIL because `config` is not registered yet

- [ ] **Step 3: Write the minimal CLI wiring**

```python
def cmd_config(_: argparse.Namespace) -> int:
    _ensure_env_file_exists()
    return run_config_editor(_env_path())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(...)
    sub = parser.add_subparsers(dest="command")

    config_parser = sub.add_parser(
        "config",
        help="Open the interactive menu editor for the active runtime .env",
        description=(
            "Open an interactive menu editor for the active runtime .env.\n"
            "Changes stay in memory until you explicitly Save or Discard.\n"
            "Remote SSH sources are edited through a structured menu."
        ),
        formatter_class=_HelpFormatter,
    )
    config_parser.set_defaults(command_name="config")

    commands = {
        "config": cmd_config,
        ...
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_help.py::test_top_level_help_includes_config_command tests/test_cli_help.py::test_config_help_describes_menu_editor -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_usage/main.py tests/test_cli_help.py
git commit -m "feat: add config command"
```

### Task 6: Document the new workflow and run focused verification

**Files:**
- Modify: `README.md`
- Test: `tests/test_env.py`
- Test: `tests/test_remotes.py`
- Test: `tests/test_interaction.py`
- Test: `tests/test_cli_help.py`

- [ ] **Step 1: Write the failing doc expectation test or checklist**

```python
def test_readme_mentions_config_editor():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "llm-usage config" in text
    assert "menu" in text.lower()
    assert "remote" in text.lower()
```

- [ ] **Step 2: Run the targeted verification set before doc update**

Run: `pytest tests/test_env.py tests/test_remotes.py tests/test_interaction.py tests/test_cli_help.py -v`
Expected: PASS for code, no README check yet if you keep this as a manual checklist instead of an automated test

- [ ] **Step 3: Update the README with the new preferred config workflow**

```markdown
- `llm-usage config`：打开菜单式配置编辑器，所有修改先保存在内存草稿，最后统一选择 Save 或 Discard

推荐配置流程：

```bash
llm-usage init
llm-usage config
llm-usage doctor
```

对于远端配置，`llm-usage config` 提供结构化编辑，不需要再手工维护 `REMOTE_*` 多行环境变量。
```

- [ ] **Step 4: Run the full focused verification**

Run: `pytest tests/test_env.py tests/test_remotes.py tests/test_interaction.py tests/test_cli_help.py -v`
Expected: PASS with all new config-editor tests green

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_env.py tests/test_remotes.py tests/test_interaction.py tests/test_cli_help.py src/llm_usage/env.py src/llm_usage/remotes.py src/llm_usage/interaction.py src/llm_usage/main.py
git commit -m "feat: add interactive config editor"
```

## Self-Review

- Spec coverage: covered unified `llm-usage config`, in-memory draft editing, grouped normal config keys, structured `Remotes`, structured path-list editing, and explicit `Save` / `Discard`.
- Placeholder scan: removed generic “handle validation later” wording and anchored each task to concrete files, tests, commands, and code shapes.
- Type consistency: the plan consistently uses `EnvDocument`, `RemoteDraft`, and `run_config_editor` as the new seams between file persistence, remote serialization, and CLI interaction.
