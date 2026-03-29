from llm_usage.models import AggregateRecord
from llm_usage.reporting import print_terminal_report, write_csv_report


def _row(
    *,
    date_local: str = "2026-03-29",
    user_hash: str = "user-hash",
    source_host_hash: str = "",
    tool: str = "codex",
    model: str = "gpt-5",
    input_tokens_sum: int = 0,
    cache_tokens_sum: int = 0,
    output_tokens_sum: int = 0,
    row_key: str = "row-key",
    updated_at: str = "2026-03-29T00:00:00+08:00",
) -> AggregateRecord:
    return AggregateRecord(
        date_local=date_local,
        user_hash=user_hash,
        source_host_hash=source_host_hash,
        tool=tool,
        model=model,
        input_tokens_sum=input_tokens_sum,
        cache_tokens_sum=cache_tokens_sum,
        output_tokens_sum=output_tokens_sum,
        row_key=row_key,
        updated_at=updated_at,
    )


def test_print_terminal_report_groups_rows_by_day_tool_model(capsys):
    rows = [
        _row(
            source_host_hash="source-a",
            input_tokens_sum=10,
            cache_tokens_sum=2,
            output_tokens_sum=3,
            row_key="row-a",
        ),
        _row(
            source_host_hash="source-b",
            input_tokens_sum=5,
            cache_tokens_sum=7,
            output_tokens_sum=11,
            row_key="row-b",
        ),
    ]

    print_terminal_report(rows)

    captured = capsys.readouterr().out.strip().splitlines()
    assert "日期" in captured[0]
    assert "工具" in captured[0]
    assert "模型" in captured[0]
    assert "输入" in captured[0]
    assert "缓存" in captured[0]
    assert "输出" in captured[0]
    assert "来源" not in captured[0]
    assert len(captured) == 3
    assert "2026-03-29" in captured[2]
    assert "codex" in captured[2]
    assert "gpt-5" in captured[2]
    assert "15" in captured[2]
    assert "9" in captured[2]
    assert "14" in captured[2]
    assert "source-a" not in captured[2]
    assert "source-b" not in captured[2]


def test_write_csv_report_keeps_original_rows(tmp_path):
    rows = [
        _row(source_host_hash="source-a", input_tokens_sum=10, row_key="row-a"),
        _row(source_host_hash="source-b", input_tokens_sum=5, row_key="row-b"),
    ]

    path = write_csv_report(rows, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "source-a" in text
    assert "source-b" in text
    assert "row-a" in text
    assert "row-b" in text
