# Runtime Preflight Robustness Design

## Goal

Improve robustness around `init`, `config`, and `sync` so that:

- entering `config` in a fresh environment no longer depends on the user remembering to run `init`
- invalid Feishu configurations fail early during `config save`
- runtime commands report aggregated prerequisite failures before deeper execution begins

This design adopts a shared preflight layer rather than scattered command-specific checks.

## Problems

### Problem 1: Fresh environment relies on hidden bootstrap behavior

Today a new user can enter `config` before running `init`, because some runtime paths and `.env` creation are implicitly handled. That avoids an immediate crash, but it also hides whether the environment is initialized and pushes meaningful failures later into `sync`.

### Problem 2: `config save` only performs shallow validation

Current validation checks basic shape such as Feishu target names, remote aliases, and some missing remote fields. It does not validate whether a Feishu target is actually runnable. As a result, a user can save an incomplete default target and only see a failure when `sync` tries to upload.

### Problem 3: Validation logic is fragmented

Web save, CLI config editing, and runtime execution paths do not share one source of truth for configuration validity. This creates drift and inconsistent user experience.

## Non-Goals

- redesigning the full config UI or menu flows
- introducing a persisted config state machine such as `draft` or `runnable`
- changing Feishu upload semantics beyond earlier validation and clearer error reporting

## Recommended Approach

Implement a shared runtime preflight module and use it in three places:

1. `config` entry bootstrap
2. `config save` validation
3. command execution preflight for `sync`, `sync preview`, and Feishu doctor

The system should distinguish between:

- bootstrap concerns: creating runtime skeleton files and directories
- configuration validity: whether the saved config is executable

Bootstrap should be automatically repaired. Invalid executable config should fail fast and explicitly.

## Design

### 1. Bootstrap on `config` entry

When the user enters `config`, the program should automatically ensure the active runtime skeleton exists:

- runtime config directory exists
- runtime `.env` exists, initialized from bootstrap template if missing
- `reports/` exists

This behavior must be idempotent. Re-entering `config` should not overwrite existing values.

This replaces the user-facing expectation that `init` must be run first for configuration editing. `init` still exists as an explicit command, but `config` no longer depends on user memory for initial setup.

### 2. Shared preflight API

Add a shared validator, conceptually shaped like:

```python
validate_runtime_config(..., mode="config_save" | "execution") -> PreflightResult
```

`PreflightResult` should contain:

- `ok: bool`
- `errors: list[str]`
- `warnings: list[str]`
- `auto_fixes: list[str]`
- `resolved_feishu_targets: list[...]`

The validator must be pure with respect to reporting. It may read config and resolve inheritance, but it should not print directly or terminate the process. Callers decide how to render messages.

### 3. Validation modes

Two strictness levels are required.

#### `config_save`

Used when the user saves configuration from Web or CLI config editors.

Behavior:

- run full Feishu structural validation
- reject save if any blocking error exists
- allow warnings to pass through without blocking save
- do not require unrelated runtime-only inputs such as transient SSH passwords

#### `execution`

Used immediately before commands that depend on executable configuration, including:

- `sync`
- `sync preview`
- Feishu doctor

Behavior:

- include the same Feishu structural validation as `config_save`
- include execution prerequisites required by the specific command
- return aggregated errors before deeper runtime execution starts

## Feishu Rules

### Core rule set

Feishu validation should use resolved target semantics, not raw field presence alone.

#### Default target

The default target is mandatory for a valid executable configuration.

The default target must satisfy all of the following:

