from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from llm_usage.models import UsageEvent


@dataclass
class CollectOutput:
    events: list[UsageEvent]
    warnings: list[str]


class BaseCollector(ABC):
    name: str

    @abstractmethod
    def probe(self) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def collect(self, start: datetime, end: datetime) -> CollectOutput:
        raise NotImplementedError
