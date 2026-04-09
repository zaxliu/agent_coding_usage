# Feishu Bitable Multi-Target Compatibility Design

## Summary

This design adds three Feishu-focused capabilities while preserving existing behavior for current users:

1. `doctor --feishu`: validate Feishu target reachability and schema completeness.
2. `init --feishu-bitable-schema`: create missing standard columns in a target Bitable table.
3. Multi-target Feishu sync: support multiple `FEISHU_APP_TOKEN` / `table_id` destinations from the same `.env`.

The compatibility rule is strict: an existing `.env` that only uses legacy single-target Feishu keys must continue to work without changes, and default command behavior must remain unchanged unless the user explicitly opts into new Feishu target selection flags.

## Goals

- Keep existing single-target `.env` files fully compatible.
- Add an explicit, readable `.env` syntax for multiple Feishu targets.
- Make Feishu schema validation available without performing data sync.
- Provide a safe, additive schema bootstrap command that only creates missing fields.
- Keep Python and Node CLIs behaviorally aligned.
- Expand `config` and `README` so users can discover and manage the new model without breaking existing workflows.

## Non-Goals

- No destructive schema migration in Feishu.
- No automatic field rename, delete, or type rewrite.
- No silent change to default `sync` fan-out behavior.
- No move from `.env` to a new config file format.
- No attempt to reconcile arbitrary user-defined Feishu schemas beyond the standard upload field set.

## Current Behavior

Current Python and Node sync flows both:

- read a single `FEISHU_APP_TOKEN`
- read an optional single `FEISHU_TABLE_ID`
- auto-select the first table if `FEISHU_TABLE_ID` is empty
- fetch the remote field list only during `upsert`
- drop unknown fields with warnings such as `飞书表缺少字段，已跳过：...`

Current `doctor` does not probe Feishu at all, and current `init` only initializes local runtime files.

## Design Overview

The design introduces a first-class concept of a **Feishu target**. A target identifies one destination Bitable table and its auth material. The runtime resolves targets from `.env`, then applies command-specific target selection rules.

There are always two possible classes of targets:

- `default`: synthesized from legacy top-level keys
- named targets: declared through a new `FEISHU_TARGETS` list and per-target prefixed env vars

Legacy behavior remains anchored on `default`.

## Env Design

### Legacy Single-Target Keys

These keys remain valid and unchanged:

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BOT_TOKEN`

If any legacy Feishu key is present, the runtime constructs a `default` target from them.

### New Multi-Target Keys

Add one new list key:

```dotenv
FEISHU_TARGETS=team_b,finance
```

For each target name, add prefixed keys:

```dotenv
FEISHU_TEAM_B_APP_TOKEN=app_token_for_team_b
FEISHU_TEAM_B_TABLE_ID=tbl_for_team_b
FEISHU_TEAM_B_APP_ID=cli_xxx
FEISHU_TEAM_B_APP_SECRET=sec_xxx
FEISHU_TEAM_B_BOT_TOKEN=

