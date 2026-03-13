from llm_usage.models import AggregateRecord
from llm_usage.privacy import to_feishu_fields


def test_privacy_whitelist_only():
    row = AggregateRecord(
        date_local="2026-03-08",
        user_hash="hash",
        tool="codex",
        model="gpt-5",
        input_tokens_sum=10,
        cache_tokens_sum=1,
        output_tokens_sum=2,
        row_key="key",
        updated_at="2026-03-08T10:00:00+08:00",
    )
    fields = to_feishu_fields(row)
    assert set(fields.keys()) == {
        "date_local",
        "user_hash",
        "tool",
        "model",
        "input_tokens_sum",
        "cache_tokens_sum",
        "output_tokens_sum",
        "row_key",
        "updated_at",
    }
