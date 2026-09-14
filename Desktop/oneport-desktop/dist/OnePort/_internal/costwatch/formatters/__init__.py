"""Output formatters for a CostReport."""

from __future__ import annotations

from costwatch.formatters.github_fmt import GitHubFormatter, build_pr_comment
from costwatch.formatters.inline import InlineFormatter
from costwatch.formatters.json_fmt import JsonFormatter
from costwatch.formatters.sarif import SarifFormatter

__all__ = [
    "GitHubFormatter",
    "InlineFormatter",
    "JsonFormatter",
    "SarifFormatter",
    "build_pr_comment",
    "get_formatter",
]

_FORMATTERS = {
    "inline": InlineFormatter,
    "json": JsonFormatter,
    "github": GitHubFormatter,
    "sarif": SarifFormatter,
}


def get_formatter(fmt: str):
    return _FORMATTERS.get(fmt, InlineFormatter)()
