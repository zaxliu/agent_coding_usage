from __future__ import annotations

import hashlib


def hash_user(username: str, salt: str) -> str:
    payload = f"{username}|{salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_row_key(
    user_hash: str,
    date_local: str,
    tool: str,
    model: str,
    session_fingerprint: str | None = None,
) -> str:
    identity = session_fingerprint.strip() if session_fingerprint and session_fingerprint.strip() else f"model:{model}"
    payload = f"{user_hash}|{date_local}|{tool}|{identity}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
