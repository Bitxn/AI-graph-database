"""
Machine-readable JSON formatter.
"""

from oneport.formatters.base import Formatter
from oneport.result import ReviewResult


class JsonFormatter(Formatter):
    """Serialises ReviewResult to pretty-printed JSON."""

    def format(self, result: ReviewResult) -> str:
        return result.to_json(indent=2)
