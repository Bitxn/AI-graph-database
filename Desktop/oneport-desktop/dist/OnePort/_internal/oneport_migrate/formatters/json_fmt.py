"""JSON formatter — machine-readable output for CI pipelines and tooling."""

from __future__ import annotations

from oneport_migrate.result import CheckResult


class JsonFormatter:
    def format(self, result: CheckResult) -> str:
        return result.to_json()
