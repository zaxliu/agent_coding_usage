from pathlib import Path

import pytest

from llm_usage.env import (
    EnvDocument,
    load_dotenv,
    load_env_document,
    save_env_document,
    split_csv_env,
    upsert_env_var,
)


def test_load_env_document_preserves_raw_round_trip_and_parses_values(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# Identity\nORG_USERNAME='alice'\n   \n# Extra\nCUSTOM_FLAG=1 # comment\n",
        encoding="utf-8",
    )

    document = load_env_document(env_path)

    assert document.get("ORG_USERNAME") == "alice"
    assert document.get("CUSTOM_FLAG") == "1"
    assert document.render() == (
        "# Identity\nORG_USERNAME='alice'\n   \n# Extra\nCUSTOM_FLAG=1 # comment\n"
    )


def test_load_env_document_preserves_crlf_newlines(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"ORG_USERNAME=alice\r\nCUSTOM_FLAG=1\r\n")

    document = load_env_document(env_path)

    assert document.render() == "ORG_USERNAME=alice\r\nCUSTOM_FLAG=1\r\n"
    save_env_document(env_path, document)
    assert env_path.read_bytes() == b"ORG_USERNAME=alice\r\nCUSTOM_FLAG=1\r\n"


def test_load_env_document_preserves_missing_trailing_newline(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\nCUSTOM_FLAG=1", encoding="utf-8")

    document = load_env_document(env_path)

    assert document.render() == "ORG_USERNAME=alice\nCUSTOM_FLAG=1"
    save_env_document(env_path, document)
    assert env_path.read_bytes() == b"ORG_USERNAME=alice\nCUSTOM_FLAG=1"


def test_env_document_set_updates_known_key_and_keeps_unknown_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME='alice'\n   \nCUSTOM_FLAG=1 # comment\n",
        encoding="utf-8",
    )
    document = load_env_document(env_path)

    document.set("ORG_USERNAME", "bob")
    document.set("LOOKBACK_DAYS", "30")
    save_env_document(env_path, document)

    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=bob" in text
    assert "CUSTOM_FLAG=1 # comment" in text
    assert "LOOKBACK_DAYS=30" in text
    assert "   \n" in text


def test_env_document_delete_removes_key_without_touching_other_lines(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=alice\n   \nCUSTOM_FLAG=1 # comment\n",
        encoding="utf-8",
    )
    document = load_env_document(env_path)

    document.delete("ORG_USERNAME")
    save_env_document(env_path, document)

    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=alice" not in text
    assert "CUSTOM_FLAG=1 # comment" in text
    assert "   \n" in text


def test_env_document_rejects_multiline_values(tmp_path: Path):
    document = EnvDocument(lines=[])

    with pytest.raises(ValueError):
        document.set("BAD", "line1\nline2")

    env_path = tmp_path / ".env"
    env_path.write_text("GOOD=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        upsert_env_var(env_path, "BAD", "line1\nline2")


def test_env_document_quotes_unsafe_values_on_save():
    document = EnvDocument(lines=[])

    document.set("SPACED", "two words")
    document.set("HASHED", "a # b")

    assert document.render() == 'SPACED="two words"\nHASHED="a # b"\n'


def test_quoted_values_with_escapes_round_trip(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        r'QUOTED="a \"quote\" and \\slash"' "\n",
        encoding="utf-8",
    )

    document = load_env_document(env_path)

    assert document.get("QUOTED") == 'a "quote" and \\slash'
    save_env_document(env_path, document)
    assert env_path.read_text(encoding="utf-8") == r'QUOTED="a \"quote\" and \\slash"' "\n"


def test_quoted_windows_path_round_trip(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(r'PATH_VALUE="C:\temp\bin"' "\n", encoding="utf-8")

    document = load_env_document(env_path)

    assert document.get("PATH_VALUE") == r"C:\temp\bin"
    save_env_document(env_path, document)
    assert env_path.read_text(encoding="utf-8") == r'PATH_VALUE="C:\temp\bin"' "\n"


def test_existing_helpers_keep_working(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CSV=a,b,c\n", encoding="utf-8")

    load_dotenv(env_path)
    monkeypatch.setenv("CSV", "x, y , ,z")

    assert split_csv_env("CSV", ["default"]) == ["x", "y", "z"]

    upsert_env_var(env_path, "NEW_KEY", "value")
    assert env_path.read_text(encoding="utf-8") == "CSV=a,b,c\nNEW_KEY=value\n"


def test_env_document_round_trip_from_empty_file(tmp_path: Path):
    env_path = tmp_path / ".env"

    document = load_env_document(env_path)
    document.set("A", "1")
    document.set("B", "2")
    save_env_document(env_path, document)

    assert env_path.read_text(encoding="utf-8") == "A=1\nB=2\n"
    assert isinstance(document, EnvDocument)
