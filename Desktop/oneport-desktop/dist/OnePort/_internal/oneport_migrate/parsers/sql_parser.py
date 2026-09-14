"""
Raw SQL migration parser — regex-based, deliberately simple.

HONEST LIMITS (also documented in the README):
  - Statements are split on ";" without full string-literal awareness. A
    semicolon inside a string literal or a $$-quoted function body will split
    a statement early and may misclassify it (a note is attached when $$ is
    seen).
  - Dynamic SQL (EXECUTE format(...), DO blocks) is not analyzed.
  - Only the statement shapes listed below are recognised; anything else is
    ignored rather than guessed at.

The upside: zero dependencies, predictable behaviour, and every miss is a
false NEGATIVE (we stay quiet), never an invented finding.
"""

from __future__ import annotations

import re

from oneport_migrate.operations import MigrationOp, OpKind, ParsedMigration

_COMMENT_LINE_RE = re.compile(r"--.*?$", re.MULTILINE)
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_DROP_TABLE_RE = re.compile(
    r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<t>[\w\".]+)", re.IGNORECASE)
_TRUNCATE_RE = re.compile(
    r"^\s*TRUNCATE\s+(?:TABLE\s+)?(?P<t>[\w\".]+)", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<t>[\w\".]+)", re.IGNORECASE)
_CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?P<conc>CONCURRENTLY\s+)?"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w\"]+)?\s*ON\s+(?:ONLY\s+)?(?P<t>[\w\".]+)",
    re.IGNORECASE)
