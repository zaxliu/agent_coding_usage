"""LLM cost analysis report generation.

Pulls aggregated usage records from one or more Feishu Bitable targets and
emits an interactive HTML cost report. Pricing per model is matched against
a built-in default table; the report's pricing UI lets the viewer edit any
value and recalculate all charts in-browser.
"""
from __future__ import annotations

import argparse
import csv
import json
import webbrowser
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Optional

from llm_usage.feishu_targets import FeishuTargetConfig, resolve_feishu_targets_from_env, select_feishu_targets
from llm_usage.sinks.feishu_bitable import FeishuBitableClient


# ---------------------------------------------------------------------------
# Default pricing: $/1M tokens
# ---------------------------------------------------------------------------
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # Anthropic — current generation
    "claude-opus-4-7":    {"input": 5,    "output": 25,   "cache": 0.5},
    "claude-opus-4-6":    {"input": 5,    "output": 25,   "cache": 0.5},
    "claude-sonnet-4-6":  {"input": 3,    "output": 15,   "cache": 0.3},
    "claude-haiku-4-5":   {"input": 1,    "output": 5,    "cache": 0.1},
    # Anthropic — previous generation
    "claude-opus-4":      {"input": 15,   "output": 75,   "cache": 1.5},
    "claude-sonnet-4":    {"input": 3,    "output": 15,   "cache": 0.3},
    "claude-sonnet-4-5":  {"input": 3,    "output": 15,   "cache": 0.3},
    "claude-3-5-sonnet":  {"input": 3,    "output": 15,   "cache": 0.3},
    "claude-3-5-haiku":   {"input": 0.8,  "output": 4,    "cache": 0.08},
    "claude-3-opus":      {"input": 15,   "output": 75,   "cache": 1.5},
    "claude-3-haiku":     {"input": 0.25, "output": 1.25, "cache": 0.03},
    # OpenAI — GPT-4
    "gpt-4o":             {"input": 2.5,  "output": 10,   "cache": 1.25},
    "gpt-4o-mini":        {"input": 0.15, "output": 0.6,  "cache": 0.075},
    "gpt-4-turbo":        {"input": 10,   "output": 30,   "cache": 5},
    "gpt-4.1":            {"input": 2,    "output": 8,    "cache": 0.5},
    "gpt-4.1-mini":       {"input": 0.4,  "output": 1.6,  "cache": 0.1},
    "gpt-4.1-nano":       {"input": 0.1,  "output": 0.4,  "cache": 0.025},
    # OpenAI — GPT-5
    "gpt-5":              {"input": 1.25, "output": 10,   "cache": 0.125},
    "gpt-5-mini":         {"input": 0.25, "output": 2,    "cache": 0.025},
    "gpt-5-nano":         {"input": 0.1,  "output": 0.4,  "cache": 0.01},
    "gpt-5.1":            {"input": 1.5,  "output": 10,   "cache": 0.15},
    "gpt-5.2":            {"input": 2,    "output": 12,   "cache": 0.2},
    "gpt-5.3-codex":      {"input": 2,    "output": 12,   "cache": 0.2},
    "gpt-5.4":            {"input": 2.5,  "output": 15,   "cache": 0.25},
    "gpt-5.4-mini":       {"input": 0.75, "output": 4.5,  "cache": 0.075},
    "gpt-5.5":            {"input": 5,    "output": 30,   "cache": 0.5},
    # OpenAI — reasoning
    "o3":                 {"input": 2,    "output": 8,    "cache": 0.5},
    "o3-mini":            {"input": 1.1,  "output": 4.4,  "cache": 0.55},
    "o4-mini":            {"input": 1.1,  "output": 4.4,  "cache": 0.275},
    # Google — Gemini
    "gemini-3.1-pro":     {"input": 2,    "output": 12,   "cache": 0.2},
    "gemini-3-pro":       {"input": 2,    "output": 12,   "cache": 0.2},
    "gemini-3-flash":     {"input": 0.5,  "output": 3,    "cache": 0.05},
    "gemini-2.5-pro":     {"input": 1.25, "output": 10,   "cache": 0.125},
    "gemini-2.5-flash":   {"input": 0.3,  "output": 2.5,  "cache": 0.03},
    "gemini-2.0-flash":   {"input": 0.1,  "output": 0.4,  "cache": 0.025},
    "gemini-1.5-pro":     {"input": 1.25, "output": 5,    "cache": 0.3125},
    "gemini-1.5-flash":   {"input": 0.35, "output": 1.05, "cache": 0.0875},
    # DeepSeek
    "deepseek-r1":        {"input": 0.55, "output": 2.19, "cache": 0.14},
    "deepseek-v3":        {"input": 0.27, "output": 1.10, "cache": 0.07},
    "deepseek-v4-flash":  {"input": 0.2,  "output": 0.8,  "cache": 0.05},
    "deepseek-v4-pro":    {"input": 0.5,  "output": 2,    "cache": 0.1},
    # xAI
    "grok-code-fast-1":   {"input": 0.6,  "output": 4,    "cache": 0.15},
    # Cursor composer (uses Claude/GPT under the hood — estimate as mid-range)
    "composer":           {"input": 3,    "output": 15,   "cache": 0.3},
    # Zhipu GLM
    "glm-5.1":            {"input": 0.98, "output": 3.08, "cache": 0.1},
    "glm-5":              {"input": 0.6,  "output": 1.92, "cache": 0.06},
    "glm-4.7":            {"input": 0.38, "output": 1.74, "cache": 0.04},
    "glm-4.5-air":        {"input": 0.06, "output": 0.4,  "cache": 0.01},
    # Alibaba Qwen
    "qwen3.6-max":        {"input": 1.3,  "output": 7.8,  "cache": 0.13},
    "qwen3.6-plus":       {"input": 0.325,"output": 1.95, "cache": 0.03},
    "qwen3.6-flash":      {"input": 0.1,  "output": 0.4,  "cache": 0.01},
    "qwen3.5-plus":       {"input": 0.26, "output": 1.56, "cache": 0.03},
    "qwen3-max":          {"input": 0.78, "output": 3.9,  "cache": 0.08},
    "qwen3-coder-plus":   {"input": 0.26, "output": 1.56, "cache": 0.03},
    # MiniMax
    "minimax-m2.7":       {"input": 0.28, "output": 1.2,  "cache": 0.03},
    "minimax-m2.5":       {"input": 0.15, "output": 1.15, "cache": 0.015},
    # Moonshot Kimi
    "kimi-k2p5":          {"input": 0.6,  "output": 3,    "cache": 0.1},
    # Xiaomi MiMo
    "mimo-v2.5-pro":      {"input": 1,    "output": 3,    "cache": 0.1},
    "mimo-v2.5":          {"input": 0.4,  "output": 2,    "cache": 0.04},
    # KwaiPilot
    "kat-coder-pro":      {"input": 0.3,  "output": 1.5,  "cache": 0.03},
    # Horizon internal (CNY 9折 → USD @7.2)
    "horizon-glm":        {"input": 0.75, "output": 3.0,  "cache": 0.16},
    "horizon-deepseek":   {"input": 0.125,"output": 0.25, "cache": 0.025},
    "horizon-minimax":    {"input": 0.26, "output": 1.05, "cache": 0.053},
    # OpenAI — legacy & specialty
    "gpt-4":              {"input": 30,   "output": 60,   "cache": 15},
    "gpt-3.5-turbo":      {"input": 0.5,  "output": 1.5,  "cache": 0.25},
    "o1":                 {"input": 15,   "output": 60,   "cache": 7.5},
    "o1-mini":            {"input": 3,    "output": 12,   "cache": 1.5},
    "o1-preview":         {"input": 15,   "output": 60,   "cache": 7.5},
    # OpenAI — embedding
    "text-embedding-3-large": {"input": 0.13, "output": 0, "cache": 0},
    "text-embedding-3-small": {"input": 0.02, "output": 0, "cache": 0},
    # Meta Llama (Together/Fireworks reference pricing)
    "llama-4":            {"input": 0.27, "output": 0.85, "cache": 0.05},
    "llama-3.3-70b":      {"input": 0.6,  "output": 0.6,  "cache": 0.15},
    "llama-3.1-405b":     {"input": 3.5,  "output": 3.5,  "cache": 0.9},
    "llama-3.1-70b":      {"input": 0.6,  "output": 0.6,  "cache": 0.15},
    "llama-3.1-8b":       {"input": 0.18, "output": 0.18, "cache": 0.05},
    # Mistral
    "mistral-large":      {"input": 2,    "output": 6,    "cache": 0.5},
    "mistral-medium":     {"input": 0.4,  "output": 2,    "cache": 0.1},
    "mistral-small":      {"input": 0.1,  "output": 0.3,  "cache": 0.025},
    "codestral":          {"input": 0.3,  "output": 0.9,  "cache": 0.08},
    # Voyage embedding
    "voyage-3":           {"input": 0.06, "output": 0,    "cache": 0},
    # Cohere
    "command-r-plus":     {"input": 2.5,  "output": 10,   "cache": 0.5},
    "command-r":          {"input": 0.15, "output": 0.6,  "cache": 0.04},
    # xAI Grok
    "grok-4":             {"input": 5,    "output": 15,   "cache": 1.25},
    "grok-3":             {"input": 3,    "output": 15,   "cache": 0.75},
    "grok-3-mini":        {"input": 0.3,  "output": 0.5,  "cache": 0.075},
    "grok-2":             {"input": 2,    "output": 10,   "cache": 0.5},
    # Cursor codenames (best-effort guesses, mapped to known Anthropic tiers)
    "big-pickle":         {"input": 3,    "output": 15,   "cache": 0.3},   # ≈ sonnet
    "raptor":             {"input": 0.6,  "output": 4,    "cache": 0.15},  # ≈ grok-code-fast
}

