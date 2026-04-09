from .base import BaseCollector, CollectOutput


__all__ = [
    "BaseCollector",
    "CollectOutput",
    "build_claude_collector",
    "build_copilot_cli_collector",
    "build_copilot_vscode_collector",
    "build_codex_collector",
    "build_cursor_collector",
    "build_opencode_collector",
    "OpenCodeCollector",
]


def __getattr__(name: str):
    if name == "build_claude_collector":
        from .claude import build_claude_collector

        return build_claude_collector
    if name == "build_copilot_cli_collector":
        from .copilot import build_copilot_cli_collector

        return build_copilot_cli_collector
    if name == "build_copilot_vscode_collector":
        from .copilot import build_copilot_vscode_collector

        return build_copilot_vscode_collector
    if name == "build_codex_collector":
        from .codex import build_codex_collector

        return build_codex_collector
    if name == "build_cursor_collector":
        from .cursor import build_cursor_collector

        return build_cursor_collector
    if name == "build_opencode_collector":
        from .opencode import build_opencode_collector

        return build_opencode_collector
    if name == "OpenCodeCollector":
        from .opencode import OpenCodeCollector

        return OpenCodeCollector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
