# llm-usage-sync

Local-first usage collector for Claude Code, Codex, and Cursor with Feishu Bitable aggregation.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
llm-usage init
# edit .env (ORG_USERNAME is required, e.g. san.zhang)
llm-usage doctor
llm-usage collect
llm-usage sync
```

## Commands

- `llm-usage init`: create `.env`/`.env.example` and `reports/`
- `llm-usage doctor`: check config and source files
- `llm-usage collect`: local aggregation + terminal report + CSV
- `llm-usage sync`: local aggregation and upsert to Feishu Bitable

## Privacy model

Uploaded fields are whitelisted aggregate metrics only:

- `date_local`
- `user_hash`
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

## Source path overrides

Use comma-separated glob patterns in `.env` if defaults are not enough:

- `CLAUDE_LOG_PATHS`
- `CODEX_LOG_PATHS`
- `CURSOR_LOG_PATHS`

## Cursor Pro+ dashboard collection (optional)

If you are on Cursor Pro+ (not Teams), you can collect historical token usage
directly from `https://cursor.com/dashboard/usage` by setting:

- `CURSOR_WEB_SESSION_TOKEN`: value of `WorkosCursorSessionToken` cookie from `cursor.com`
- `CURSOR_WEB_WORKOS_ID` (optional): auxiliary `workos_id` cookie; auto-filled by `collect/sync` when available
- `CURSOR_DASHBOARD_BASE_URL` (optional, default `https://cursor.com`)
- `CURSOR_DASHBOARD_TEAM_ID` (optional, default `0`)
- `CURSOR_DASHBOARD_PAGE_SIZE` (optional, default `300`)
- `CURSOR_DASHBOARD_TIMEOUT_SEC` (optional, default `15`)

To auto-capture the cookie from your normal browser (recommended), install once:

```bash
pip install browser-cookie3
```

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
