"""
Migration registry — every supported upgrade target, keyed by id (e.g. "django:5.0").

Adding a migration is adding a module here; the scanner/codemod engine is generic.
"""

from __future__ import annotations

from oneport_upgrade.exceptions import UnknownMigration
from oneport_upgrade.rules.django import DJANGO_50
from oneport_upgrade.rules.pydantic import PYDANTIC_2
from oneport_upgrade.rules.python3 import PYTHON_312
from oneport_upgrade.rules.schema import Migration, Rule

MIGRATIONS: dict[str, Migration] = {
    m.id: m for m in (PYTHON_312, DJANGO_50, PYDANTIC_2)
}

# Convenience aliases so `--to django5` or `--to python` resolve to the canonical id.
_ALIASES = {
    "python": "python:3.12", "python3": "python:3.12", "py": "python:3.12",
    "python:3.12": "python:3.12", "python3.12": "python:3.12",
    "django": "django:5.0", "django5": "django:5.0", "django:5": "django:5.0",
    "pydantic": "pydantic:2", "pydantic2": "pydantic:2", "pydantic:v2": "pydantic:2",
}


def get_migration(target: str) -> Migration:
    key = _ALIASES.get(target.strip().lower(), target.strip().lower())
    migration = MIGRATIONS.get(key)
    if migration is None:
        raise UnknownMigration(
            f"Unknown migration target '{target}'. Available: "
            + ", ".join(sorted(MIGRATIONS)))
    return migration


def list_migrations() -> list[Migration]:
    return list(MIGRATIONS.values())


__all__ = ["MIGRATIONS", "Migration", "Rule", "get_migration", "list_migrations"]