_ALTER_TABLE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<t>[\w\".]+)\s+(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL)
_DELETE_RE = re.compile(r"^\s*DELETE\s+FROM\s+(?P<t>[\w\".]+)", re.IGNORECASE)
_UPDATE_RE = re.compile(r"^\s*UPDATE\s+(?:ONLY\s+)?(?P<t>[\w\".]+)", re.IGNORECASE)
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(?P<t>[\w\".]+)", re.IGNORECASE)
_BEGIN_RE = re.compile(r"^\s*(BEGIN|START\s+TRANSACTION)\b", re.IGNORECASE)

# ALTER TABLE sub-actions (searched inside the "rest" of the statement).
_DROP_COLUMN_RE = re.compile(
    r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(?P<c>[\w\"]+)", re.IGNORECASE)
_ADD_COLUMN_RE = re.compile(
    r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(?P<c>[\w\"]+)\s+(?P<def>[^,]+)",
    re.IGNORECASE)
_ALTER_TYPE_RE = re.compile(
    r"ALTER\s+(?:COLUMN\s+)?(?P<c>[\w\"]+)\s+(?:SET\s+DATA\s+)?TYPE\s+", re.IGNORECASE)
_MODIFY_RE = re.compile(  # MySQL
    r"MODIFY\s+(?:COLUMN\s+)?(?P<c>[\w\"]+)\s+", re.IGNORECASE)
_SET_NOT_NULL_RE = re.compile(
    r"ALTER\s+(?:COLUMN\s+)?(?P<c>[\w\"]+)\s+SET\s+NOT\s+NULL", re.IGNORECASE)
_RENAME_COLUMN_RE = re.compile(
    r"RENAME\s+(?:COLUMN\s+)?(?P<c>[\w\"]+)\s+TO\s+(?P<n>[\w\"]+)", re.IGNORECASE)
_RENAME_TABLE_RE = re.compile(r"RENAME\s+TO\s+(?P<n>[\w\"]+)", re.IGNORECASE)

# Keywords that make an ADD COLUMN definition NOT NULL / defaulted.
_NOT_NULL_KW = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
_DEFAULT_KW = re.compile(r"\bDEFAULT\b", re.IGNORECASE)
# ADD CONSTRAINT / ADD PRIMARY KEY etc. must not be misread as ADD COLUMN.
_ADD_NON_COLUMN_KW = re.compile(
    r"^(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK|INDEX|KEY)\b", re.IGNORECASE)


def _clean(name: str) -> str:
    return name.strip().strip('"').strip("`")


def parse_sql_migration(
    path: str, content: str, framework: str = "sql", line_offset: int = 0
) -> ParsedMigration:
    """
    Parse a raw SQL migration into normalized ops.

    Args:
        path:        File path for finding locations.
        content:     The SQL text.
        framework:   Reported framework ("sql", or the host framework when
                     called for Django RunSQL / Alembic op.execute strings).
        line_offset: Added to every op's line (host-file position of the SQL).
    """
    m = ParsedMigration(file=path, framework=framework, source=content)

    if "$$" in content:
        m.notes.append(
            f"{path}: contains $$-quoted blocks — regex SQL parsing may split or "
            "misread statements inside them."
        )

    stripped = _COMMENT_BLOCK_RE.sub(lambda s: " " * len(s.group()), content)
    stripped = _COMMENT_LINE_RE.sub("", stripped)

    for stmt, line in _split_statements(stripped):
        _classify(stmt, line + line_offset, path, framework, m)

    return m


def _split_statements(sql: str) -> list[tuple[str, int]]:
    """Split on ';', tracking the 1-based line each statement starts on."""
    statements: list[tuple[str, int]] = []
    buf: list[str] = []
    start_line = 1
    line = 1
    for ch in sql:
        if ch == ";":
            text = "".join(buf).strip()
            if text:
                statements.append((text, start_line))
            buf = []
            start_line = line
        else:
            if ch == "\n":
                line += 1
            if not buf:
                if ch.strip():
                    start_line = line
                    buf.append(ch)
            else:
                buf.append(ch)
    text = "".join(buf).strip()
    if text:
        statements.append((text, start_line))
    return statements


def _classify(
    stmt: str, line: int, path: str, framework: str, m: ParsedMigration
) -> None:
    snippet = " ".join(stmt.split())[:160]

    def add(kind: OpKind, table: str = "", column: str = "", **details) -> None:
        m.ops.append(MigrationOp(
            kind=kind, file=path, framework=framework, line=line,
            table=_clean(table), column=_clean(column), snippet=snippet,
            details=details,
        ))

    if _BEGIN_RE.match(stmt):
        m.atomic = True
        return

    if match := _DROP_TABLE_RE.match(stmt):
        add(OpKind.DROP_TABLE, table=match.group("t"))
        return

    if match := _TRUNCATE_RE.match(stmt):
        add(OpKind.TRUNCATE, table=match.group("t"))
        return

    if match := _CREATE_TABLE_RE.match(stmt):
        add(OpKind.CREATE_TABLE, table=match.group("t"))
        return

    if match := _CREATE_INDEX_RE.match(stmt):
        add(OpKind.CREATE_INDEX, table=match.group("t"),
            concurrently=bool(match.group("conc")),
            unique=bool(match.group("unique")))
        return

    if match := _ALTER_TABLE_RE.match(stmt):
        _classify_alter(match.group("t"), match.group("rest"), add)
        return

    if match := _DELETE_RE.match(stmt):
        if re.search(r"\bWHERE\b", stmt, re.IGNORECASE):
            add(OpKind.DATA_MIGRATION, table=match.group("t"), dml="delete")
        else:
            add(OpKind.TRUNCATE, table=match.group("t"), destructive_delete=True)
        return

    if match := _UPDATE_RE.match(stmt):
        add(OpKind.DATA_MIGRATION, table=match.group("t"), dml="update")
        return

    if match := _INSERT_RE.match(stmt):
        add(OpKind.DATA_MIGRATION, table=match.group("t"), dml="insert")
        return

    # Unrecognised statement — stay quiet (documented false-negative policy),
    # but keep DDL-looking statements visible to the LLM as RAW_SQL.
    if re.match(r"^\s*(ALTER|CREATE|DROP)\b", stmt, re.IGNORECASE):
        add(OpKind.RAW_SQL)


def _classify_alter(table: str, rest: str, add) -> None:
    """Handle the (possibly comma-separated) actions of one ALTER TABLE."""
    matched = False

    for match in _DROP_COLUMN_RE.finditer(rest):
        add(OpKind.DROP_COLUMN, table=table, column=match.group("c"))
        matched = True

    for match in _SET_NOT_NULL_RE.finditer(rest):
        add(OpKind.SET_NOT_NULL, table=table, column=match.group("c"))
        matched = True

    for match in _ALTER_TYPE_RE.finditer(rest):
        # Skip if this span was already consumed by SET NOT NULL (different verbs,
        # can't overlap in practice — ALTER col TYPE vs ALTER col SET NOT NULL).
        add(OpKind.ALTER_COLUMN, table=table, column=match.group("c"), type_changed=True)
        matched = True

    for match in _MODIFY_RE.finditer(rest):
        add(OpKind.ALTER_COLUMN, table=table, column=match.group("c"), type_changed=True)
        matched = True

    for match in _RENAME_COLUMN_RE.finditer(rest):
        add(OpKind.RENAME_COLUMN, table=table, column=match.group("c"),
            new_name=_clean(match.group("n")))
        matched = True

    if match := _RENAME_TABLE_RE.search(rest):
        # "RENAME TO" without "COLUMN x TO" — table rename.
        if not _RENAME_COLUMN_RE.search(rest):
            add(OpKind.RENAME_TABLE, table=table, new_name=_clean(match.group("n")))
            matched = True

    for match in _ADD_COLUMN_RE.finditer(rest):
        candidate = match.group("c")
        if _ADD_NON_COLUMN_KW.match(candidate):
            continue  # ADD CONSTRAINT / ADD PRIMARY KEY — not a column add
        definition = match.group("def")
        add(OpKind.ADD_COLUMN, table=table, column=candidate,
            nullable=not bool(_NOT_NULL_KW.search(definition)),
            has_default=bool(_DEFAULT_KW.search(definition)))
        matched = True

    if not matched:
        add(OpKind.RAW_SQL, table=table)
