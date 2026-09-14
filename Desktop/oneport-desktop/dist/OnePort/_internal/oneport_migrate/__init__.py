"""
Oneport Migrate — the database-migration safety gate for Python teams.

Deterministic rule engine (Django + Alembic + raw SQL) plus LLM blast-radius
judgment. The LLM never decides WHETHER an operation is dangerous — only how
dangerous it is in your repo.
"""

__version__ = "0.2.0"

from oneport_migrate.result import CheckResult, Finding, Location, Severity

__all__ = ["CheckResult", "Finding", "Location", "Severity", "__version__"]
