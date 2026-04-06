# Web Console Dashboard Redesign

## Summary

This redesign changes the current local web console from a developer-facing JSON debug page into a dashboard-first product surface.

The home page becomes a personal analysis workbench:

- default time range is the last 30 days
- the primary visual is a wide token trend chart
- secondary visuals compare tools and models
- the detail table defaults to `date + tool + model` aggregated rows
- `config` moves out of the home page into a separate Settings page
- `doctor`, `collect`, `sync preview`, and `sync` remain available, but are reduced to a compact action cluster in the top-right

The redesign also introduces a clear distinction between:

- persistent configuration entered in Settings and written to `.env`
- runtime credentials entered only when needed during task execution, such as SSH passwords for remote collection during `collect` or `sync`

## Product Direction

### Home Page

The home page is a dashboard, not a control panel and not a raw API inspector.

Visual and product direction:

- visual language: calm analysis tool
- default range: last 30 days
- audience emphasis: mixed personal + operational, but primarily personal analysis
- information hierarchy: trend first, comparisons second, table third, controls last

Top area:

- page title and last updated time
- compact 30-day range indicator
- top-right action group with:
  - `Doctor`
  - `Collect`
  - `Sync Preview`
  - `Sync`

Main dashboard layout:

1. Primary trend chart
   - large, wide chart above the fold
   - shows `input`, `cache`, and `output` token series by day
   - this is the dominant element on the page

2. Summary cards
   - total tokens
   - active days
   - top tool
   - top model

3. Comparison charts
   - tool distribution or ranking
   - model distribution or ranking

4. Detail table
   - default row grain is `date + tool + model`
   - supports search, sorting, and filtering
   - does not show raw JSON or raw API payloads

Operational information:

- task status should appear as compact status summaries or light notifications
- recent jobs must not dominate the page
- runtime metadata should be visible only where it helps orientation, not as a full debug panel

### Settings Page

Settings becomes a separate page instead of sharing space with the dashboard.

It contains persistent configuration only:

- Basic
  - `ORG_USERNAME`
  - `HASH_SALT`
  - `TIMEZONE`
  - `LOOKBACK_DAYS`
- Feishu
  - default target
  - named targets
- Cursor
  - current cursor-related persistent fields
- Remotes
  - remote hosts, labels, paths, sshpass usage flags

Settings behavior:

- changes edit a form state first
- save writes to `.env`
- validation runs before save
- secret-like fields are masked by default

Settings must not be used for one-off runtime secrets such as an SSH password required during a specific run.

## Runtime Credential Flow

### Goal

Support manual input during `collect` or `sync` when a remote source requires data that is not safely persisted, especially SSH passwords.

### Rules

- runtime credentials are never written to `.env`
- runtime credentials are never shown as historical saved values
- runtime credentials may be remembered only in the current browser/server session
- restarting the web service clears them

### Supported behavior

When a task needs runtime input:

1. the task transitions into a `needs_input` state
2. the UI presents a blocking credential dialog
3. the dialog explains:
   - which remote needs input
   - what kind of input is required
   - that the value is only kept for the current session
4. the user enters the value
5. the task resumes

Session behavior:

- once entered, an SSH password may be reused during the current browser/server session
- the UI should make the session-scoped nature explicit
- there is no default “save permanently” option in v1

### Scope

The first runtime-input target is SSH password entry for remotes used by `collect` or `sync`.

This is enough to establish the pattern without expanding into a full credential management subsystem.

## Visual Design

### Palette

Use a neutral, professional analysis palette:

- app background: `#F3F6F8`
- surface: `#FFFFFF`
- primary text: `#14202B`
- secondary text: `#5B6B79`
- divider / grid / border: `#D9E2EA`
- primary accent: `#1F6FEB`
- secondary accent: `#0F9D94`
- warning: `#C98512`
- error: `#C74A3A`

Chart color mapping:

- input tokens: `#1F6FEB`
- cache tokens: `#7B8EA3`
- output tokens: `#0F9D94`

### Tone

Avoid:

- raw JSON panels
- decorative gradients as the main visual identity
- marketing-page styling
- overloaded control-center layouts

Prefer:

- crisp spacing
- clear alignment
- high-information charts
- restrained card usage
- readable tabular data

## Implementation Design

### Frontend

Restructure the current frontend into at least two routes or route-like views:

- `Dashboard`
- `Settings`

Dashboard responsibilities:

- fetch and render dashboard-shaped result data
- trigger collect/sync actions
- show lightweight task status
- handle runtime credential prompts when a job pauses for input

Settings responsibilities:

- fetch and edit persistent configuration
- validate and save config
- manage remotes and Feishu target structure

The frontend should stop rendering raw API payloads directly.

### Backend API Shape

The current result payload is not sufficient as a presentation model and should be upgraded to a dashboard-friendly structure.

Target shape for result-oriented responses:

- summary
  - totals
  - active days
  - top tool
  - top model
  - generated time
- timeseries
  - per-day token metrics for charting
- breakdowns
  - by tool
  - by model
- table rows
  - `date + tool + model` rows for the default detail table
- warnings

Task/job responses should support a paused-for-input flow:

- `queued`
- `running`
- `needs_input`
- `succeeded`
- `failed`

When `needs_input` is returned, the job payload should include enough structured metadata for the UI to render a credential prompt without parsing human log strings.

### Runtime Credential State

Each backend implementation should maintain a memory-only session credential store keyed by the active browser/service session and remote identity.

Minimum supported behavior:

- cache a provided SSH password for the current session
- reuse it for later remote runs in that session
- drop it when the service restarts

This state must stay separate from persisted runtime state files and `.env`.

## Error Handling

Dashboard errors:

- if no report exists yet, show an empty-state dashboard rather than raw errors
- if a chart has no data, show a clear “no data for current range” state

Task execution errors:

- show a concise task failure summary near the action area
- allow viewing task details without forcing them into the main dashboard layout

Credential errors:

- if provided SSH credentials fail, show a targeted prompt retry state
- do not silently fall back to saving credentials or disabling remotes

## Testing

Frontend:

- dashboard renders summary cards, trend chart, comparison charts, and table without raw JSON blocks
- Settings renders separately from Dashboard
- runtime credential modal appears only when a job enters `needs_input`
- credential reuse works within one session

Backend:

- dashboard result endpoint returns summary, timeseries, breakdowns, and table rows
- `collect` and `sync` can transition into `needs_input`
- submitted runtime SSH password resumes the paused job
- runtime credentials are not written into `.env`
- restarting the server clears runtime credentials

Regression:

- existing collect/sync behavior remains intact from the CLI
- existing config persistence remains intact for persistent settings

## Decisions Locked

- home page direction: personal workbench
- visual style: calm analysis tool
- default time range: 30 days
- default detail grain: `date + tool + model`
- actions live in a compact top-right action cluster
- Settings is a separate page
- runtime SSH passwords are allowed and are session-scoped only