FEISHU_FINANCE_APP_TOKEN=app_token_for_finance
FEISHU_FINANCE_TABLE_ID=
FEISHU_FINANCE_APP_ID=cli_yyy
FEISHU_FINANCE_APP_SECRET=sec_yyy
FEISHU_FINANCE_BOT_TOKEN=
```

### Target Name Rules

- names are declared in `FEISHU_TARGETS`
- names are normalized like remote aliases: trim whitespace, lowercase for identity, env prefix uses uppercase snake case
- allowed characters should be limited to letters, digits, and `_`
- `default` is reserved and cannot appear inside `FEISHU_TARGETS`

### Inheritance Rules

To minimize duplication while staying predictable:

- `APP_TOKEN` is always target-specific and required per target
- `TABLE_ID` is target-specific and optional per target
- auth keys may inherit from legacy top-level keys if omitted:
  - `APP_ID`
  - `APP_SECRET`
  - `BOT_TOKEN`

This allows teams to share one Feishu app credential set while targeting multiple Bitable apps or tables.

### Resolution Rules

Runtime resolves targets in this order:

1. Build `default` from legacy top-level keys if any legacy Feishu key is present.
2. Parse `FEISHU_TARGETS`.
3. For each named target, build a target object from prefixed keys plus inherited auth defaults.
4. Validate duplicate names and malformed target identifiers.

Errors:

- duplicate target names: hard error
- missing target `APP_TOKEN`: hard error for that target when selected
- missing both `BOT_TOKEN` and `APP_ID` / `APP_SECRET`: hard error for that target when selected

Unselected malformed named targets should not break unrelated legacy commands unless the user explicitly selects them or asks for all targets. This preserves compatibility for partially configured environments.

## Command Design

### `sync`

Default compatibility behavior:

- if no new target-selection flag is supplied, `sync` uploads only to `default`
- if only legacy keys exist, behavior is identical to today

New options:

- `--feishu-target NAME`
- `--all-feishu-targets`

Rules:

- `--feishu-target NAME` uploads only to the named target
- `--all-feishu-targets` uploads to `default` plus all named targets that are valid and resolvable
- `--feishu-target default` explicitly selects the legacy/default target
- `--feishu-target` may appear multiple times to select a subset
- mixing `--feishu-target` and `--all-feishu-targets` is a usage error

Execution behavior:

- rows are collected once
- target uploads run sequentially for deterministic logs and simpler rate-limit behavior
- per-target summaries are printed with target labels
- command exit code is:
  - `0` if all selected targets succeed
  - non-zero if any selected target hard-fails or reports failed row uploads

### `doctor`

Existing `doctor` remains unchanged when called without Feishu-specific flags.

New options:

- `--feishu`
- `--feishu-target NAME`
- `--all-feishu-targets`

Rules:

- plain `doctor` keeps current collector/env behavior only
- `doctor --feishu` checks the `default` target only
- `doctor --feishu --feishu-target NAME` checks one or more named targets
- `doctor --feishu --all-feishu-targets` checks all resolved targets
- `--feishu-target` or `--all-feishu-targets` without `--feishu` is a usage error, to keep the feature explicit

Checks performed per target:

1. target config completeness
2. auth token resolution
3. table reachability
4. field list fetch success
5. required field completeness against standard upload schema
6. field type compatibility warnings for standard fields where the runtime has expectations

Recommended output shape:

- `feishu[default]: OK - schema complete`
- `feishu[team_b]: WARN - missing fields: output_tokens_sum, updated_at`
- `feishu[finance]: ERROR - auth failed`

Exit code policy:

- `0`: all selected targets pass or only emit non-fatal warnings
- `2`: at least one selected target has a hard error such as auth failure, permission failure, or field API failure

Compatibility note:

- missing schema fields are warnings, not hard errors, so teams can audit before running schema init

### `init`

Existing `init` behavior remains unchanged when called without Feishu schema flags.

New options:

- `--feishu-bitable-schema`
- `--feishu-target NAME`
- `--all-feishu-targets`
- `--dry-run`

Rules:

- plain `init` still only initializes local `.env` and reports directory
- `init --feishu-bitable-schema` initializes schema for `default`
- `init --feishu-bitable-schema --feishu-target NAME` initializes one or more named targets
- `init --feishu-bitable-schema --all-feishu-targets` initializes all resolved targets
- `--feishu-target` or `--all-feishu-targets` without `--feishu-bitable-schema` is a usage error

Per-target behavior:

1. resolve auth
2. resolve table id; if empty, auto-select first table exactly like current sync behavior
3. fetch existing fields
4. compare with standard schema
5. create only missing standard fields
6. report created, skipped, and suspicious type mismatch fields

Safety rules:

- no deletion
- no rename
- no type mutation of existing fields
- no overwrite of user-added columns

Dry-run behavior:

- prints the fields that would be created per target
- does not call create-field endpoints

Exit code policy:

- `0`: all selected targets initialized successfully or had nothing to do
- `2`: at least one selected target had a hard error

## Standard Schema Definition

The single source of truth remains the upload-field contract currently defined in code:

- `date_local`
- `user_hash`
- `source_host_hash`
- `tool`
- `model`
- `input_tokens_sum`
- `cache_tokens_sum`
- `output_tokens_sum`
- `row_key`
- `updated_at`

Design extension:

- move from a plain field-name set to a richer schema definition object
- each field definition should include:
  - name
  - desired Feishu field type
  - human description for docs/help if useful
  - whether type mismatch is warn-only

This schema definition will be used by:

- sync field filtering
- doctor completeness/type checks
- init schema creation
- README field reference

## Feishu Field Type Strategy

To avoid breaking current behavior, type enforcement is conservative.

Recommended initial mapping:

- `date_local`: Text or DateTime-compatible warning-only
- `user_hash`: Text
- `source_host_hash`: Text
- `tool`: Text
- `model`: Text
- `input_tokens_sum`: Number
- `cache_tokens_sum`: Number
- `output_tokens_sum`: Number
- `row_key`: Text
- `updated_at`: DateTime

Compatibility rule:

- if a field exists with a different type, `doctor` warns and `init --feishu-bitable-schema` does not mutate it
- sync continues current best-effort normalization behavior, especially for DateTime-like fields

## Config Command Design

Current `config` opens an interactive editor around the active runtime `.env`. It should remain the preferred user-facing configuration workflow, while raw env editing remains supported.

### Default Behavior

`llm-usage config` should continue opening the interactive menu editor with no required flags.

### Menu Changes

Current top-level menu includes `Basic`, `Feishu`, `Cursor`, `Remotes`, and `Advanced / Raw Env`.

Recommended Feishu section redesign:

- `2. Feishu`
  - `1. Edit default target`
  - `2. Manage named targets`
  - `3. Doctor current Feishu targets`
  - `4. Initialize current Feishu schema`
  - `b. Back`

The editor should not force users to understand raw env prefixes before they can manage multiple targets.

### Default Target Editing

Editing default target maps directly to legacy keys:

- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_ID`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BOT_TOKEN`

This preserves user muscle memory and avoids rewriting existing `.env` files unnecessarily.

### Named Target Management

Provide CRUD-like interactions:

- list targets from `FEISHU_TARGETS`
- add target
- edit target
- delete target

For each named target:

- prompt for target name
- edit target-specific `APP_TOKEN`
- edit optional `TABLE_ID`
- edit optional auth overrides
- show inherited values as inherited, not duplicated

Deletion behavior:

- remove target name from `FEISHU_TARGETS`
- remove all corresponding `FEISHU_<TARGET>_*` keys

### Config Command Extensions

To keep automation and scripting possible without forcing TUI automation, add optional non-interactive shortcuts:

- `config --list-feishu-targets`
- `config --show-feishu-target NAME`
- `config --add-feishu-target NAME`
- `config --delete-feishu-target NAME`

Optional but recommended:

- `config --set-feishu-target NAME --app-token ... --table-id ...`

These shortcuts should be additive. They must not change the default `config` experience.

### Validation in Config

The editor and non-interactive config sub-flows should validate:

- reserved name `default`
- duplicate target names
- invalid target-name characters
- deleting last named target should update `FEISHU_TARGETS` to empty cleanly

Validation should be immediate in the config flow so users do not discover format mistakes only at sync time.

## README Design

README should be expanded but remain readable for legacy users. The structure should explicitly separate "single target" from "multiple targets".

### New README Sections

1. Keep the current `飞书多维表格同步` section.
2. Add a short compatibility note near the top of that section:
   - existing single-target env files still work unchanged
3. Split into subsections:
   - `单目标配置（兼容旧版）`
   - `多目标配置`
   - `检查目标表结构`
   - `初始化目标表结构`
   - `同步到一个或多个目标表`

### README Content Changes

#### Single target subsection

Keep today’s example almost unchanged so existing users recognize it immediately.

#### Multi-target subsection

Add a concrete `.env` example:

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
FEISHU_FINANCE_APP_ID=cli_finance
FEISHU_FINANCE_APP_SECRET=sec_finance
```

