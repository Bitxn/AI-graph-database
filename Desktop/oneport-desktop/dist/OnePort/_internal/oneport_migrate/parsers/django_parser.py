"""
Django migration parser — walks the migration file's AST.

Recognised operations (everything else becomes OTHER and is left to the LLM
layer via the raw source):

  AddField / RemoveField / DeleteModel / AlterField / RenameField /
  RenameModel / AlterModelTable / AddIndex / AddIndexConcurrently /
  CreateModel / RunPython / RunSQL

HONEST LIMITS:
  - AlterField shows only the NEW field definition; a single file cannot tell
    whether the type actually changed, so every AlterField is flagged as a
    potential rewrite (type_changed=True) — the LLM layer and the developer
    have the context to dismiss it.
  - model_name is the Django model, not the physical db table (usually
    app_modelname). Rule messages use the model name.
  - RunSQL with a non-literal argument (variable, f-string) cannot be
    inspected — a note is attached and the op recorded as RAW_SQL.
"""

from __future__ import annotations

import ast

from oneport_migrate.exceptions import ParseError
from oneport_migrate.operations import MigrationOp, OpKind, ParsedMigration
from oneport_migrate.parsers.sql_parser import parse_sql_migration


def parse_django_migration(path: str, content: str) -> ParsedMigration:
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ParseError(f"Cannot parse Django migration {path}: {exc}", path=path) from exc

    m = ParsedMigration(file=path, framework="django", source=content)

    migration_class = _find_migration_class(tree)
    if migration_class is None:
        m.notes.append(f"{path}: no `class Migration` found — treated as empty.")
        return m

    operations_node: ast.expr | None = None
    for stmt in migration_class.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if target.id == "atomic":
                        m.atomic = _const(stmt.value, default=True)
                    elif target.id == "operations":
                        operations_node = stmt.value

    if not isinstance(operations_node, (ast.List, ast.Tuple)):
        if operations_node is not None:
            m.notes.append(f"{path}: `operations` is not a literal list — cannot inspect.")
        return m

    for element in operations_node.elts:
        if isinstance(element, ast.Call):
            _handle_operation(element, path, m)

    return m


def _find_migration_class(tree: ast.Module) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    return None


# ── AST helpers ────────────────────────────────────────────────────────────────

def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _const(node: ast.expr | None, default=None):
    if isinstance(node, ast.Constant):
        return node.value
    return default


def _arg(call: ast.Call, index: int, kwarg: str) -> ast.expr | None:
    """Positional-or-keyword argument lookup."""
    kwargs = _kwargs(call)
    if kwarg in kwargs:
        return kwargs[kwarg]
    if len(call.args) > index:
        return call.args[index]
    return None


def _snippet(content_lines: list[str] | None, node: ast.AST) -> str:
    return ""  # snippets come from the source via line numbers in formatters


# ── Operation handlers ─────────────────────────────────────────────────────────

