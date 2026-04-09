from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from llm_usage.feishu_targets import FeishuTargetConfig


@dataclass
class BootstrapResult:
    bootstrap_applied: bool
    created_env: bool = False
    created_reports: bool = False
    auto_fixes: list[str] = field(default_factory=list)


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_fixes: list[str] = field(default_factory=list)
    bootstrap_applied: bool = False
    resolved_feishu_targets: list[FeishuTargetConfig] = field(default_factory=list)


def ensure_runtime_bootstrap(*, env_path: Path, reports_dir: Path, bootstrap_text: str) -> BootstrapResult:
    created_env = False
    created_reports = False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.write_text(bootstrap_text, encoding="utf-8")
        created_env = True
    if not reports_dir.exists():
        reports_dir.mkdir(parents=True, exist_ok=True)
        created_reports = True
    auto_fixes: list[str] = []
    if created_env:
        auto_fixes.append(f"created runtime env: {env_path}")
    if created_reports:
        auto_fixes.append(f"created reports dir: {reports_dir}")
    return BootstrapResult(
        bootstrap_applied=created_env or created_reports,
        created_env=created_env,
        created_reports=created_reports,
        auto_fixes=auto_fixes,
    )


def _has_complete_app_credentials(app_id: str, app_secret: str) -> bool:
    return bool(app_id.strip() and app_secret.strip())


def _has_partial_app_credentials(app_id: str, app_secret: str) -> bool:
    return bool(app_id.strip()) ^ bool(app_secret.strip())


def _resolve_named_target(item: dict[str, Any], default: FeishuTargetConfig) -> FeishuTargetConfig:
    name = str(item.get("name", "")).strip().lower()
    app_token = str(item.get("app_token", "")).strip()
    own_bot_token = str(item.get("bot_token", "")).strip()
    own_app_id = str(item.get("app_id", "")).strip()
    own_app_secret = str(item.get("app_secret", "")).strip()
    if _has_partial_app_credentials(own_app_id, own_app_secret):
        raise ValueError(f"feishu[{name}]: APP_ID and APP_SECRET must be set together")
    bot_token = own_bot_token or default.bot_token.strip()
    app_id = own_app_id or default.app_id.strip()
    app_secret = own_app_secret or default.app_secret.strip()
    return FeishuTargetConfig(
        name=name,
        app_token=app_token,
        table_id=str(item.get("table_id", "")).strip(),
        app_id=app_id,
        app_secret=app_secret,
        bot_token=bot_token,
        inherited_auth=not bool(own_bot_token or _has_complete_app_credentials(own_app_id, own_app_secret)),
    )


def validate_feishu_targets(
    *,
    basic: dict[str, Any],
    feishu_default: dict[str, Any],
    feishu_targets: list[dict[str, Any]],
    mode: str,
    selected_feishu_targets: Optional[list[str]] = None,
    all_feishu_targets: bool = False,
) -> PreflightResult:
    _ = basic
    errors: list[str] = []
    resolved_targets: list[FeishuTargetConfig] = []
    selected_names = {str(name).strip().lower() for name in (selected_feishu_targets or []) if str(name).strip()}
    explicit_named_selection = mode == "execution" and (all_feishu_targets or bool(selected_names))
    default_app_token = str(feishu_default.get("FEISHU_APP_TOKEN", "")).strip()
    default_bot_token = str(feishu_default.get("FEISHU_BOT_TOKEN", "")).strip()
    default_app_id = str(feishu_default.get("FEISHU_APP_ID", "")).strip()
    default_app_secret = str(feishu_default.get("FEISHU_APP_SECRET", "")).strip()
    default = FeishuTargetConfig(
        name="default",
        app_token=default_app_token,
        table_id=str(feishu_default.get("FEISHU_TABLE_ID", "")).strip(),
        app_id=default_app_id,
        app_secret=default_app_secret,
        bot_token=default_bot_token,
    )

    if not explicit_named_selection and not default_app_token:
        errors.append("feishu[default]: default target is required")
    if default_app_token and not default_bot_token and not (default_app_id and default_app_secret):
        errors.append("feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET")
    if default_app_token and (all_feishu_targets or not explicit_named_selection):
        resolved_targets.append(default)

    items_to_validate = feishu_targets
    if mode == "execution" and not all_feishu_targets:
        if selected_names:
            items_to_validate = [
                item for item in feishu_targets if str(item.get("name", "")).strip().lower() in selected_names
            ]
        else:
            items_to_validate = []

    for item in items_to_validate:
        name = str(item.get("name", "")).strip().lower()
        app_token = str(item.get("app_token", "")).strip()
        if not app_token:
            errors.append(f"feishu[{name}]: missing APP_TOKEN")
            continue
        try:
            resolved_targets.append(_resolve_named_target(item, default))
        except ValueError as exc:
            errors.append(str(exc))

    return PreflightResult(ok=not errors, errors=errors, resolved_feishu_targets=resolved_targets)


