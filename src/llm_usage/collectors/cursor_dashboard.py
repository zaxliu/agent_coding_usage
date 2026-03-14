from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from llm_usage.models import UsageEvent

from .base import BaseCollector, CollectOutput


class CursorDashboardCollector(BaseCollector):
    name = "cursor"

    def __init__(
        self,
        session_token: str,
        workos_id: str = "",
        team_id: int = 0,
        page_size: int = 300,
        base_url: str = "https://cursor.com",
        timeout_sec: float = 15.0,
    ) -> None:
        self.session_token = session_token.strip()
        self.workos_id = workos_id.strip()
        self.team_id = team_id
        self.page_size = max(1, min(page_size, 300))
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = max(1.0, timeout_sec)
        self._request_mode: str | None = None

    def probe(self) -> tuple[bool, str]:
        if not self.session_token:
            return False, "CURSOR_WEB_SESSION_TOKEN is empty"
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=1)
        try:
            payload = self._request_page(start=start, end=end, page=1, page_size=1)
        except RuntimeError as exc:
            return False, str(exc)

        events = payload.get("usageEventsDisplay")
        if not isinstance(events, list):
            return False, "cursor dashboard API returned unexpected response shape"
        return True, "cursor dashboard API reachable"

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        warnings: list[str] = []
        try:
            raw_events = self._fetch_usage_events(start=start, end=end)
        except RuntimeError as exc:
            return CollectOutput(events=[], warnings=[f"cursor dashboard: {exc}"])

        events: list[UsageEvent] = []
        for idx, item in enumerate(raw_events, start=1):
            if not isinstance(item, dict):
                continue
            token_usage = item.get("tokenUsage")
            if not isinstance(token_usage, dict):
                continue

            input_tokens = _coerce_int(token_usage.get("inputTokens"))
            output_tokens = _coerce_int(token_usage.get("outputTokens"))
            cache_tokens = _coerce_int(token_usage.get("cacheReadTokens")) + _coerce_int(
                token_usage.get("cacheWriteTokens")
            )
            if input_tokens == 0 and output_tokens == 0 and cache_tokens == 0:
                continue

            event_time = _extract_time(item) or end
            if event_time < start or event_time > end:
                continue

            events.append(
                UsageEvent(
                    tool=self.name,
                    model=_extract_model(item),
                    event_time=event_time,
                    input_tokens=input_tokens,
                    cache_tokens=cache_tokens,
                    output_tokens=output_tokens,
                    source_ref=f"cursor_dashboard:{idx}",
                )
            )

        if not events:
            warnings.append("cursor: no usage events in selected time range")
        return CollectOutput(events=events, warnings=warnings)

    def _fetch_usage_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        page = 1
        all_events: list[dict[str, Any]] = []
        total_pages: int | None = None

        while True:
            payload = self._request_page(start=start, end=end, page=page, page_size=self.page_size)
            page_events = payload.get("usageEventsDisplay")
            if not isinstance(page_events, list):
                raise RuntimeError("cursor dashboard response missing usageEventsDisplay list")

            all_events.extend(item for item in page_events if isinstance(item, dict))

            if total_pages is None:
                total_count = _coerce_int(payload.get("totalUsageEventsCount"))
                if total_count > 0:
                    total_pages = max(1, math.ceil(total_count / self.page_size))

            if total_pages is not None:
                if page >= total_pages:
                    break
            else:
                if len(page_events) < self.page_size:
                    break

            page += 1

        return all_events

    def _request_page(
        self,
        start: datetime,
        end: datetime,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/dashboard/get-filtered-usage-events"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/dashboard/usage",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        cookies = {"WorkosCursorSessionToken": self.session_token}
        if self.workos_id:
            cookies["workos_id"] = self.workos_id

        auth_failures: list[tuple[str, int, str]] = []
        last_http_error: tuple[int, str] | None = None
        for mode, body in self._candidate_request_bodies(start=start, end=end, page=page, page_size=page_size):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    cookies=cookies,
                    json=body,
                    timeout=self.timeout_sec,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"request failed: {exc}") from exc

            if response.status_code in {401, 403}:
                auth_failures.append((mode, response.status_code, response.text[:200]))
                continue

            if response.status_code >= 400:
                last_http_error = (response.status_code, response.text[:200])
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("cursor dashboard returned non-JSON response") from exc

            if not isinstance(payload, dict):
                raise RuntimeError("cursor dashboard returned invalid JSON payload")

            self._request_mode = mode
            return payload

        if auth_failures:
            details = ", ".join(
                f"{mode}: {code} {snippet}".strip()
                for mode, code, snippet in auth_failures
                if snippet
            )
            if details:
                raise RuntimeError(f"authentication failed ({details})")
            raise RuntimeError("authentication failed (session cookie may be expired)")

        if last_http_error:
            code, text = last_http_error
            raise RuntimeError(f"http error {code}: {text}")

        raise RuntimeError("cursor dashboard request failed")

    def _candidate_request_bodies(
        self,
        start: datetime,
        end: datetime,
        page: int,
        page_size: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        start_ms = str(int(start.timestamp() * 1000))
        end_ms = str(int(end.timestamp() * 1000))

        team_body = {
            "teamId": self.team_id,
            "startDate": start_ms,
            "endDate": end_ms,
            "page": page,
            "pageSize": page_size,
        }
        personal_body = {
            "startDate": start_ms,
            "endDate": end_ms,
            "page": page,
            "pageSize": page_size,
        }

        if self._request_mode == "team":
            return [("team", team_body)]
        if self._request_mode == "personal":
            return [("personal", personal_body)]
        return [("team", team_body), ("personal", personal_body)]


def _coerce_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_time(raw: Any) -> datetime | None:
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 10_000_000_000:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        candidate = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _extract_time(node: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "createdAt", "created_at", "time", "eventTime", "date"):
        parsed = _parse_time(node.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_model(node: dict[str, Any]) -> str:
    for key in ("model", "modelName", "model_name"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"
