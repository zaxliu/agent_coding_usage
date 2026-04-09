from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

import llm_usage.main as main
from llm_usage.interaction import run_config_editor
from llm_usage.feishu_targets import resolve_feishu_targets_from_env


class _TTYStringIO(StringIO):
    def isatty(self):  # noqa: ANN201
        return True


_VALID_DEFAULT_TARGET = "FEISHU_APP_TOKEN=default-app\nFEISHU_BOT_TOKEN=default-bot\n"


def _env_map(path: Path) -> dict[str, str]:
    from llm_usage.env import load_env_document

    doc = load_env_document(path)
    return {line.key: line.value or "" for line in doc.lines if line.kind == "entry" and line.key}


def test_save_rewrites_feishu_targets_and_prefixed_keys_deterministically(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=alice\n"
        "HASH_SALT=salt\n"
        "FEISHU_APP_TOKEN=legacy-app\n"
        "FEISHU_BOT_TOKEN=legacy-bot\n"
        "FEISHU_TARGETS=beta,alpha\n"
        "FEISHU_BETA_APP_TOKEN=beta-tok\n"
        "FEISHU_ALPHA_APP_TOKEN=alpha-tok\n",
        encoding="utf-8",
    )
    # Main -> Feishu -> Named -> delete first (beta) -> back -> back -> save
    user_input = "\n".join(
        [
            "2",
            "2",
            "d",
            "1",
            "b",
            "b",
            "s",
        ]
    ) + "\n"
    exit_code = run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=_TTYStringIO())
    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_TARGETS=alpha" in text.replace(" ", "")
    assert "FEISHU_ALPHA_APP_TOKEN=alpha-tok" in text
    assert "FEISHU_BETA_APP_TOKEN" not in text
    m = _env_map(env_path)
    assert m["FEISHU_APP_TOKEN"] == "legacy-app"
    assert m["FEISHU_BOT_TOKEN"] == "legacy-bot"
    targets = resolve_feishu_targets_from_env(m)
    assert [t.name for t in targets] == ["default", "alpha"]


def test_interactive_default_feishu_menu_still_edits_legacy_keys(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ORG_USERNAME=alice\n"
        "HASH_SALT=salt\n"
        "FEISHU_APP_TOKEN=old-token\n"
        "FEISHU_BOT_TOKEN=default-bot\n",
        encoding="utf-8",
    )
    # Feishu -> Default (legacy) -> first key -> new value -> back -> save from main menu
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n1\n1\nnew-token\nb\ns\n"),
        stdout=_TTYStringIO(),
    )
    assert exit_code == 0
    assert "FEISHU_APP_TOKEN=new-token" in env_path.read_text(encoding="utf-8")


def test_interactive_feishu_submenu_save_rejects_invalid_default_target(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    stdout = _TTYStringIO()
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n1\n1\nnew-token\nb\ns\nb\nd\n"),
        stdout=stdout,
    )
    assert exit_code == 0
    assert "feishu[default]: missing BOT_TOKEN or APP_ID+APP_SECRET" in stdout.getvalue()
    assert "Saved.\n" not in stdout.getvalue()
    assert "FEISHU_APP_TOKEN=new-token" not in env_path.read_text(encoding="utf-8")


def test_interactive_add_named_target_rejects_reserved_default(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_TARGET + "ORG_USERNAME=u\n", encoding="utf-8")
    out = _TTYStringIO()
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n2\na\ndefault\nb\nq\nd\n"),
        stdout=out,
    )
    assert exit_code == 0
    assert "feishu target name" in out.getvalue().lower() or "reserved" in out.getvalue().lower()
    assert "FEISHU_TARGETS" not in env_path.read_text(encoding="utf-8")


def test_interactive_add_named_target_rejects_invalid_name(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_TARGET + "ORG_USERNAME=u\n", encoding="utf-8")
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n2\na\nBad-Name\nb\nq\nd\n"),
        stdout=_TTYStringIO(),
    )
    assert exit_code == 0
    assert "FEISHU_TARGETS" not in env_path.read_text(encoding="utf-8")