def validate_basic_config(
    *,
    basic: dict[str, Any],
    is_interactive_tty: bool = False,
) -> PreflightResult:
    errors: list[str] = []
    org_username = str(basic.get("ORG_USERNAME", "")).strip()
    hash_salt = str(basic.get("HASH_SALT", "")).strip()
    if not org_username and not is_interactive_tty:
        errors.append("missing ORG_USERNAME (set in .env or run in interactive terminal)")
    if not hash_salt:
        errors.append("missing HASH_SALT (set in .env)")
    return PreflightResult(ok=not errors, errors=errors)


def validate_runtime_config(
    *,
    basic: dict[str, Any],
    feishu_default: dict[str, Any],
    feishu_targets: list[dict[str, Any]],
    mode: str,
    selected_feishu_targets: Optional[list[str]] = None,
    all_feishu_targets: bool = False,
    is_interactive_tty: bool = False,
    skip_feishu: bool = False,
) -> PreflightResult:
    basic_result = validate_basic_config(basic=basic, is_interactive_tty=is_interactive_tty)
    warnings = list(basic_result.warnings)
    errors = list(basic_result.errors)

    if skip_feishu:
        return PreflightResult(ok=not errors, errors=errors, warnings=warnings)

    result = validate_feishu_targets(
        basic=basic,
        feishu_default=feishu_default,
        feishu_targets=feishu_targets,
        mode=mode,
        selected_feishu_targets=selected_feishu_targets,
        all_feishu_targets=all_feishu_targets,
    )
    warnings.extend(result.warnings)
    errors.extend(result.errors)
    selected_names = {str(name).strip().lower() for name in (selected_feishu_targets or []) if str(name).strip()}

    default_app_token = str(feishu_default.get("FEISHU_APP_TOKEN", "")).strip()
    default_table_id = str(feishu_default.get("FEISHU_TABLE_ID", "")).strip()
    default_app_id = str(feishu_default.get("FEISHU_APP_ID", "")).strip()
    default_app_secret = str(feishu_default.get("FEISHU_APP_SECRET", "")).strip()

    if _has_partial_app_credentials(default_app_id, default_app_secret):
        errors.append("feishu[default]: APP_ID and APP_SECRET must be set together")
    if default_app_token and not default_table_id:
        warnings.append("feishu[default]: TABLE_ID is empty; first table will be auto-selected")

    items_to_validate = feishu_targets
    if mode == "execution" and not all_feishu_targets:
        if selected_names:
            items_to_validate = [
                item for item in feishu_targets if str(item.get("name", "")).strip().lower() in selected_names
            ]
        else:
            items_to_validate = []

    for item in items_to_validate:
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue
        table_id = str(item.get("table_id", "")).strip()
        app_id = str(item.get("app_id", "")).strip()
        app_secret = str(item.get("app_secret", "")).strip()
        if _has_partial_app_credentials(app_id, app_secret):
            message = f"feishu[{name}]: APP_ID and APP_SECRET must be set together"
            if message not in errors:
                errors.append(message)
        if str(item.get("app_token", "")).strip() and not table_id:
            warnings.append(f"feishu[{name}]: TABLE_ID is empty; first table will be auto-selected")

    return PreflightResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        auto_fixes=list(result.auto_fixes),
        bootstrap_applied=result.bootstrap_applied,
        resolved_feishu_targets=list(result.resolved_feishu_targets),
    )
