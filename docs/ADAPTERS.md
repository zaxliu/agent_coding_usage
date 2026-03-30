# Collector Adapter Guide

This project uses collector adapters so new tools can be added without changing the sync pipeline.

## Adapter contract

Each adapter should provide:

- `probe() -> (bool, str)`: quick health check for local source availability.
- `collect(start, end) -> CollectOutput`: return normalized usage events in UTC.

Event schema:

- `tool`
- `model`
- `event_time` (UTC)
- `input_tokens`
- `cache_tokens`
- `output_tokens`
- `source_ref` (local-only debugging field)
- `source_host_hash`

## Current adapters

- `claude_code`: default local globs under `~/.claude` and `~/.config/claude`
- `codex`: default local globs under `~/.codex`
- `copilot_cli`: local session logs under `~/.copilot/session-state` (Windows uses `%APPDATA%` / user-profile equivalents when available)
- `copilot_vscode`: local VS Code chat session files under VS Code user storage (`Code/User/globalStorage/emptyWindowChatSessions`)
- `cursor`: local globs by default; if `CURSOR_WEB_SESSION_TOKEN` is set, uses Cursor dashboard web API (`/api/dashboard/get-filtered-usage-events`). optional `CURSOR_WEB_WORKOS_ID` is sent as auxiliary auth cookie when present. if token is empty and local cursor logs are unavailable, or local logs have no events in lookback, `collect/sync` opens the login page. on Windows Chromium browsers, `collect/sync` prefers a tool-managed browser profile login flow instead of scanning the user's default browser cookies; manual token paste remains the fallback.
- `remote claude_code/codex/copilot_cli/copilot_vscode`: selected at runtime via TUI or CLI, fetched over SSH from configured `REMOTE_*` hosts, and normalized on the desktop machine

## Add a new tool

1. Create `src/llm_usage/collectors/<tool>.py`
2. Return a `FileCollector` or custom collector class implementing `BaseCollector`
3. Register builder in `src/llm_usage/collectors/__init__.py`
4. Add the collector in `_collectors()` inside `src/llm_usage/main.py`
5. Add tests for parser behavior and date-range filtering

## Privacy rule

Do not upload raw prompt/response text. Only aggregate fields are allowed in Feishu payload.
Use `source_host_hash` for remote/source identity; do not upload raw hostnames or SSH details.