- `FEISHU_APP_TOKEN` is required
- either `FEISHU_BOT_TOKEN` is present, or both `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are present
- `FEISHU_TABLE_ID` may be empty; if empty, emit a warning that first-table auto-discovery will be used

If only one of `FEISHU_APP_ID` or `FEISHU_APP_SECRET` is present, this is an error.

#### Named targets

Each named target must satisfy:

- its own `APP_TOKEN` is required
- its own auth takes precedence if present:
  - `BOT_TOKEN`, or
  - `APP_ID + APP_SECRET`
- if no complete auth is present on the named target, it inherits auth from the default target
- if inheritance still does not provide complete auth, validation fails
- `TABLE_ID` may be empty and should only produce a warning

This means a named target may save and execute with only its own `APP_TOKEN` when the default target has valid auth.

### Invalid Feishu examples

These cases must fail during `config save`:

- no default target configured
- default target has `APP_TOKEN` only
- default target has only `APP_ID`
- default target has only `APP_SECRET`
- named target missing `APP_TOKEN`
- named target has partial auth that is still incomplete after considering inheritance

### Error format

Errors should identify the target explicitly, for example:

- `feishu[default]: missing APP_TOKEN`
- `feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET`
- `feishu[finance]: missing APP_TOKEN`
- `feishu[finance]: auth not configured and cannot inherit from default`

Warnings should also remain target-scoped where relevant, for example:

- `feishu[default]: TABLE_ID is empty; first table will be auto-selected`

## Command Behavior

### `config`

- automatically bootstrap runtime skeleton on entry
- surface any auto-fix notes in a non-blocking way if needed
- allow the user to edit configuration normally

### `config save`

- run shared preflight with `mode=config_save`
- if `errors` is non-empty, reject the save and show all errors together
- if only `warnings` exist, save successfully and show the warnings

### `sync` and `sync preview`

- run shared preflight with `mode=execution` before upload-specific work begins
- report aggregated prerequisite failures before deeper runtime or API calls
- preserve existing execution logic after preflight succeeds

### Feishu doctor

- run shared preflight with `mode=execution`
- if preflight fails, return those errors rather than probing Feishu APIs with incomplete credentials

## Web Behavior

The Web layer should use the same bootstrap and preflight rules as CLI flows. The main difference is response shape and frontend rendering.

### Response shape

For Web endpoints that initialize, validate, or save config, use a consistent JSON structure where applicable:

- `ok: bool`
- `errors: list[str]`
- `warnings: list[str]`
- `auto_fixes: list[str]`
- `bootstrap_applied: bool`

Write-oriented endpoints may add:

- `saved: bool`

Initialization endpoints may add:

- `created_env: bool`
- `created_reports: bool`

### `POST /api/init`

Behavior:

- explicitly bootstrap runtime skeleton
- remain idempotent
- never overwrite existing config values

Response expectations:

- `ok=true`
- `created_env` reflects whether `.env` was newly created
- `created_reports` reflects whether `reports/` was newly created
- `bootstrap_applied=true` if either of those was created

Frontend reaction:

- show a success message when the action is user-triggered
- if nothing new was created, show a neutral success state rather than an error

### `GET /api/config`

Behavior:

- before loading config payload, ensure runtime bootstrap has been applied
- return current editable config plus bootstrap metadata when bootstrap happened during the request

Response expectations:

- existing config payload fields remain
- add `bootstrap_applied`
- add `auto_fixes` when bootstrap created missing runtime skeleton pieces

Frontend reaction:

- load and render config normally
- if `bootstrap_applied=true`, show a non-blocking informational notice such as "已自动初始化运行时目录"
- do not treat bootstrap as a validation error

### `POST /api/config/validate`

Behavior:

- run shared preflight using `mode=config_save`
- do not persist any changes

Response expectations:

- `ok` is `false` when blocking validation errors exist
- `errors` contains all blocking issues
- `warnings` contains non-blocking issues such as empty `TABLE_ID`
- `auto_fixes` is usually empty here unless request handling also had to bootstrap runtime skeleton
- `bootstrap_applied` indicates whether a missing runtime skeleton was auto-created before validation

Frontend reaction:

- display all returned errors together
- display warnings separately from errors
- preserve unsaved form state
- do not silently coerce an invalid result into a saveable state

### `PUT /api/config`

Behavior:

- run shared preflight using `mode=config_save`
- if any blocking errors exist, reject the save and do not write `.env`
- if only warnings exist, write config successfully

Response expectations:

- on failure:
  - `ok=false`
  - `saved=false`
  - `errors` contains all blocking issues
- on success:
  - `ok=true`
  - `saved=true`
  - `warnings` may still be present
- include `bootstrap_applied` and `auto_fixes` if runtime skeleton creation happened during request handling

Frontend reaction:

- on save failure, keep the current draft values in the UI
- render all blocking errors together, with target-scoped Feishu messages preserved
- on save success with warnings, show success and warnings together
- do not require the user to re-open the page after a failed save

## Module Boundaries

Introduce a shared validation module in Python for the runtime commands and Web layer. The exact file name may vary, but the responsibilities should be:

- bootstrap helper for runtime skeleton existence
- Feishu target resolution and validation
- execution preflight aggregation

Existing Web and interaction layers should call this shared module instead of duplicating Feishu validation rules.

Expected primary integration points:

- `src/llm_usage/web.py`
- `src/llm_usage/interaction.py`
- shared runtime validation module

## Testing

Add or update tests to cover:

- entering config in a fresh environment auto-creates runtime `.env` and `reports/`
- `config save` fails when default target only has `APP_TOKEN`
- `config save` fails when default target is missing entirely
- `config save` succeeds when named target has only `APP_TOKEN` but default auth is complete
- `config save` fails when named target lacks `APP_TOKEN`
- `sync` and Feishu doctor fail at preflight with aggregated config errors instead of deep runtime failures
- warning-only cases such as missing `TABLE_ID` remain non-blocking

## Tradeoffs

### Benefits

- users get earlier and clearer feedback
- Web and CLI config editing converge on one rule set
- runtime commands fail for the right reason and at the right layer

### Costs

- introduces a small amount of refactoring to centralize validation
- requires careful distinction between save-time and execution-time checks

## Open Questions

There are no remaining open questions for this scope. The user-selected policy is:

- `config` entry auto-bootstraps runtime skeleton
- blocking Feishu errors reject `config save`
- default target is mandatory
- named targets may inherit auth only from default
