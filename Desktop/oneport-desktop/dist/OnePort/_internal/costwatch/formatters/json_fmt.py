"""JSON formatter — machine-parseable output for CI and dashboards."""

from __future__ import annotations

from costwatch.formatters.base import Formatter
from costwatch.result import CostReport


class JsonFormatter(Formatter):
    def format(self, report: CostReport) -> str:
        return report.to_json()
