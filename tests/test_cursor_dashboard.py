from datetime import datetime, timezone

from llm_usage.collectors.cursor import build_cursor_collector
from llm_usage.collectors.cursor_dashboard import CursorDashboardCollector
from llm_usage.collectors.file_collector import FileCollector
from llm_usage.collectors import cursor_dashboard


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self) -> dict:
        return self._payload


def test_build_cursor_collector_defaults_to_file_collector(monkeypatch):
    monkeypatch.delenv("CURSOR_WEB_SESSION_TOKEN", raising=False)
    collector = build_cursor_collector()
    assert isinstance(collector, FileCollector)
    assert collector.name == "cursor"


def test_build_cursor_collector_uses_dashboard_collector(monkeypatch):
    monkeypatch.setenv("CURSOR_WEB_SESSION_TOKEN", "token-abc")
    collector = build_cursor_collector()
    assert isinstance(collector, CursorDashboardCollector)
    assert collector.name == "cursor"


def test_cursor_dashboard_collect_paginates_and_maps_tokens(monkeypatch):
    calls: list[int] = []
    origins: list[str] = []
    base_ts = int(datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    def _fake_post(url, headers, cookies, json, timeout):  # noqa: ANN001, ANN201
        calls.append(json["page"])
        origins.append(headers.get("Origin", ""))
        if json["page"] == 1:
            return _Resp(
                {
                    "totalUsageEventsCount": 3,
                    "usageEventsDisplay": [
                        {
                            "timestamp": str(base_ts),
                            "model": "gpt-5",
                            "tokenUsage": {
                                "inputTokens": 100,
                                "outputTokens": 20,
                                "cacheReadTokens": 5,
                                "cacheWriteTokens": 2,
                            },
                        },
                        {
                            "timestamp": str(base_ts + 1000),
                            "model": "claude-4",
                            "tokenUsage": {
                                "inputTokens": 10,
                                "outputTokens": 1,
                                "cacheReadTokens": 0,
                                "cacheWriteTokens": 0,
                            },
                        },
                    ],
                }
            )

        return _Resp(
            {
                "totalUsageEventsCount": 3,
                "usageEventsDisplay": [
                    {
                        "timestamp": str(base_ts + 2000),
                        "model": "gpt-4.1",
                        "tokenUsage": {
                            "inputTokens": 30,
                            "outputTokens": 6,
                            "cacheReadTokens": 3,
                            "cacheWriteTokens": 1,
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(cursor_dashboard.requests, "post", _fake_post)

    collector = CursorDashboardCollector(session_token="token", page_size=2)
    out = collector.collect(
        start=datetime(2026, 3, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )

    assert calls == [1, 2]
    assert origins and all(x == "https://cursor.com" for x in origins)
    assert not out.warnings
    assert len(out.events) == 3
    assert out.events[0].input_tokens == 100
    assert out.events[0].cache_tokens == 7
    assert out.events[0].output_tokens == 20
    assert out.events[2].model == "gpt-4.1"


def test_cursor_dashboard_probe_returns_false_on_auth_error(monkeypatch):
    def _fake_post(url, headers, cookies, json, timeout):  # noqa: ANN001, ANN201
        return _Resp({}, status_code=401)

    monkeypatch.setattr(cursor_dashboard.requests, "post", _fake_post)
    collector = CursorDashboardCollector(session_token="expired-token")
    ok, msg = collector.probe()
    assert ok is False
    assert "authentication failed" in msg
