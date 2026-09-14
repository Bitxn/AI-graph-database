"""
The deterministic rule engine.

Matches normalized MigrationOps against the rule catalog. This layer alone
decides WHETHER an operation is dangerous — the LLM layer only judges how
dangerous it is in a specific repo, and may adjust severity solely on the
basis of explicit team guidelines.
"""

from __future__ import annotations

from oneport_migrate.operations import MigrationOp, OpKind, ParsedMigration
from oneport_migrate.result import Finding, Location, Severity
from oneport_migrate.rules.loader import RuleSet


def run_rules(
    migrations: list[ParsedMigration],
    rule_set: RuleSet,
    db: str = "postgres",
) -> list[Finding]:
    """Evaluate every parsed migration and return the full finding list,
    sorted by severity desc then file/line asc."""
    findings: list[Finding] = []
    for migration in migrations:
        findings.extend(_check_migration(migration, rule_set, db))
    findings.sort(key=lambda f: (-f.severity.rank, f.file, f.line))
    return findings


def _check_migration(
    m: ParsedMigration, rule_set: RuleSet, db: str
) -> list[Finding]:
    findings: list[Finding] = []

    def emit(rule_id: str, op_or_none: MigrationOp | None, message: str, suggestion: str,
             line: int | None = None, snippet: str = "") -> None:
        if rule_id in rule_set.ignored_ids:
            return
        rule = rule_set.get(rule_id)
        severity = Severity(rule_set.effective_severity(rule_id))
        findings.append(Finding(
            rule_id=rule_id,
            severity=severity,
            message=message,
            suggestion=suggestion,
            location=Location(
                file=m.file,
                line=line if line is not None else (op_or_none.line if op_or_none else 0),
            ),
            snippet=snippet or (op_or_none.snippet if op_or_none else ""),
            category=rule.category if rule else "",
            framework=m.framework,
            docs_url=rule.docs_url if rule else "",
        ))

    created = m.created_tables

    for op in m.ops:
        d = op.details
        label = _label(op)

        if op.kind == OpKind.DROP_COLUMN:
            emit("OPM001", op,
                 f"Dropping column {label} — destructive, data is unrecoverable.",
                 "Two-stage removal: 1) deploy code that no longer reads/writes the column; "
                 "2) drop it in a later release once verified unused. Archive the data first "
                 "if there is any chance you need it back.")

        elif op.kind == OpKind.DROP_TABLE:
            emit("OPM002", op,
                 f"Dropping table `{op.table}` — destructive, all rows are lost.",
                 "Archive/export the table first and confirm no code path still references it. "
                 "Consider renaming it (e.g. users_deprecated) for one release before dropping.")

        elif op.kind == OpKind.TRUNCATE:
            what = "DELETE without WHERE" if d.get("destructive_delete") else "TRUNCATE"
            emit("OPM003", op,
                 f"{what} on `{op.table}` wipes every row in the table.",
                 "If intentional, run it as an explicit reviewed runbook step, not an "
                 "automated migration. Otherwise add a WHERE clause / remove the statement.")

        elif op.kind == OpKind.ALTER_COLUMN:
            if d.get("type_changed"):
                emit("OPM010", op,
                     f"Altering column {label} — type/definition changes usually rewrite "
                     f"the whole table under an exclusive lock.",
                     "Expand-and-contract: add a new column with the new type, dual-write, "
                     "backfill in batches, switch reads, drop the old column in a later release.")
            if d.get("not_null"):
                emit("OPM013", op,
                     f"Altered definition of {label} sets NOT NULL on an existing column — "
                     f"full-table validation scan under lock; fails if any NULLs remain.",
                     "Backfill NULLs first. On Postgres 12+: ADD CONSTRAINT ... CHECK (col IS "
                     "NOT NULL) NOT VALID, then VALIDATE CONSTRAINT, then SET NOT NULL.")

        elif op.kind == OpKind.SET_NOT_NULL:
            emit("OPM013", op,
                 f"Adding NOT NULL to existing column {label} — full-table validation "
                 f"scan under lock; fails if any NULLs remain.",
                 "Backfill NULLs first. On Postgres 12+: ADD CONSTRAINT ... CHECK (col IS "
                 "NOT NULL) NOT VALID, then VALIDATE CONSTRAINT, then SET NOT NULL.")

        elif op.kind == OpKind.ADD_COLUMN:
            if not d.get("nullable", True) and not d.get("has_default", False):
                emit("OPM011", op,
                     f"Adding NOT NULL column {label} without a default — fails on any "
                     f"non-empty table (every existing row violates the constraint).",
                     "Add the column as nullable (or with a DEFAULT), backfill in batches, "
                     "then add the NOT NULL constraint as a separate step.")

        elif op.kind == OpKind.CREATE_INDEX:
            # Indexing a table created in this same migration is instant — skip.
            # CONCURRENTLY is Postgres-specific; MySQL/SQLite have different
            # online-DDL semantics, so this rule only fires for postgres.
            if db == "postgres" and not d.get("concurrently", False) and op.table not in created:
                emit("OPM012", op,
                     f"CREATE INDEX without CONCURRENTLY on `{op.table or 'existing table'}` — "
                     f"blocks all writes to the table until the index build finishes.",
                     "Use CREATE INDEX CONCURRENTLY. It must run outside a transaction: "
                     "Django — AddIndexConcurrently (django.contrib.postgres) with atomic=False; "
                     "Alembic — postgresql_concurrently=True inside op.get_context()."
                     "autocommit_block(); raw SQL — no BEGIN around the statement.")

        elif op.kind == OpKind.RENAME_COLUMN:
            emit("OPM030", op,
                 f"Renaming column {label} breaks rolling deploys — old and new code "
                 f"run simultaneously and one always uses the wrong name.",
                 "Expand-and-contract: add the new column, dual-write, backfill, switch "
                 "reads, then drop the old column in a later release.")

        elif op.kind == OpKind.RENAME_TABLE:
            emit("OPM031", op,
                 f"Renaming table `{op.table}` breaks rolling deploys.",
                 "Create a view or alias with the old name for the transition, or migrate "
                 "all access code before renaming.")

    # Reversibility. The `reversible` detail is set only by RunPython/RunSQL
    # (Django) and analyzed op.execute SQL — any op carrying reversible=False
    # means its source operation has no reverse step. Deduped per source line
    # so a multi-statement RunSQL warns once, not once per statement.
    warned_lines: set[tuple[str, int]] = set()
    for op in m.ops:
        if op.details.get("reversible") is not False:
            continue
        key = (op.file, op.line)
        if key in warned_lines:
            continue
        warned_lines.add(key)
        emit("OPM020", op,
             f"Operation at {op.file}:{op.line} has no reverse step — the deploy "
             f"cannot be rolled back past this migration.",
             "Provide reverse_code / reverse_sql. If reversal is genuinely impossible, "
             "make that explicit (RunPython.noop) so it is a reviewed decision.")

    # File-level reverse step (Alembic: empty or missing downgrade).
    if m.has_reverse is False:
        emit("OPM020", None,
             f"{m.file} has an empty or missing downgrade() — the deploy cannot be "
             f"rolled back past this migration.",
             "Implement downgrade() with the inverse operations. If reversal is genuinely "
             "impossible, raise an explicit exception so it is a reviewed decision.",
             line=0)

    # Data migration inside a schema transaction. Framework-specific trigger:
    #   django  — any data op while the migration is atomic (the default);
    #   alembic — data op mixed with schema ops (Postgres transactional DDL);
    #   sql     — data op mixed with DDL, or wrapped in an explicit BEGIN.
    data_ops = m.data_ops
    if data_ops:
        fire = False
        if m.framework == "django":
            fire = m.atomic
        elif m.framework == "alembic":
            fire = m.has_schema_ops
        elif m.framework == "sql":
            fire = m.has_schema_ops or m.atomic
        if fire:
            for op in data_ops:
                emit("OPM021", op,
                     "Data migration runs inside the schema transaction — locks and "
                     "transaction state are held for the whole backfill; on a large "
                     "table that is an outage.",
                     "Move the backfill to its own migration with the transaction disabled "
                     "(Django: atomic = False; Alembic: run in an autocommit block; SQL: "
                     "outside BEGIN/COMMIT) and write in batches.")

    return findings


def _label(op: MigrationOp) -> str:
    if op.table and op.column:
        return f"`{op.table}.{op.column}`"
    if op.column:
        return f"`{op.column}`"
    if op.table:
        return f"on `{op.table}`"
    return "(unresolved name)"
