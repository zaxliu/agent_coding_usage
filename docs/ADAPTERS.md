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

## Current adapters

- `claude_code`: default local globs under `~/.claude` and `~/.config/claude`
- `codex`: default local globs under `~/.codex`
- `cursor`: default local globs under Cursor user storage/log locations (macOS + Linux)

## Add a new tool

1. Create `src/llm_usage/collectors/<tool>.py`
2. Return a `FileCollector` or custom collector class implementing `BaseCollector`
3. Register builder in `src/llm_usage/collectors/__init__.py`
4. Add the collector in `_collectors()` inside `src/llm_usage/main.py`
5. Add tests for parser behavior and date-range filtering

## Privacy rule

Do not upload raw prompt/response text. Only aggregate fields are allowed in Feishu payload.