def _handle_operation(call: ast.Call, path: str, m: ParsedMigration) -> None:
    name = _call_name(call)
    line = call.lineno

    def add(kind: OpKind, table: str = "", column: str = "", **details) -> None:
        m.ops.append(MigrationOp(
            kind=kind, file=path, framework="django", line=line,
            table=table, column=column, details=details,
            snippet=f"migrations.{name}(...)",
        ))

    model = _const(_arg(call, 0, "model_name"), "") or _const(_arg(call, 0, "name"), "")

    if name == "AddField":
        column = _const(_arg(call, 1, "name"), "")
        field = _arg(call, 2, "field")
        nullable, has_default = _field_null_default(field)
        add(OpKind.ADD_COLUMN, table=model, column=column,
            nullable=nullable, has_default=has_default)

    elif name == "RemoveField":
        column = _const(_arg(call, 1, "name"), "")
        add(OpKind.DROP_COLUMN, table=model, column=column)

    elif name == "DeleteModel":
        add(OpKind.DROP_TABLE, table=model)

    elif name == "CreateModel":
        add(OpKind.CREATE_TABLE, table=model)

    elif name == "AlterField":
        column = _const(_arg(call, 1, "name"), "")
        field = _arg(call, 2, "field")
        nullable, _ = _field_null_default(field)
        # One file can't show the OLD definition, so any AlterField is a
        # potential type change / rewrite; not_null flags an explicit null=False.
        add(OpKind.ALTER_COLUMN, table=model, column=column,
            type_changed=True, not_null=(nullable is False))

    elif name == "RenameField":
        old = _const(_arg(call, 1, "old_name"), "")
        new = _const(_arg(call, 2, "new_name"), "")
        add(OpKind.RENAME_COLUMN, table=model, column=old, new_name=new)

    elif name == "RenameModel":
        old = _const(_arg(call, 0, "old_name"), "") or model
        new = _const(_arg(call, 1, "new_name"), "")
        add(OpKind.RENAME_TABLE, table=old, new_name=new)

    elif name == "AlterModelTable":
        add(OpKind.RENAME_TABLE, table=model,
            new_name=_const(_arg(call, 1, "table"), ""))

    elif name in ("AddIndex", "AddIndexConcurrently"):
        add(OpKind.CREATE_INDEX, table=model,
            concurrently=(name == "AddIndexConcurrently"),
            unique=False)

    elif name in ("AddConstraint", "AlterUniqueTogether", "AlterIndexTogether"):
        # Unique constraints build an index under a lock too, but semantics
        # vary — leave detection to a future rule; record for LLM visibility.
        add(OpKind.OTHER, table=model, django_op=name)

    elif name == "RunPython":
        reverse = _arg(call, 1, "reverse_code")
        add(OpKind.DATA_MIGRATION, reversible=reverse is not None)

    elif name == "RunSQL":
        _handle_run_sql(call, path, line, m)

    else:
        add(OpKind.OTHER, django_op=name)


def _handle_run_sql(call: ast.Call, path: str, line: int, m: ParsedMigration) -> None:
    sql_node = _arg(call, 0, "sql")
    reverse_node = _arg(call, 1, "reverse_sql")
    reversible = reverse_node is not None and not (
        isinstance(reverse_node, ast.Constant) and reverse_node.value is None
    )

    sql_text = _sql_literal(sql_node)
    if sql_text is None:
        m.notes.append(
            f"{path}:{line}: RunSQL argument is not a string literal — SQL not analyzed."
        )
        m.ops.append(MigrationOp(
            kind=OpKind.RAW_SQL, file=path, framework="django", line=line,
            snippet="migrations.RunSQL(<dynamic>)", details={"reversible": reversible},
        ))
        return

    inner = parse_sql_migration(path, sql_text, framework="django", line_offset=line - 1)
    m.notes.extend(inner.notes)
    for op in inner.ops:
        op.details.setdefault("reversible", reversible)
        m.ops.append(op)
    if not inner.ops:
        m.ops.append(MigrationOp(
            kind=OpKind.RAW_SQL, file=path, framework="django", line=line,
            snippet="migrations.RunSQL(...)", details={"reversible": reversible},
        ))


def _sql_literal(node: ast.expr | None) -> str | None:
    """Extract SQL from a string constant or a list/tuple of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = []
        for element in node.elts:
            # Elements may be strings or (sql, params) tuples.
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                parts.append(element.value)
            elif (isinstance(element, (ast.List, ast.Tuple)) and element.elts
                  and isinstance(element.elts[0], ast.Constant)
                  and isinstance(element.elts[0].value, str)):
                parts.append(element.elts[0].value)
            else:
                return None
        return ";\n".join(parts)
    return None


def _field_null_default(field: ast.expr | None) -> tuple[bool, bool]:
    """(nullable, has_default) for a models.XField(...) call.

    Django fields default to null=False — an AddField without null=True adds a
    NOT NULL column.
    """
    if not isinstance(field, ast.Call):
        return False, False
    kwargs = _kwargs(field)
    nullable = bool(_const(kwargs.get("null"), False))
    has_default = "default" in kwargs
    # Special cases that don't need a default: auto_now/auto_now_add timestamps.
    if not has_default:
        for auto_kw in ("auto_now", "auto_now_add"):
            if bool(_const(kwargs.get(auto_kw), False)):
                has_default = True
                break
    return nullable, has_default
