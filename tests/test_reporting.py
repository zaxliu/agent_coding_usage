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


def test_print_terminal_report_keeps_hosts_separate_and_renders_labels(capsys):
    rows = [
        _row(source_host_hash="local-hash", input_tokens_sum=10, cache_tokens_sum=2, output_tokens_sum=3),
        _row(source_host_hash="remote-hash", input_tokens_sum=5, cache_tokens_sum=7, output_tokens_sum=11),
    ]

    print_terminal_report(
        rows,
        host_labels={"local-hash": "local", "remote-hash": "alice@host-a"},
    )

    captured = capsys.readouterr().out.strip().splitlines()
    assert "Host" in captured[0]
    assert len(captured) == 4
    assert "local" in captured[2]
    assert "alice@host-a" in captured[3]
    assert "10" in captured[2]
    assert "5" in captured[3]


def test_print_terminal_report_shows_local_for_empty_source_host_hash(capsys):
    print_terminal_report([_row(source_host_hash="")], host_labels={})

    captured = capsys.readouterr().out.strip().splitlines()
    assert len(captured) == 3
    row_cells = captured[2].split(" | ")
    assert row_cells[1].strip() == "local"


def test_print_terminal_report_uses_hash_prefix_for_unknown_hosts(capsys):
    print_terminal_report([_row(source_host_hash="abcdef1234567890")], host_labels={})

    captured = capsys.readouterr().out.strip().splitlines()
    assert "abcdef12" in captured[2]


def test_print_terminal_report_sizes_host_column_from_rendered_content(capsys):
    rows = [
        _row(source_host_hash="short-hash", tool="codex", model="gpt-5"),
        _row(source_host_hash="long-hash", tool="codex", model="gpt-5"),
    ]

    print_terminal_report(
        rows,
        host_labels={
            "short-hash": "local",
            "long-hash": "very-long-host-label@example.internal",
        },
    )

    header, divider, first_row, second_row = capsys.readouterr().out.strip().splitlines()
    assert len(header.split(" | ")) == 7
    assert len(divider.split("-+-")) == 7
    assert len(first_row.split(" | ")) == 7
    assert len(second_row.split(" | ")) == 7
    # Sorted by (date, source_host_hash, tool, model); long-hash < short-hash lexicographically.
    assert "very-long-host-label@example.internal" in first_row
    assert "local" in second_row


def test_print_terminal_report_merges_duplicate_host_tool_model_rows(capsys):
    rows = [
        _row(source_host_hash="remote-hash", input_tokens_sum=10, cache_tokens_sum=2, output_tokens_sum=3),
        _row(source_host_hash="remote-hash", input_tokens_sum=5, cache_tokens_sum=7, output_tokens_sum=11),
    ]

    print_terminal_report(
        rows,
        host_labels={"remote-hash": "alice@host-a"},
    )

    captured = capsys.readouterr().out.strip().splitlines()
    assert len(captured) == 3
    assert "alice@host-a" in captured[2]
    assert "15" in captured[2]
    assert "9" in captured[2]
    assert "14" in captured[2]


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
