from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional


@dataclass
class EnvLine:
    kind: str
    raw: str = ""
    key: Optional[str] = None
    value: Optional[str] = None

    def render(self) -> str:
        return self.raw


@dataclass
class EnvDocument:
    lines: list[EnvLine]
    newline: str = "\n"
    trailing_newline: bool = True

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        key = key.strip()
        for line in self.lines:
            if line.kind == "entry" and line.key == key:
                return line.value
        return default

    def set(self, key: str, value: str) -> None:
        key = key.strip()
        if not key:
            raise ValueError("env key cannot be empty")
        if "\n" in value or "\r" in value:
            raise ValueError("env values cannot contain newlines")

        new_line = EnvLine(
            kind="entry",
            raw=f"{key}={_render_env_value(value)}",
            key=key,
            value=value,
        )
        first_index: Optional[int] = None
        retained: list[EnvLine] = []
        for line in self.lines:
            if line.kind == "entry" and line.key == key:
                if first_index is None:
                    first_index = len(retained)
                    retained.append(new_line)
                continue
            retained.append(line)

        if first_index is None:
            retained.append(new_line)

        self.lines = retained

    def delete(self, key: str) -> None:
        key = key.strip()
        self.lines = [
            line
            for line in self.lines
            if line.kind != "entry" or line.key != key
        ]

    def render(self) -> str:
        if not self.lines:
            return ""
        rendered = self.newline.join(line.render() for line in self.lines)
        return rendered + (self.newline if self.trailing_newline else "")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.rstrip()


def _parse_env_value(value: str) -> str:
    candidate = _strip_inline_comment(value.lstrip())
    candidate = candidate.strip()
    stripped = _strip_quotes(candidate)
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] == '"':
        return _unescape_double_quoted_value(stripped)
    return stripped


def _unescape_double_quoted_value(value: str) -> str:
    out: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            if char in {'\\', '"'}:
                out.append(char)
            else:
                out.append('\\')
                out.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        out.append(char)
    if escaped:
        out.append("\\")
    return "".join(out)


def _render_env_value(value: str) -> str:
    if value == "":
        return '""'

    needs_quotes = any(
        char.isspace() or char in {'#', '"', "'"} for char in value
    )
    if not needs_quotes:
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_env_line(raw: str) -> EnvLine:
    stripped = raw.strip()
    if not stripped:
        return EnvLine(kind="blank", raw=raw)
    if stripped.startswith("#"):
        return EnvLine(kind="comment", raw=raw)
    if "=" not in raw:
        return EnvLine(kind="raw", raw=raw)

    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        return EnvLine(kind="raw", raw=raw)
    return EnvLine(kind="entry", raw=raw, key=key, value=_parse_env_value(value))


def _split_env_content(content: str) -> tuple[list[EnvLine], str, bool]:
    if not content:
        return [], "\n", True

    lines: list[str] = []
    newline = "\n"
    trailing_newline = False

    for index, segment in enumerate(content.splitlines(keepends=True)):
        if segment.endswith("\r\n"):
            separator = "\r\n"
            raw = segment[:-2]
            has_separator = True
        elif segment.endswith("\n") or segment.endswith("\r"):
            separator = segment[-1]
            raw = segment[:-1]
            has_separator = True
        else:
            separator = ""
            raw = segment
            has_separator = False

        if index == 0 and separator:
            newline = separator

        lines.append(_parse_env_line(raw))
        trailing_newline = has_separator

    return lines, newline, trailing_newline


def load_env_document(path: Path) -> EnvDocument:
    if not path.exists():
        return EnvDocument(lines=[])

    content = path.read_bytes().decode("utf-8")
    lines, newline, trailing_newline = _split_env_content(content)
    return EnvDocument(lines=lines, newline=newline, trailing_newline=trailing_newline)


def save_env_document(path: Path, document: EnvDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document.render().encode("utf-8"))


def load_dotenv(path: Path) -> None:
    document = load_env_document(path)
    for line in document.lines:
        if line.kind == "entry" and line.key is not None and line.value is not None:
            os.environ.setdefault(line.key, line.value)


def split_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def upsert_env_var(path: Path, key: str, value: str) -> None:
    document = load_env_document(path)
    document.set(key, value)
    save_env_document(path, document)
