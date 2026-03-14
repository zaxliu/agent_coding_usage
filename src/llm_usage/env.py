from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def split_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def upsert_env_var(path: Path, key: str, value: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("env key cannot be empty")

    encoded = f"{key}={value}"
    if not path.exists():
        path.write_text(encoded + "\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith(f"{key}="):
            out.append(encoded)
            replaced = True
        else:
            out.append(raw)

    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(encoded)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
