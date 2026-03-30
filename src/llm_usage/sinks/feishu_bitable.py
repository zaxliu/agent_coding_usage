from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import requests

from llm_usage.models import AggregateRecord
from llm_usage.privacy import to_feishu_fields


def _format_feishu_api_error(payload: dict, *, context: str) -> str:
    parts = [context, f"code={payload.get('code')}"]
    for key in ("msg", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}={value.strip()}")
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "msg"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(f"error.{key}={value.strip()}")
                break

    text = " | ".join(parts)
    lowered = text.lower()
    if any(
        token in lowered
        for token in ("permission", "forbidden", "无权限", "没有权限", "access denied", "auth scope")
    ):
        text += (
            " | hint=飞书开放平台的应用接口权限不能替代表格协作权限；"
            "请确认该应用或其运行身份对目标多维表格/数据表仍有可编辑权限。"
            "仅将分享权限改为“组织内可阅读”通常不足以写入。"
        )
    return text


def _maybe_json(resp: requests.Response) -> Optional[dict]:
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@dataclass
class SyncResult:
    created: int
    updated: int
    failed: int
    error_samples: list[str]
    warning_samples: list[str]


class UploadProgress:
    def __init__(self, total: int, stream=None, enabled: bool = True) -> None:
        self.total = max(0, total)
        self.stream = stream or sys.stdout
        self.enabled = enabled and self.total > 0 and getattr(self.stream, "isatty", lambda: False)()
        self.current = 0

    def advance(self, created: int, updated: int, failed: int) -> None:
        if not self.enabled:
            return
        self.current += 1
        width = 24
        filled = int(width * self.current / max(1, self.total))
        bar = "#" * filled + "-" * (width - filled)
        self.stream.write(
            f"\r飞书上传 [{bar}] {self.current}/{self.total} "
            f"新增:{created} 更新:{updated} 失败:{failed}"
        )
        self.stream.flush()

    def finish(self) -> None:
        if not self.enabled:
            return
        self.stream.write("\n")
        self.stream.flush()


def fetch_tenant_access_token(
    app_id: str,
    app_secret: str,
    request_timeout_sec: int = 20,
) -> str:
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=request_timeout_sec,
    )
    payload = _maybe_json(resp)
    if resp.status_code >= 400:
        if payload is not None:
            raise RuntimeError(_format_feishu_api_error(payload, context="feishu auth http error"))
        resp.raise_for_status()
    if payload is None:
        resp.raise_for_status()
        raise RuntimeError("feishu auth response is not json")
    if payload.get("code", 0) != 0:
        raise RuntimeError(_format_feishu_api_error(payload, context="feishu auth error"))
    token = payload.get("tenant_access_token")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError(f"feishu auth token missing: {payload}")
    return token


def fetch_first_table_id(
    app_token: str,
    bot_token: str,
    request_timeout_sec: int = 20,
) -> str:
    resp = requests.get(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables",
        headers={"Authorization": f"Bearer {bot_token}"},
        params={"page_size": 1},
        timeout=request_timeout_sec,
    )
    payload = _maybe_json(resp)
    if resp.status_code >= 400:
        if payload is not None:
            raise RuntimeError(_format_feishu_api_error(payload, context="feishu list tables http error"))
        resp.raise_for_status()
    if payload is None:
        resp.raise_for_status()
        raise RuntimeError("feishu list tables response is not json")
    if payload.get("code", 0) != 0:
        raise RuntimeError(_format_feishu_api_error(payload, context="feishu list tables error"))
    items = payload.get("data", {}).get("items", [])
    if not items:
        raise RuntimeError("feishu table list is empty")
    table_id = items[0].get("table_id")
    if not isinstance(table_id, str) or not table_id.strip():
        raise RuntimeError(f"feishu table id missing: {payload}")
    return table_id