def test_interactive_add_duplicate_named_target_is_rejected(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_TARGET +
        "FEISHU_TARGETS=alpha\n"
        "FEISHU_ALPHA_APP_TOKEN=t\n",
        encoding="utf-8",
    )
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n2\na\nalpha\nb\nq\nd\n"),
        stdout=_TTYStringIO(),
    )
    assert exit_code == 0


def test_interactive_named_target_edit_accepts_target_name(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _VALID_DEFAULT_TARGET
        + "ORG_USERNAME=u\n"
        + "HASH_SALT=salt\n"
        +
        "FEISHU_TARGETS=myself\n"
        "FEISHU_MYSELF_APP_TOKEN=\n",
        encoding="utf-8",
    )
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n2\ne\nmyself\n1\nnamed-token\nb\nb\nb\ns\n"),
        stdout=_TTYStringIO(),
    )
    assert exit_code == 0
    m = _env_map(env_path)
    assert m["FEISHU_MYSELF_APP_TOKEN"] == "named-token"


def test_interactive_add_named_target_enters_detail_immediately(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_VALID_DEFAULT_TARGET + "ORG_USERNAME=u\nHASH_SALT=salt\n", encoding="utf-8")
    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("2\n2\na\nteam_b\n1\nteam-token\nb\nb\nb\ns\n"),
        stdout=_TTYStringIO(),
    )
    assert exit_code == 0
    m = _env_map(env_path)
    assert m["FEISHU_TARGETS"] == "team_b"
    assert m["FEISHU_TEAM_B_APP_TOKEN"] == "team-token"


def test_save_preserves_named_target_keys_when_feishu_targets_list_is_invalid(tmp_path: Path):
    env_path = tmp_path / ".env"
    original = (
        _VALID_DEFAULT_TARGET
        +
        "ORG_USERNAME=alice\n"
        "HASH_SALT=salt\n"
        "FEISHU_TARGETS=alpha,Alpha\n"
        "FEISHU_ALPHA_APP_TOKEN=alpha-token\n"
        "FEISHU_ALPHA_TABLE_ID=alpha-table\n"
    )
    env_path.write_text(original, encoding="utf-8")

    exit_code = run_config_editor(
        env_path=env_path,
        stdin=_TTYStringIO("1\n1\nbob\ns\n"),
        stdout=_TTYStringIO(),
    )

    assert exit_code == 0
    text = env_path.read_text(encoding="utf-8")
    assert "ORG_USERNAME=bob" in text
    assert "FEISHU_TARGETS=alpha,Alpha" in text
    assert "FEISHU_ALPHA_APP_TOKEN=alpha-token" in text
    assert "FEISHU_ALPHA_TABLE_ID=alpha-table" in text


