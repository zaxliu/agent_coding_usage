from __future__ import annotations

import json
from pathlib import Path


def load_selected_remote_aliases(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    aliases = payload.get("selected_remote_aliases")
    if not isinstance(aliases, list):
        return []
    out: list[str] = []
    for alias in aliases:
        if isinstance(alias, str) and alias.strip():
            out.append(alias.strip())
    return out


def save_selected_remote_aliases(path: Path, aliases: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"selected_remote_aliases": aliases}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
