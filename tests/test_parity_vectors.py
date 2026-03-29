import json
from datetime import datetime, timezone
from pathlib import Path

from llm_usage.aggregation import aggregate_events
from llm_usage.identity import build_row_key, hash_source_host, hash_user
from llm_usage.models import UsageEvent
from llm_usage.privacy import to_feishu_fields


def _load_vector(name: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    path = root / "spec" / "parity-vectors" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_hash_vectors_match_shared_spec():
    vectors = _load_vector("hash_vectors.json")

    for item in vectors["userHashes"]:
        assert hash_user(item["username"], item["salt"]) == item["expected"]

    for item in vectors["sourceHostHashes"]:
        assert hash_source_host(item["username"], item["sourceLabel"], item["salt"]) == item["expected"]

    for item in vectors["rowKeys"]:
        assert (
            build_row_key(
                item["userHash"],
                item["sourceHostHash"],
                item["dateLocal"],
                item["tool"],
                item["model"],
                session_fingerprint=item["sessionFingerprint"],
            )
            == item["expected"]
        )


def test_aggregation_vectors_match_shared_spec():
    vectors = _load_vector("aggregation_vectors.json")

    events = [
        UsageEvent(
            tool=item["tool"],
            model=item["model"],
            event_time=datetime.fromisoformat(item["eventTime"].replace("Z", "+00:00")),
            input_tokens=item["inputTokens"],
            cache_tokens=item["cacheTokens"],
            output_tokens=item["outputTokens"],
            session_fingerprint=item.get("sessionFingerprint"),
            source_host_hash=item.get("sourceHostHash", ""),
        )
        for item in vectors["events"]
    ]

    rows = aggregate_events(
        events,
        user_hash=vectors["userHash"],
        timezone_name=vectors["timeZone"],
        now=datetime.fromisoformat(vectors["now"].replace("Z", "+00:00")).astimezone(timezone.utc),
    )

    assert [row.__dict__ for row in rows] == vectors["expectedRows"]
    assert to_feishu_fields(rows[0]) == {
        key: vectors["expectedRows"][0][key]
        for key in (
            "date_local",
            "user_hash",
            "source_host_hash",
            "tool",
            "model",
            "input_tokens_sum",
            "cache_tokens_sum",
            "output_tokens_sum",
            "row_key",
            "updated_at",
        )
    }
