# llm-usage-sync

Local-first usage collector for Claude Code, Codex, and Cursor with Feishu Bitable aggregation.
It can also pull Claude/Codex logs from multiple remote servers over SSH and upload them from the desktop machine.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
llm-usage init
# edit .env (ORG_USERNAME is required, e.g. san.zhang)
llm-usage doctor
llm-usage collect --ui auto
llm-usage sync --ui auto
llm-usage bundle
```

## Commands

- `llm-usage init`: create `.env`/`.env.example` and `reports/`
- `llm-usage doctor`: check config and source files
- `llm-usage collect`: local + selected remote aggregation + terminal report + CSV
- `llm-usage sync`: local + selected remote aggregation and upsert to Feishu Bitable
- `llm-usage bundle`: build internal/external zip bundles for distribution

## Privacy model

Uploaded fields are whitelisted aggregate metrics only:

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

No prompt text, session id, path, or command text is uploaded.

`ORG_USERNAME` is required. Set your group username in `.env` (for example `san.zhang`).

## Feishu Bitable fields

Create fields with exact names:

- `date_local` (text/date)
- `user_hash` (text)
- `source_host_hash` (text)
- `tool` (text)
- `model` (text)
- `input_tokens_sum` (number)
- `cache_tokens_sum` (number)
- `output_tokens_sum` (number)
- `row_key` (text, unique recommended)
- `updated_at` (text/datetime)

## Feishu auth env

- `FEISHU_APP_TOKEN`: target bitable app token
- `FEISHU_TABLE_ID`: target table id (optional; if empty, sync auto-selects the first table)
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: app credentials used to fetch `tenant_access_token` at runtime
- `FEISHU_BOT_TOKEN` (optional): if set, used directly as bearer token and skips runtime token fetch

## Distribution bundles

Use `llm-usage bundle` to generate two zip files in `dist/`:

- `internal`: keeps shared team config for sync, but clears personal identifiers and local machine overrides
- `external`: ships a fully sanitized `.env` with all internal secrets removed

Sanitization rules:

- both bundles clear `ORG_USERNAME`, `CURSOR_WEB_SESSION_TOKEN`, `CURSOR_WEB_WORKOS_ID`,
  `CLAUDE_LOG_PATHS`, `CODEX_LOG_PATHS`, `CURSOR_LOG_PATHS`, and all `REMOTE_*` values
- external bundle also clears `HASH_SALT` and all `FEISHU_*` values
- dashboard defaults such as `CURSOR_DASHBOARD_BASE_URL` are reset to safe defaults

Useful options:

- `llm-usage bundle --output-dir some/path`
- `llm-usage bundle --keep-staging`

## Source path overrides

Use comma-separated glob patterns in `.env` if defaults are not enough:

- `CLAUDE_LOG_PATHS`
- `CODEX_LOG_PATHS`
- `CURSOR_LOG_PATHS`

## Remote SSH collection

Remote collection only applies to `claude_code` and `codex`. `cursor` remains desktop-only.

Configure static remotes in `.env`:

- `REMOTE_HOSTS=SERVER_A,SERVER_B`
- `REMOTE_SERVER_A_SSH_HOST=host-a`
- `REMOTE_SERVER_A_SSH_USER=alice`
- `REMOTE_SERVER_A_SSH_PORT=22`
- `REMOTE_SERVER_A_LABEL=prod-a`
- `REMOTE_SERVER_A_CLAUDE_LOG_PATHS=/home/alice/.claude/**/*.jsonl`
- `REMOTE_SERVER_A_CODEX_LOG_PATHS=/home/alice/.codex/**/*.jsonl`

Behavior:

- `collect` / `sync` support `--ui auto|tui|cli|none`
- `auto` prefers a lightweight TUI when available and falls back to pure CLI
- the selector remembers the last chosen static remotes in `reports/runtime_state.json`
- you can add a temporary remote during `collect` / `sync`; it is only saved to `.env` if you confirm it
- temporary remotes auto-generate their source label as `ssh_user@ssh_host`; you do not need to type a label
- `source_host_hash` is derived from `ORG_USERNAME + source_label + HASH_SALT`, so different users on one shared server do not collide
- remote execution only assumes SSH plus a minimal `python3` or `python` on the server

## Cursor Pro+ dashboard collection (optional)

If you are on Cursor Pro+ (not Teams), you can collect historical token usage
directly from `https://cursor.com/dashboard/usage` by setting:

- `CURSOR_WEB_SESSION_TOKEN`: value of `WorkosCursorSessionToken` cookie from `cursor.com`
- `CURSOR_WEB_WORKOS_ID` (optional): auxiliary `workos_id` cookie; auto-filled by `collect/sync` when available
- `CURSOR_DASHBOARD_BASE_URL` (optional, default `https://cursor.com`)
- `CURSOR_DASHBOARD_TEAM_ID` (optional, default `0`)
- `CURSOR_DASHBOARD_PAGE_SIZE` (optional, default `300`)
- `CURSOR_DASHBOARD_TIMEOUT_SEC` (optional, default `15`)

`browser-cookie3` is installed with this project by default, so `collect` / `sync`
can auto-capture the cookie from your normal browser without extra setup.

Behavior:

- if `CURSOR_WEB_SESSION_TOKEN` is set, cursor collector uses web dashboard API
  (`POST /api/dashboard/get-filtered-usage-events`)
- if existing `CURSOR_WEB_SESSION_TOKEN` is expired, collect/sync auto-refreshes it from browser cookies
- if it is empty and local cursor logs are unavailable, or local logs have no events in lookback,
  `llm-usage collect` / `llm-usage sync`
  will reuse existing system-browser cookies; if still missing, it opens the normal browser for login,
  then auto-detects session cookies from local browser profiles and saves them to `.env`
- timeout can be adjusted with `--cursor-login-timeout-sec` on `collect` / `sync`
- browser can be selected with `--cursor-login-browser`
  (supports `chrome` / `edge` / `safari` / `firefox`, default `default`)
- `--cursor-login-user-data-dir` is kept for compatibility and ignored in system-browser mode

## Scheduling templates

- Linux cron template: `templates/cron.sample`
- macOS launchd template: `templates/com.team.llm-usage-sync.plist`
