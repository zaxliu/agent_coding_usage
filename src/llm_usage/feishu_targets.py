from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

_NAME_RE = re.compile(r"^[a-z0-9_]+$")

_LEGACY_ENV_KEYS = (
    "FEISHU_APP_TOKEN",
    "FEISHU_TABLE_ID",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BOT_TOKEN",
)


@dataclass(frozen=True)
class FeishuTargetConfig:
    name: str
    app_token: str
    table_id: str = ""
    app_id: str = ""
    app_secret: str = ""
    bot_token: str = ""
    inherited_auth: bool = False


def normalize_feishu_target_name(raw: str) -> str:
    value = raw.strip().lower()
    if not _NAME_RE.fullmatch(value):
        raise RuntimeError(f"invalid feishu target name: {raw!r}")
    if value == "default":
        raise RuntimeError("feishu target name 'default' is reserved")
    return value


def _normalize_target_label(raw: str) -> str:
    return raw.strip().lower()


def _legacy_any_nonempty(env: Mapping[str, str]) -> bool:
    return any(env.get(k, "").strip() for k in _LEGACY_ENV_KEYS)


def _read_prefixed_or_legacy(
    env: Mapping[str, str],
    *,
    prefix: str,
    suffix: str,
    legacy_key: str,
) -> tuple[str, bool]:
    """Return (value, inherited_from_legacy)."""
    v = env.get(f"{prefix}{suffix}", "").strip()
    if v:
        return v, False
    leg = env.get(legacy_key, "").strip()
    return leg, bool(leg)


def _parse_feishu_targets_list(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        name = normalize_feishu_target_name(part)
        if name in seen:
            raise RuntimeError(f"duplicate feishu target name: {name}")
        seen.add(name)
        ordered.append(name)
    return tuple(ordered)


def _target_key_prefix(normalized_name: str) -> str:
    return f"FEISHU_{normalized_name.upper()}_"


def _default_from_legacy(env: Mapping[str, str]) -> Optional[FeishuTargetConfig]:
    if not _legacy_any_nonempty(env):
        return None
    return FeishuTargetConfig(
        name="default",
        app_token=env.get("FEISHU_APP_TOKEN", "").strip(),
        table_id=env.get("FEISHU_TABLE_ID", "").strip(),
        app_id=env.get("FEISHU_APP_ID", "").strip(),
        app_secret=env.get("FEISHU_APP_SECRET", "").strip(),
        bot_token=env.get("FEISHU_BOT_TOKEN", "").strip(),
        inherited_auth=False,
    )


def _named_target_from_env(env: Mapping[str, str], normalized_name: str) -> FeishuTargetConfig:
    pfx = _target_key_prefix(normalized_name)
    app_id, id_inh = _read_prefixed_or_legacy(env, prefix=pfx, suffix="APP_ID", legacy_key="FEISHU_APP_ID")
    app_secret, sec_inh = _read_prefixed_or_legacy(
        env, prefix=pfx, suffix="APP_SECRET", legacy_key="FEISHU_APP_SECRET"
    )
    bot_token, bot_inh = _read_prefixed_or_legacy(env, prefix=pfx, suffix="BOT_TOKEN", legacy_key="FEISHU_BOT_TOKEN")
    return FeishuTargetConfig(
        name=normalized_name,
        app_token=env.get(f"{pfx}APP_TOKEN", "").strip(),
        table_id=env.get(f"{pfx}TABLE_ID", "").strip(),
        app_id=app_id,
        app_secret=app_secret,
        bot_token=bot_token,
        inherited_auth=id_inh or sec_inh or bot_inh,
    )


def resolve_feishu_targets_from_env(env: Optional[Mapping[str, str]] = None) -> list[FeishuTargetConfig]:
    """Parse Feishu targets: synthetic ``default`` from legacy keys plus optional named targets.

    Order is ``default`` (if any legacy key is set) then each name in ``FEISHU_TARGETS`` left-to-right.
    """
    from os import environ

    m: Mapping[str, str] = environ if env is None else env
    out: list[FeishuTargetConfig] = []
    default = _default_from_legacy(m)
    if default is not None:
        out.append(default)

    raw_list = m.get("FEISHU_TARGETS", "").strip()
    named_order = _parse_feishu_targets_list(raw_list) if raw_list else ()
    for name in named_order:
        out.append(_named_target_from_env(m, name))

    return out


def select_feishu_targets(
    targets: Sequence[FeishuTargetConfig],
    *,
    selected_names: Optional[Sequence[str]] = None,
    select_all: bool = False,
    default_only: bool = True,
) -> list[FeishuTargetConfig]:
    """Choose targets for a command.

    - ``select_all``: every resolved target in order (default first, then named declaration order).
    - Non-empty ``selected_names``: explicit selection (labels normalized like env names).
    - Otherwise with ``default_only``: only the ``default`` target when present, else empty.
    - Otherwise: empty list.
    """
    if select_all and selected_names is not None and len(selected_names) > 0:
        raise ValueError("cannot combine select_all with explicit feishu target names")
    by_name = {t.name: t for t in targets}
    if select_all:
        return list(targets)
    names_list = list(selected_names) if selected_names is not None else []
    if len(names_list) > 0:
        normalized = [_normalize_target_label(n) for n in names_list]
        picked: list[FeishuTargetConfig] = []
        for n in normalized:
            if n not in by_name:
                raise ValueError(f"unknown feishu target: {n}")
            picked.append(by_name[n])
        return picked
    if default_only:
        d = by_name.get("default")
        return [d] if d is not None else []
    return []
