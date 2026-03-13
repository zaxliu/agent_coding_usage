from llm_usage.sinks.feishu_bitable import FeishuBitableClient


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
