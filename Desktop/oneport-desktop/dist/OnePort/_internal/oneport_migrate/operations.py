"""
The normalized operation model.

Every parser (Django AST, Alembic AST, raw SQL) reduces a migration file to a
list of MigrationOp — one flat vocabulary the rule engine matches against, so
rules are written once and apply to all three frameworks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OpKind(str, Enum):
    ADD_COLUMN = "add_column"
    DROP_COLUMN = "drop_column"
    DROP_TABLE = "drop_table"
    TRUNCATE = "truncate"            # TRUNCATE, or DELETE without WHERE
    ALTER_COLUMN = "alter_column"    # type / definition change
    SET_NOT_NULL = "set_not_null"    # NOT NULL added to an EXISTING column
    CREATE_INDEX = "create_index"
    CREATE_TABLE = "create_table"
    RENAME_COLUMN = "rename_column"
    RENAME_TABLE = "rename_table"
    DATA_MIGRATION = "data_migration"  # RunPython, UPDATE/INSERT/DELETE-with-WHERE, bulk_insert
    RAW_SQL = "raw_sql"              # SQL we could not statically classify
    OTHER = "other"


# Kinds that touch the schema (used for "data migration mixed with schema
# changes in one transaction" detection).
SCHEMA_KINDS = {
    OpKind.ADD_COLUMN,
    OpKind.DROP_COLUMN,
    OpKind.DROP_TABLE,
    OpKind.ALTER_COLUMN,
    OpKind.SET_NOT_NULL,
    OpKind.CREATE_INDEX,
    OpKind.CREATE_TABLE,
    OpKind.RENAME_COLUMN,
    OpKind.RENAME_TABLE,
}


@dataclass
class MigrationOp:
    """One normalized operation extracted from a migration file.

    Well-known `details` keys:
        nullable: bool        (ADD_COLUMN) column allows NULL
        has_default: bool     (ADD_COLUMN) a default/server_default is supplied
        concurrently: bool    (CREATE_INDEX) Postgres CONCURRENTLY requested
        unique: bool          (CREATE_INDEX)
        type_changed: bool    (ALTER_COLUMN) column type is (or may be) changing
        not_null: bool        (ALTER_COLUMN) new definition sets NOT NULL
        reversible: bool      (DATA_MIGRATION / RAW_SQL) reverse code/SQL provided
        destructive_delete: bool  (TRUNCATE) came from DELETE without WHERE
    """

    kind: OpKind
    file: str
    framework: str                   # django | alembic | sql
    line: int = 0
    table: str = ""
    column: str = ""
    snippet: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMigration:
    """A migration file reduced to normalized operations plus file-level facts."""

    file: str
    framework: str
    ops: list[MigrationOp] = field(default_factory=list)
    # True when the migration runs inside one transaction. Django: atomic=True
    # default; SQL: explicit BEGIN detected; Alembic: Postgres transactional
    # DDL default. Parsers set this as best they can and note assumptions.
    atomic: bool = True
    # False when the framework supports a reverse step and it is missing/empty
    # (Alembic downgrade). None = not applicable at file level (Django/SQL
    # reversibility is tracked per-op via details["reversible"]).
    has_reverse: bool | None = None
    # Honest parser limitations hit while reading this file — surfaced in output.
    notes: list[str] = field(default_factory=list)
    # Raw file content, passed to the LLM layer for repo-specific judgment.
    source: str = ""

    @property
    def created_tables(self) -> set[str]:
        """Tables created inside this same migration (indexing them is safe)."""
        return {op.table for op in self.ops if op.kind == OpKind.CREATE_TABLE}

    @property
    def has_schema_ops(self) -> bool:
        return any(op.kind in SCHEMA_KINDS for op in self.ops)

    @property
    def data_ops(self) -> list[MigrationOp]:
        return [op for op in self.ops if op.kind == OpKind.DATA_MIGRATION]