# Order matters: more specific patterns first
MATCH_RULES: list[tuple[str, str]] = [
    # Anthropic — specific versions first
    ("opus-4-7", "claude-opus-4-7"),
    ("opus-4.7", "claude-opus-4-7"),
    ("opus-4-6", "claude-opus-4-6"),
    ("opus-4.6", "claude-opus-4-6"),
    ("opus-4-5", "claude-opus-4"),
    ("opus-4.5", "claude-opus-4"),
    ("opus-4-20250", "claude-opus-4"),
    ("sonnet-4-6", "claude-sonnet-4-6"),
    ("sonnet-4.6", "claude-sonnet-4-6"),
    ("sonnet-4-5", "claude-sonnet-4-5"),
    ("sonnet-4.5", "claude-sonnet-4-5"),
    ("sonnet-4-20250", "claude-sonnet-4"),
    ("sonnet-4", "claude-sonnet-4"),
    ("haiku-4-5", "claude-haiku-4-5"),
    ("haiku-4.5", "claude-haiku-4-5"),
    ("haiku-4", "claude-haiku-4-5"),
    # Cursor-style Claude names (claude-4.6-opus-high etc.)
    ("4.7-opus", "claude-opus-4-7"),
    ("4.6-opus", "claude-opus-4-6"),
    ("4.5-opus", "claude-opus-4"),
    ("4.6-sonnet", "claude-sonnet-4-6"),
    ("4.5-sonnet", "claude-sonnet-4-5"),
    ("claude-3-5-sonnet", "claude-3-5-sonnet"),
    ("claude-3-5-haiku", "claude-3-5-haiku"),
    ("claude-3-opus", "claude-3-opus"),
    ("claude-3-haiku", "claude-3-haiku"),
    # OpenAI — GPT-5.x (specific first)
    ("5.5-medium", "gpt-5.5"),
    ("gpt-5.5", "gpt-5.5"),
    ("5.4-mini", "gpt-5.4-mini"),
    ("5.4-nano", "gpt-5.4-mini"),
    ("5.4", "gpt-5.4"),
    ("5.3-codex", "gpt-5.3-codex"),
    ("5.2-codex", "gpt-5.2"),
    ("5.2", "gpt-5.2"),
    ("5.1-codex", "gpt-5.1"),
    ("5.1", "gpt-5.1"),
    ("gpt-5-codex", "gpt-5"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5-nano", "gpt-5-nano"),
    ("gpt-5 mini", "gpt-5-mini"),
    ("gpt-5", "gpt-5"),
    ("codex 5.3", "gpt-5.3-codex"),
    ("codex-5.3", "gpt-5.3-codex"),
    # OpenAI — GPT-4.x
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o", "gpt-4o"),
    ("gpt-4-turbo", "gpt-4-turbo"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4.1-nano", "gpt-4.1-nano"),
    ("gpt-4.1", "gpt-4.1"),
    # OpenAI — reasoning
    ("o4-mini", "o4-mini"),
    ("o3-mini", "o3-mini"),
    ("o3", "o3"),
    # Google
    ("gemini-3.1-pro", "gemini-3.1-pro"),
    ("gemini-3-pro", "gemini-3-pro"),
    ("gemini-3-flash", "gemini-3-flash"),
    ("gemini-2.5-pro", "gemini-2.5-pro"),
    ("gemini-2.5-flash", "gemini-2.5-flash"),
    ("gemini-2.0-flash", "gemini-2.0-flash"),
    ("gemini-1.5-pro", "gemini-1.5-pro"),
    ("gemini-1.5-flash", "gemini-1.5-flash"),
    # DeepSeek
    ("deepseek-r1", "deepseek-r1"),
    ("deepseek-v4-flash", "deepseek-v4-flash"),
    ("deepseek-v4-pro", "deepseek-v4-pro"),
    ("deepseek-v4", "deepseek-v4-pro"),
    ("deepseek-v3", "deepseek-v3"),
    # xAI
    ("grok-code-fast", "grok-code-fast-1"),
    ("raptor-mini", "grok-code-fast-1"),
    # Cursor composer
    ("composer", "composer"),
    # Zhipu GLM
    ("glm-5.1", "glm-5.1"),
    ("glm5-", "glm-5"),
    ("glm-5-pd", "glm-5"),
    ("glm-5-turbo", "glm-5"),
    ("glm-5", "glm-5"),
    ("glm-4.7", "glm-4.7"),
    ("glm-4.5-air", "glm-4.5-air"),
    # Alibaba Qwen
    ("qwen3.6-max", "qwen3.6-max"),
    ("qwen3.6-plus", "qwen3.6-plus"),
    ("qwen3.6-flash", "qwen3.6-flash"),
    ("qwen3.5-plus", "qwen3.5-plus"),
    ("qwen3-coder", "qwen3-coder-plus"),
    ("qwen3-max", "qwen3-max"),
    # MiniMax
    ("minimax-m2.7", "minimax-m2.7"),
    ("m2.7", "minimax-m2.7"),
    ("minimax-m2.5", "minimax-m2.5"),
    ("m2.5", "minimax-m2.5"),
    # Moonshot Kimi
    ("kimi-k2", "kimi-k2p5"),
    # Xiaomi MiMo
    ("mimo-v2.5-pro", "mimo-v2.5-pro"),
    ("mimo-v2.5", "mimo-v2.5"),
    # KwaiPilot
    ("kat-coder", "kat-coder-pro"),
    # Horizon internal
    ("horizon-glm", "horizon-glm"),
    ("horizon-deepseek", "horizon-deepseek"),
    ("horizon-minimax", "horizon-minimax"),
    # OpenAI — legacy / reasoning
    ("o1-preview", "o1-preview"),
    ("o1-mini", "o1-mini"),
    ("o1", "o1"),
    ("gpt-3.5-turbo", "gpt-3.5-turbo"),
    ("gpt-3.5", "gpt-3.5-turbo"),
    ("gpt-4-32k", "gpt-4"),
    ("gpt-4-0", "gpt-4"),
    ("gpt-4", "gpt-4"),
    # OpenAI — embedding (no version)
    ("text-embedding-3-large", "text-embedding-3-large"),
    ("text-embedding-3-small", "text-embedding-3-small"),
    ("text-embedding-ada", "text-embedding-3-small"),
    # Meta Llama
    ("llama-4", "llama-4"),
    ("llama-3.3", "llama-3.3-70b"),
    ("llama-3.1-405", "llama-3.1-405b"),
    ("llama-3.1-70", "llama-3.1-70b"),
    ("llama-3.1-8", "llama-3.1-8b"),
    ("llama-3-70", "llama-3.1-70b"),
    ("llama-3-8", "llama-3.1-8b"),
    ("llama3.1", "llama-3.1-70b"),
    ("llama3", "llama-3.1-70b"),
    # Mistral / Codestral
    ("codestral", "codestral"),
    ("mistral-large", "mistral-large"),
    ("mistral-medium", "mistral-medium"),
    ("mistral-small", "mistral-small"),
    ("mistral", "mistral-medium"),
    # Voyage
    ("voyage-3", "voyage-3"),
    ("voyage", "voyage-3"),
    # Cohere
    ("command-r-plus", "command-r-plus"),
    ("command-r", "command-r"),
    # xAI Grok
    ("grok-4", "grok-4"),
    ("grok-3-mini", "grok-3-mini"),
    ("grok-3", "grok-3"),
    ("grok-2", "grok-2"),
    # Cursor codenames
    ("big-pickle", "big-pickle"),
    ("raptor", "raptor"),
    # Generic Anthropic short aliases (fallback — match last so versioned ones win above)
    ("sonnet-3", "claude-3-5-sonnet"),
    ("haiku-3", "claude-3-5-haiku"),
    ("opus-3", "claude-3-opus"),
    ("sonnet", "claude-sonnet-4-6"),
    ("opus", "claude-opus-4-7"),
    ("haiku", "claude-haiku-4-5"),
    # Generic GPT short aliases
    ("gpt4o", "gpt-4o"),
    ("gpt-4o", "gpt-4o"),
    ("gpt4", "gpt-4"),
]


def match_pricing(model_name: str) -> dict[str, float]:
    """Map a model name (any case/separator) to a default pricing entry."""
    lower = model_name.lower().replace(" ", "-")
    for pattern, key in MATCH_RULES:
        if pattern in lower:
            return DEFAULT_PRICING[key]
    return {"input": 0.0, "output": 0.0, "cache": 0.0}


def _parse_date(val: str) -> str:
    """Normalize a date value (epoch ms / ISO / YYYY-MM-DD) to YYYY-MM-DD."""
    val = (val or "").strip()
    if not val:
        return ""
    if len(val) == 10 and val[4] == "-":
        return val
    try:
        ts = float(val)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        pass
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return val


def _safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Feishu fetch
# ---------------------------------------------------------------------------

def fetch_all_records(client: FeishuBitableClient) -> list[dict]:
    records: list[dict] = []
    for item in client.iter_all_records():
        fields = dict(item.get("fields", {}))
        fields["record_id"] = item.get("record_id", "")
        records.append(fields)
    return records


def fetch_from_targets(targets: list[FeishuTargetConfig]) -> list[dict]:
    from llm_usage.main import _feishu_bot_token_for_target, _feishu_table_id_for_target

    all_records: list[dict] = []
    for cfg in targets:
        print(f"Fetching target: {cfg.name} (app_token={cfg.app_token[:8]}...)")
        token = _feishu_bot_token_for_target(cfg)
        table_id = _feishu_table_id_for_target(cfg, token)
        client = FeishuBitableClient(app_token=cfg.app_token, table_id=table_id, bot_token=token)
        records = fetch_all_records(client)
        for r in records:
            r["_target"] = cfg.name
        all_records.extend(records)
        print(f"  -> {len(records)} records from {cfg.name}")
    return all_records


def load_from_csv(path: Path) -> list[dict]:
    """Read aggregated rows from a CSV (same schema as `usage_report.csv`)."""
    if path.is_dir():
        raise RuntimeError(f"--from-csv {str(path)!r} is a directory; provide a file path")
    try:
        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        raise RuntimeError(f"CSV not found: {path} (did you run `llm-usage collect` first?)") from None
    except OSError as exc:
        raise RuntimeError(f"failed to read CSV {path}: {exc}") from exc
    if not rows:
        raise RuntimeError(f"CSV is empty: {path}")
    return rows


def records_to_csv(records: list[dict], output_path: Path) -> None:
    if not records:
        return
    all_keys: list[str] = []
    seen: set[str] = set()
    for rec in records:
        for k in rec:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        writer.writerows(records)


# ---------------------------------------------------------------------------
# Normalize + render
# ---------------------------------------------------------------------------

def normalize_records(raw: list[dict]) -> tuple[list[dict], dict[str, dict[str, float]]]:
    """Convert raw Bitable rows to the JSON shape the HTML expects."""
    records = []
    for row in raw:
        records.append({
            "date": _parse_date(str(row.get("date_local", ""))),
            "user": row.get("user_hash", "unknown"),
            "host": row.get("source_host_hash", ""),
            "tool": row.get("tool", "unknown"),
            "model": row.get("model", "unknown"),
            "input_tokens": _safe_float(row.get("input_tokens_sum", 0)),
            "cache_tokens": _safe_float(row.get("cache_tokens_sum", 0)),
            "output_tokens": _safe_float(row.get("output_tokens_sum", 0)),
        })
    pricing = {m: match_pricing(m) for m in sorted({r["model"] for r in records})}
    return records, pricing


def _load_template() -> str:
    return resources.files("llm_usage.resources").joinpath("cost_report.html").read_text(encoding="utf-8")


def render_html(records: list[dict], pricing: dict[str, dict[str, float]]) -> str:
    html = _load_template()
    compact = (",", ":")
    html = html.replace("%%DATA_JSON%%", json.dumps(records, ensure_ascii=False, separators=compact))
    html = html.replace("%%PRICING_JSON%%", json.dumps(pricing, ensure_ascii=False, separators=compact))
    html = html.replace("%%GENERATED_DATE%%", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return html


def _prepare_output_path(raw: Optional[str], default: Path, kind: str) -> Path:
    """Resolve and validate an output file path, ensure parent exists.

    Raises RuntimeError on any path problem so the CLI's top-level try/except
    can print a clean error instead of leaking an OSError traceback. Does NOT
    pre-check writability — that's left to the actual write (avoids TOCTOU
    races and false positives from os.access's effective-vs-real UID quirks).
    """
    if raw is None:
        path = default
    else:
        s = raw.strip()
        if not s:
            raise RuntimeError(f"--{kind} cannot be empty")
        if s.endswith(("/", "\\")):
            raise RuntimeError(f"--{kind} {raw!r} looks like a directory; provide a file path")
        path = Path(s).expanduser()
    if path.is_dir():
        raise RuntimeError(f"--{kind} {str(path)!r} is an existing directory; provide a file path")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot create parent directory for --{kind} ({path.parent}): {exc}") from exc
    return path


def _write_text_safe(path: Path, text: str, kind: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"failed to write {kind} to {path}: {exc}") from exc


def cmd_cost_report(args: argparse.Namespace) -> int:
    from llm_usage.main import _load_runtime_env, _reports_dir

    _load_runtime_env()

    if args.from_csv is not None and args.feishu_target:
        raise RuntimeError("--from-csv cannot be combined with --feishu-target")

    reports_dir = _reports_dir()
    html_path = _prepare_output_path(args.output, reports_dir / "cost_report.html", "output")
    csv_path = _prepare_output_path(args.csv, html_path.with_suffix(".csv"), "csv") if args.csv else None
    if csv_path is not None and csv_path.resolve() == html_path.resolve():
        raise RuntimeError("--csv and --output cannot point to the same file")

    if args.from_csv is not None:
        src_path = reports_dir / "usage_report.csv" if args.from_csv is True else Path(args.from_csv).expanduser()
        print(f"Loading from CSV: {src_path}")
        raw = load_from_csv(src_path)
        print(f"  -> {len(raw)} rows")
    else:
        all_targets = resolve_feishu_targets_from_env()
        if not all_targets:
            raise RuntimeError("no Feishu targets configured in .env")
        if args.feishu_target:
            targets = select_feishu_targets(all_targets, selected_names=[args.feishu_target])
        else:
            targets = select_feishu_targets(all_targets, default_only=True)
        if not targets:
            if args.feishu_target:
                raise RuntimeError(f'Feishu target "{args.feishu_target}" not found')
            raise RuntimeError("default Feishu target unavailable")
        raw = fetch_from_targets(targets)
        if not raw:
            raise RuntimeError("no records fetched from Feishu")

    records, pricing = normalize_records(raw)
    print(f"Normalized {len(records)} records, {len(pricing)} unique models")

    if csv_path is not None:
        try:
            records_to_csv(raw, csv_path)
        except OSError as exc:
            raise RuntimeError(f"failed to write CSV to {csv_path}: {exc}") from exc
        print(f"Saved raw CSV to {csv_path}")

    html = render_html(records, pricing)
    _write_text_safe(html_path, html, "report")
    url = html_path.resolve().as_uri()
    print(f"Report saved to {html_path} ({len(html)//1024} KB)")
    print(f"  → {url}")
    if args.open_report:
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            print(f"warn: could not open browser: {exc}")
        else:
            if not opened:
                print("warn: webbrowser.open returned false (no GUI browser available?)")
    return 0
