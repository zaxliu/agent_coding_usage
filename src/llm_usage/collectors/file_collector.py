from __future__ import annotations

from datetime import datetime
from glob import glob
from pathlib import Path

from .base import BaseCollector, CollectOutput
from .parsing import read_events_from_file


class FileCollector(BaseCollector):
    def __init__(
        self,
        name: str,
        patterns: list[str],
        source_name: str = "local",
        source_host_hash: str = "",
    ) -> None:
        self.name = name
        self.patterns = patterns
        self.source_name = source_name
        self.source_host_hash = source_host_hash

    def _matched_files(self) -> list[Path]:
        files: list[Path] = []
        for pattern in self.patterns:
            files.extend(Path(p) for p in glob(str(Path(pattern).expanduser()), recursive=True))
        deduped = sorted(
            {
                p
                for p in files
                if p.is_file()
                and p.suffix.lower() in {".json", ".jsonl"}
                and not _is_noise_path(p)
            }
        )
        return deduped

    def probe(self) -> tuple[bool, str]:
        files = self._matched_files()
        if not files:
            return False, f"no data files found for {self.name}"
        parsable_events = 0
        parse_warnings: list[str] = []
        for path in files:
            parsed, warning = read_events_from_file(path, self.name)
            if warning:
                parse_warnings.append(warning)
                continue
            parsable_events += len(parsed)

        message = f"{len(files)} files detected, {parsable_events} parsable events"
        if parse_warnings:
            message += f", {len(parse_warnings)} parse warnings"
            message += f" (first: {_shorten_warning(parse_warnings[0])})"
        return parsable_events > 0, message

    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        events = []
        warnings: list[str] = []
        for path in self._matched_files():
            parsed, warning = read_events_from_file(path, self.name)
            if warning:
                warnings.append(warning)
                continue
            for event in parsed:
                if start <= event.event_time <= end:
                    events.append(event)
        if not events:
            warnings.append(f"{self.name}: no usage events in selected time range")
        return CollectOutput(events=events, warnings=warnings)


def _is_noise_path(path: Path) -> bool:
    noise_parts = {
        "extensions",
        "node_modules",
        ".git",
        ".cache",
        "Cache",
        "__pycache__",
    }
    return any(part in noise_parts for part in path.parts)


def _shorten_warning(warning: str, limit: int = 120) -> str:
    text = " ".join(warning.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
