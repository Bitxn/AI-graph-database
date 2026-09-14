"""Output formatters for ReviewResult."""

from oneport.formatters.base import Formatter
from oneport.formatters.inline import InlineFormatter
from oneport.formatters.json_fmt import JsonFormatter
from oneport.formatters.github_fmt import GitHubFormatter
from oneport.formatters.sarif import SarifFormatter

__all__ = [
    "Formatter",
    "InlineFormatter",
    "JsonFormatter",
    "GitHubFormatter",
    "SarifFormatter",
]

REGISTRY: dict[str, type[Formatter]] = {
    "inline": InlineFormatter,
    "json": JsonFormatter,
    "github": GitHubFormatter,
    "sarif": SarifFormatter,
}


def get_formatter(name: str) -> Formatter:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown formatter '{name}'. Choose from: {list(REGISTRY)}")
    return cls()