class FeishuBitableClient:
    def __init__(
        self,
        app_token: str,
        table_id: str,
        bot_token: str,
        request_timeout_sec: int = 20,
    ) -> None:
        self.app_token = app_token
        self.table_id = table_id
        self.bot_token = bot_token
        self.request_timeout_sec = request_timeout_sec
        self.base_url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"
        )
        self.batch_size = 100

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs) -> dict:
        backoff = 0.8
        for _ in range(4):
            resp = requests.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.request_timeout_sec,
                **kwargs,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            payload = _maybe_json(resp)
            if resp.status_code >= 400:
                if payload is not None:
                    raise RuntimeError(
                        _format_feishu_api_error(
                            payload,
                            context=f"feishu api http error: {method} {url}",
                        )
                    )
                resp.raise_for_status()
            if payload is None:
                resp.raise_for_status()
                raise RuntimeError(f"feishu api response is not json: {method} {url}")
            if payload.get("code", 0) != 0:
                raise RuntimeError(_format_feishu_api_error(payload, context=f"feishu api error: {method} {url}"))
            return payload
        raise RuntimeError("feishu api retry exhausted")

    def fetch_existing_row_keys(self) -> dict[str, str]:
        row_key_to_record_id: dict[str, str] = {}
        page_token = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            payload = self._request("GET", self.base_url, params=params)
            data = payload.get("data", {})
            for item in data.get("items", []):
                fields = item.get("fields", {})
                row_key = fields.get("row_key")
                record_id = item.get("record_id")
                if isinstance(row_key, str) and isinstance(record_id, str):
                    row_key_to_record_id[row_key] = record_id
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break
        return row_key_to_record_id

    def _fetch_field_type_map(self) -> dict[str, int]:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields"
        out: dict[str, int] = {}
        page_token: Optional[str] = None
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            payload = self._request("GET", url, params=params)
            data = payload.get("data", {})
            for item in data.get("items", []):
                name = item.get("field_name")
                field_type = item.get("type")
                if isinstance(name, str) and isinstance(field_type, int):
                    out[name] = field_type
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not isinstance(page_token, str) or not page_token:
                break
        return out

    def _normalize_datetime_value(self, value: object) -> object:
        if isinstance(value, (int, float)):
            return int(value if value > 10_000_000_000 else value * 1000)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return value
            try:
                dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
            try:
                d = date.fromisoformat(candidate)
                dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                return value
        return value

    def _normalize_fields_for_table(
        self,
        fields: dict[str, object],
        field_type_map: dict[str, int],
    ) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in fields.items():
            # Feishu Bitable field type 5 means DateTime and requires unix ms.
            if field_type_map.get(key) == 5:
                out[key] = self._normalize_datetime_value(value)
            else:
                out[key] = value
        return out

    def _filter_fields_for_table(
        self,
        fields: dict[str, object],
        field_type_map: dict[str, int],
    ) -> tuple[dict[str, object], list[str]]:
        filtered = {key: value for key, value in fields.items() if key in field_type_map}
        missing = [key for key in fields if key not in field_type_map]
        return filtered, missing

    def _chunks(self, items: list[dict[str, object]]) -> list[list[dict[str, object]]]:
        return [items[idx : idx + self.batch_size] for idx in range(0, len(items), self.batch_size)]

    def upsert(self, rows: list[AggregateRecord]) -> SyncResult:
        existing = self.fetch_existing_row_keys()
        field_type_map = self._fetch_field_type_map()
        created = 0
        updated = 0
        failed = 0
        error_samples: list[str] = []
        warning_samples: list[str] = []
        progress = UploadProgress(total=len(rows))
        create_records: list[dict[str, object]] = []
        update_records: list[dict[str, object]] = []
        warned_missing_fields: set[str] = set()

        for row in rows:
            raw_fields = to_feishu_fields(row)
            filtered_fields, missing_fields = self._filter_fields_for_table(raw_fields, field_type_map)
            fields = self._normalize_fields_for_table(filtered_fields, field_type_map)
            for field_name in missing_fields:
                if field_name not in warned_missing_fields and len(warning_samples) < 5:
                    warning_samples.append(f"飞书表缺少字段，已跳过：{field_name}")
                warned_missing_fields.add(field_name)
            record_id = existing.get(row.row_key)
            if record_id:
                update_records.append({"record_id": record_id, "fields": fields, "__row_key": row.row_key})
            else:
                create_records.append({"fields": fields, "__row_key": row.row_key})

        try:
            for batch in self._chunks(create_records):
                try:
                    payload = {"records": [{"fields": item["fields"]} for item in batch]}
                    resp = self._request("POST", f"{self.base_url}/batch_create", json=payload)
                    items = resp.get("data", {}).get("records", [])
                    created += len(batch)
                    for source_item, record_item in zip(batch, items):
                        rid = record_item.get("record_id")
                        if isinstance(rid, str):
                            existing[str(source_item["__row_key"])] = rid
                except Exception as exc:
                    failed += len(batch)
                    if len(error_samples) < 5:
                        error_samples.append(f"批量创建失败 batch={len(batch)}: {exc}")
                for _ in batch:
                    progress.advance(created=created, updated=updated, failed=failed)

            for batch in self._chunks(update_records):
                try:
                    payload = {
                        "records": [
                            {"record_id": item["record_id"], "fields": item["fields"]}
                            for item in batch
                        ]
                    }
                    self._request("POST", f"{self.base_url}/batch_update", json=payload)
                    updated += len(batch)
                except Exception as exc:
                    failed += len(batch)
                    if len(error_samples) < 5:
                        error_samples.append(f"批量更新失败 batch={len(batch)}: {exc}")
                for _ in batch:
                    progress.advance(created=created, updated=updated, failed=failed)
        finally:
            progress.finish()
        return SyncResult(
            created=created,
            updated=updated,
            failed=failed,
            error_samples=error_samples,
            warning_samples=warning_samples,
        )