def test_cli_list_feishu_targets(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEISHU_APP_TOKEN=a\n"
        "FEISHU_TARGETS=team_b\n"
        "FEISHU_TEAM_B_APP_TOKEN=tb\n",
        encoding="utf-8",
    )
    parser = main.build_parser()
    args = parser.parse_args(["config", "--list-feishu-targets"])
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    rc = main.cmd_config(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "default" in out
    assert "team_b" in out


def test_cli_show_feishu_target(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEISHU_TARGETS=z\n"
        "FEISHU_Z_APP_TOKEN=zapp\n"
        "FEISHU_Z_TABLE_ID=tblz\n",
        encoding="utf-8",
    )
    parser = main.build_parser()
    args = parser.parse_args(["config", "--show-feishu-target", "z"])
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    rc = main.cmd_config(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "zapp" in out
    assert "tblz" in out


def test_cli_add_and_delete_feishu_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
    parser = main.build_parser()
    monkeypatch.setattr(main, "_env_path", lambda: env_path)

    args_add = parser.parse_args(["config", "--add-feishu-target", "new_tgt"])
    assert main.cmd_config(args_add) == 0
    m = _env_map(env_path)
    assert "new_tgt" in m.get("FEISHU_TARGETS", "")
    assert "FEISHU_NEW_TGT_APP_TOKEN" in m

    args_del = parser.parse_args(["config", "--delete-feishu-target", "new_tgt"])
    assert main.cmd_config(args_del) == 0
    m2 = _env_map(env_path)
    assert "new_tgt" not in (m2.get("FEISHU_TARGETS") or "")
    assert "FEISHU_NEW_TGT_APP_TOKEN" not in m2


def test_cli_set_feishu_target_updates_prefixed_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEISHU_TARGETS=x\n"
        "FEISHU_X_APP_TOKEN=old\n",
        encoding="utf-8",
    )
    parser = main.build_parser()
    args = parser.parse_args(
        [
            "config",
            "--set-feishu-target",
            "x",
            "--app-token",
            "newtok",
            "--table-id",
            "tbl1",
        ]
    )
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    assert main.cmd_config(args) == 0
    m = _env_map(env_path)
    assert m["FEISHU_X_APP_TOKEN"] == "newtok"
    assert m["FEISHU_X_TABLE_ID"] == "tbl1"


def test_cli_rejects_conflicting_shortcuts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=u\n", encoding="utf-8")
    parser = main.build_parser()
    args = parser.parse_args(["config", "--list-feishu-targets", "--add-feishu-target", "x"])
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    rc = main.cmd_config(args)
    assert rc != 0


def test_cli_set_default_without_fields_does_not_rewrite_named_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    env_path = tmp_path / ".env"
    original = (
        "FEISHU_TARGETS=alpha,Alpha\n"
        "FEISHU_ALPHA_APP_TOKEN=alpha-token\n"
        "FEISHU_ALPHA_TABLE_ID=alpha-table\n"
    )
    env_path.write_text(original, encoding="utf-8")
    parser = main.build_parser()
    args = parser.parse_args(["config", "--set-feishu-target", "default"])
    monkeypatch.setattr(main, "_env_path", lambda: env_path)

    rc = main.cmd_config(args)

    assert rc != 0
    assert env_path.read_text(encoding="utf-8") == original
    assert "no feishu fields specified" in capsys.readouterr().out.lower()


def test_plain_config_still_runs_interactive_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORG_USERNAME=alice\n", encoding="utf-8")
    parser = main.build_parser()
    args = parser.parse_args(["config"])
    monkeypatch.setattr(main, "_env_path", lambda: env_path)
    monkeypatch.setattr("llm_usage.interaction.run_config_editor", lambda env_path, stdin=None, stdout=None: 0)
    rc = main.cmd_config(args)
    assert rc == 0


def test_config_editor_discards_dirty_state_on_eof_after_failed_save(tmp_path: Path):
    class _BoundedTTYStringIO(StringIO):
        def __init__(self, limit: int = 12000):
            super().__init__()
            self.limit = limit

        def isatty(self):  # noqa: ANN201
            return True

        def write(self, text: str) -> int:
            if self.tell() + len(text) > self.limit:
                raise AssertionError("stdout grew unexpectedly after EOF")
            return super().write(text)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEISHU_APP_TOKEN=legacy-app\n"
        "FEISHU_BOT_TOKEN=legacy-bot\n"
        "FEISHU_TARGETS=beta,alpha\n"
        "FEISHU_BETA_APP_TOKEN=beta-tok\n"
        "FEISHU_ALPHA_APP_TOKEN=alpha-tok\n",
        encoding="utf-8",
    )
    user_input = "\n".join(["2", "2", "d", "1", "b", "b", "s"]) + "\n"
    stdout = _BoundedTTYStringIO()

    exit_code = run_config_editor(env_path=env_path, stdin=_TTYStringIO(user_input), stdout=stdout)

    assert exit_code == 0
    text = stdout.getvalue()
    assert "missing ORG_USERNAME" in text
    assert "missing HASH_SALT" in text
    assert env_path.read_text(encoding="utf-8").count("FEISHU_TARGETS=beta,alpha") == 1
