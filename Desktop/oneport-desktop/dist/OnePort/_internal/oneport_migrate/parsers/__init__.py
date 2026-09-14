"""
Framework detection and parser dispatch.

A file is a migration if:
  - it ends in .sql                                   → sql
  - it imports django.db migrations                   → django
  - it imports alembic op                             → alembic
Anything else returns None from detect_framework and is skipped.
"""

from __future__ import annotations

from oneport_migrate.operations import ParsedMigration
from oneport_migrate.parsers.alembic_parser import parse_alembic_migration
from oneport_migrate.parsers.django_parser import parse_django_migration
from oneport_migrate.parsers.sql_parser import parse_sql_migration


def detect_framework(path: str, content: str) -> str | None:
    if path.lower().endswith(".sql"):
        return "sql"
    if not path.lower().endswith(".py"):
        return None
    if "from django.db import migrations" in content or "django.db.migrations" in content:
        return "django"
    if "from alembic import op" in content or "import alembic" in content:
        return "alembic"
    return None


def parse_migration(path: str, content: str, framework: str | None = None) -> ParsedMigration | None:
    """Parse one file into a ParsedMigration, or None if it isn't a migration."""
    framework = framework or detect_framework(path, content)
    if framework == "sql":
        return parse_sql_migration(path, content)
    if framework == "django":
        return parse_django_migration(path, content)
    if framework == "alembic":
        return parse_alembic_migration(path, content)
    return None


__all__ = [
    "detect_framework",
    "parse_migration",
    "parse_django_migration",
    "parse_alembic_migration",
    "parse_sql_migration",
]
