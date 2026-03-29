from io import StringIO

from llm_usage.models import AggregateRecord
from llm_usage.sinks.feishu_bitable import FeishuBitableClient, UploadProgress


def test_normalize_datetime_value_from_iso():
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")
    ms = client._normalize_datetime_value("2026-03-13T17:30:00+08:00")
    assert isinstance(ms, int)
    assert ms > 0


def test_normalize_datetime_value_from_date():
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")
    ms = client._normalize_datetime_value("2026-03-13")
    assert isinstance(ms, int)
    assert ms > 0


def test_normalize_fields_only_converts_datetime_type():
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")
    fields = {"date_local": "2026-03-13", "tool": "codex"}
    out = client._normalize_fields_for_table(fields, {"date_local": 5, "tool": 1})
    assert isinstance(out["date_local"], int)
    assert out["tool"] == "codex"


class _TTYStringIO(StringIO):
    def isatty(self):  # noqa: ANN201
        return True


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self) -> dict:
        return self._payload


def test_upload_progress_renders_on_tty():
    stream = _TTYStringIO()
    progress = UploadProgress(total=2, stream=stream, enabled=True)
    progress.advance(created=1, updated=0, failed=0)
    progress.advance(created=1, updated=1, failed=0)
    progress.finish()
    text = stream.getvalue()
    assert "飞书上传" in text
    assert "2/2" in text


def test_upsert_preserves_result_counts(monkeypatch):
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")
    monkeypatch.setattr(client, "fetch_existing_row_keys", lambda: {"key-2": "rec-2"})
    monkeypatch.setattr(
        client,
        "_fetch_field_type_map",
        lambda: {
            "date_local": 1,
            "user_hash": 1,
            "source_host_hash": 1,
            "tool": 1,
            "model": 1,
            "input_tokens_sum": 2,
            "cache_tokens_sum": 2,
            "output_tokens_sum": 2,
            "row_key": 1,
            "updated_at": 1,
        },
    )
    requests = []

    def _fake_request(method, url, **kwargs):  # noqa: ANN001, ANN201
        requests.append((method, url))
        if url.endswith("/batch_create"):
            return {"data": {"records": [{"record_id": "rec-1"}]}}
        return {}

    monkeypatch.setattr(client, "_request", _fake_request)
    rows = [
        AggregateRecord(
            date_local="2026-03-08",
            user_hash="u",
            source_host_hash="s",
            tool="codex",
            model="m",
            input_tokens_sum=1,
            cache_tokens_sum=0,
            output_tokens_sum=1,
            row_key="key-1",
            updated_at="2026-03-08T00:00:00+00:00",
        ),
        AggregateRecord(
            date_local="2026-03-08",
            user_hash="u",
            source_host_hash="s",
            tool="codex",
            model="m",
            input_tokens_sum=1,
            cache_tokens_sum=0,
            output_tokens_sum=1,
            row_key="key-2",
            updated_at="2026-03-08T00:00:00+00:00",
        ),
    ]

    result = client.upsert(rows)

    assert result.created == 1
    assert result.updated == 1
    assert result.failed == 0
    assert result.error_samples == []
    assert result.warning_samples == []
    assert requests[0][1].endswith("/batch_create")
    assert requests[1][1].endswith("/batch_update")


def test_request_prefers_feishu_json_error_over_generic_http_error(monkeypatch):
    client = FeishuBitableClient(app_token="a", table_id="t", bot_token="x")

    def _fake_request(method, url, headers, timeout, **kwargs):  # noqa: ANN001, ANN201
        return _Resp(
            {"code": 91403, "msg": "Forbidden: no permission to write record"},
            status_code=403,
        )

    monkeypatch.setattr("llm_usage.sinks.feishu_bitable.requests.request", _fake_request)
    try:
        client._request("POST", "https://example.test")
    except RuntimeError as exc:
        text = str(exc)
        assert "feishu api http error" in text
        assert "no permission" in text
        assert "hint=" in text
    else:
        raise AssertionError("expected RuntimeError")
