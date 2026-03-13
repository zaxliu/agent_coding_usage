from __future__ import annotations

from datetime import datetime
from glob import glob
from pathlib import Path

from .base import BaseCollector, CollectOutput
from .parsing import read_events_from_file


class FileCollector(BaseCollector):
    def __init__(self, name: str, patterns: list[str]) -> None:
        self.name = name
        self.patterns = patterns

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
        return True, f"{len(files)} files detected"

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
