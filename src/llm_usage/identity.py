from __future__ import annotations

import hashlib
from typing import Optional


def hash_user(username: str, salt: str) -> str:
    payload = f"{username}|{salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_source_host(username: str, source_label: str, salt: str) -> str:
    payload = f"{username}|{source_label}|{salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_row_key(
    user_hash: str,
    source_host_hash: str,
    date_local: str,
    tool: str,
    model: str,
    session_fingerprint: Optional[str] = None,
) -> str:
    identity = session_fingerprint.strip() if session_fingerprint and session_fingerprint.strip() else f"model:{model}"
    payload = f"{user_hash}|{source_host_hash}|{date_local}|{tool}|{identity}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