Document inheritance explicitly:

- named targets inherit top-level `APP_ID` / `APP_SECRET` / `BOT_TOKEN` if omitted
- named targets do not inherit `APP_TOKEN`
- empty `TABLE_ID` still means auto-select first table for that target

#### Doctor subsection

Add examples:

```bash
llm-usage doctor --feishu
llm-usage doctor --feishu --feishu-target team_b
llm-usage doctor --feishu --all-feishu-targets
```

Document warning vs error semantics.

#### Init schema subsection

Add examples:

```bash
llm-usage init --feishu-bitable-schema --dry-run
llm-usage init --feishu-bitable-schema --feishu-target finance
llm-usage init --feishu-bitable-schema --all-feishu-targets
```

Document that the command only creates missing standard fields and never deletes or rewrites existing columns.

#### Sync subsection

Add examples:

```bash
llm-usage sync
llm-usage sync --feishu-target team_b
llm-usage sync --feishu-target team_b --feishu-target finance
llm-usage sync --all-feishu-targets
```

Document the most important compatibility rule:

- without target flags, `sync` still uploads only to the legacy/default target

### Field Reference Table

Replace the simple bullet list of field names with a compact table:

- field name
- expected role
- recommended Feishu field type
- required for sync yes/no

This makes doctor/init output easier to connect back to README guidance.

