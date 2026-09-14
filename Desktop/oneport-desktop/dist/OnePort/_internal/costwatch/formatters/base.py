"""Formatter base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from costwatch.result import CostReport


class Formatter(ABC):
    @abstractmethod
    def format(self, report: CostReport) -> str:  # pragma: no cover - interface
        ...
