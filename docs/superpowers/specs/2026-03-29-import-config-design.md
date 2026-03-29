# Import Config Design

## Goal

Add an explicit `llm-usage import-config` command so users who downloaded the source repository and are still using the legacy project-root `.env` can migrate into the new runtime config/data directories in one step.

## Problem

The current runtime path resolver can detect legacy files and prompt opportunistically, but that behavior is implicit and tied to whichever command the user runs next. It also hides the migration surface behind path resolution instead of giving users a clear migration command.

## Scope

- Add `llm-usage import-config`
- Migrate legacy repo-root `.env`
- Migrate legacy `reports/runtime_state.json`
- Support an explicit source repo path
- Support dry-run and forced overwrite modes
- Keep migration copy-based; do not delete legacy files

## Command Shape

```bash
llm-usage import-config [--from PATH] [--force] [--dry-run]
```

### Arguments

- `--from PATH`: optional legacy repository root; defaults to current working directory
- `--force`: overwrite target files without prompting
- `--dry-run`: print planned copy actions without writing files

## Behavior

The command treats `PATH/.env` and `PATH/reports/runtime_state.json` as the import source. The destination is the current runtime location returned by `resolve_runtime_paths(repo_root)`:

- `.env` -> `env_path`
- `reports/runtime_state.json` -> `runtime_state_path`

The command copies only files that exist. Missing source files are reported as skipped, not errors. If neither source file exists, the command exits non-zero with a clear message.

## Conflict Policy

- If target file does not exist: copy directly
- If target file exists and contents are identical: report `unchanged`
- If target file exists and differs:
  - `--force`: overwrite
  - interactive terminal: ask once per file
  - non-interactive terminal without `--force`: skip with warning and exit non-zero if nothing was imported

## User Messaging

Successful output should explicitly list:

- source path
- destination path
- status per file: `copied`, `overwritten`, `unchanged`, `skipped`, `missing`

At the end, print a short note that future commands will use the runtime config/data locations rather than the legacy repo-root files.

## Integration With Existing Legacy Detection

Keep the current fallback behavior in `resolve_runtime_paths()` for compatibility, but update warning/prompt text to recommend `llm-usage import-config` for a clean one-time migration.

## Architecture

Implementation should stay small and explicit:

- add a helper dataclass to describe import results
- add file-copy helpers near runtime path logic or in `main.py`
- keep parser wiring in `build_parser()`
- keep command handler in `main.py`

## Testing

Add focused tests for:

- import `.env` only
- import runtime state only
- import both files
- `--from` path support
- dry-run does not write files
- existing target file prompts/skips by default
- `--force` overwrites target files
- non-interactive run without `--force` skips conflicting targets