## Python / Node Parity

Both implementations should share the same semantics for:

- env parsing
- target inheritance
- default vs named target selection
- CLI flags
- exit code behavior
- help text language

If full parity cannot land in one change, Python remains the reference implementation and Node should:

- reject unsupported new flags clearly, or
- implement the same features in the same release

Silent divergence is not acceptable.

## Error Handling

Usage errors:

- selecting target flags without enabling the corresponding feature flag
- passing both `--feishu-target` and `--all-feishu-targets`
- selecting unknown target names

Runtime errors:

- auth failure
- permission failure
- table list failure
- field list failure
- field creation failure

Warning cases:

- missing standard fields in doctor
- existing field type mismatch
- named target inherits auth from default keys
- auto-selected first table because `TABLE_ID` is empty

## Testing Strategy

Add tests for:

- legacy single-target env remains unchanged
- target parsing with `FEISHU_TARGETS`
- target inheritance and precedence
- reserved and invalid target names
- sync target-selection rules
- doctor target-selection rules
- init schema dry-run and create-only behavior
- config command menu/help behavior
- README examples kept in sync with parser/help expectations where feasible

Important compatibility tests:

- old `.env` + plain `sync` works exactly as before
- old `.env` + plain `doctor` works exactly as before
- old `.env` + plain `init` works exactly as before
- adding named targets does not change plain `sync` fan-out

## Rollout Notes

Recommended rollout order:

1. introduce Feishu target parsing and compatibility tests
2. add shared schema definition object
3. add `doctor --feishu`
4. add `init --feishu-bitable-schema`
5. add multi-target `sync`
6. extend `config`
7. update README and examples

This order keeps risk controlled and allows read-only validation features to land before schema mutation and multi-target sync.

## Open Decisions Resolved

- Multi-target configuration stays in `.env`, not a new config file.
- Legacy top-level Feishu keys remain the source of `default`.
- Multi-target sync requires explicit target flags; no implicit fan-out.
- Schema initialization is additive only.
- `config` remains interactive by default but gains optional Feishu target shortcuts.
