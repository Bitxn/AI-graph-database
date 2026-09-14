"""Abstract base class for all output formatters."""

from abc import ABC, abstractmethod

from oneport.result import ReviewResult


class Formatter(ABC):
    """
    Convert a ReviewResult into a string representation.

    Each concrete formatter targets a specific consumer:
      - InlineFormatter  → human reading terminal output
      - JsonFormatter    → scripts and custom tooling
      - GitHubFormatter  → GitHub Checks API / PR comments
      - SarifFormatter   → VS Code Problems panel / GitHub Code Scanning
    """

    @abstractmethod
    def format(self, result: ReviewResult) -> str:
        """Return the formatted string for the given result."""
        ...

    def __call__(self, result: ReviewResult) -> str:
        return self.format(result)
