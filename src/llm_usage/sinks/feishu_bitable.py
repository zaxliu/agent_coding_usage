from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from llm_usage.models import AggregateRecord
from llm_usage.privacy import to_feishu_fields


@dataclass
class SyncResult:
    created: int
    updated: int
    failed: int


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
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code", 0) != 0:
                raise RuntimeError(f"feishu api error: {payload}")
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

    def upsert(self, rows: list[AggregateRecord]) -> SyncResult:
        existing = self.fetch_existing_row_keys()
        created = 0
        updated = 0
        failed = 0

        for row in rows:
            fields = to_feishu_fields(row)
            payload = {"fields": fields}
            try:
                record_id = existing.get(row.row_key)
                if record_id:
                    self._request(
                        "PUT",
                        f"{self.base_url}/{record_id}",
                        json=payload,
                    )
                    updated += 1
                else:
                    resp = self._request("POST", self.base_url, json=payload)
                    created += 1
                    item = resp.get("data", {}).get("record", {})
                    rid = item.get("record_id")
                    if isinstance(rid, str):
                        existing[row.row_key] = rid
            except Exception:
                failed += 1
        return SyncResult(created=created, updated=updated, failed=failed)
