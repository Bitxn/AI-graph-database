"""
Alembic migration parser — walks upgrade()/downgrade() ASTs for op.* calls.

Recognised: op.add_column, op.drop_column, op.drop_table, op.create_table,
op.alter_column (type_/nullable/new_column_name), op.create_index,
op.rename_table, op.execute (string literals re-parsed as SQL),
op.bulk_insert, and the same calls on a `with op.batch_alter_table(...) as b:`
alias (where the table comes from the with-statement and arguments shift).

HONEST LIMITS:
  - op.execute with a non-literal argument is recorded as RAW_SQL, unanalyzed.
  - postgresql_concurrently=True is the only CONCURRENTLY signal we detect;
    an autocommit_block() wrapper alone is not treated as concurrent.
  - alter_column: existing_type/type_ tells us a type change was requested;
    like Django, we cannot verify the old type from one file.
"""

from __future__ import annotations

import ast

from oneport_migrate.exceptions import ParseError
from oneport_migrate.operations import MigrationOp, OpKind, ParsedMigration
from oneport_migrate.parsers.sql_parser import parse_sql_migration


def parse_alembic_migration(path: str, content: str) -> ParsedMigration:
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ParseError(f"Cannot parse Alembic migration {path}: {exc}", path=path) from exc

    m = ParsedMigration(file=path, framework="alembic", source=content)

    upgrade = _find_function(tree, "upgrade")
    downgrade = _find_function(tree, "downgrade")

    if upgrade is None:
        m.notes.append(f"{path}: no upgrade() function found — treated as empty.")
    else:
        _walk_function(upgrade, path, m)

    m.has_reverse = _has_real_body(downgrade)
    return m


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _has_real_body(func: ast.FunctionDef | None) -> bool:
    """True if the function exists and does more than pass/docstring.

    An explicit `raise` counts as a real body: raising in downgrade() is the
    reviewed way to say "irreversible on purpose".
    """
    if func is None:
        return False
    for stmt in func.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring or bare literal
        return True
    return False


def _const(node: ast.expr | None, default=None):
    if isinstance(node, ast.Constant):
        return node.value
    return default


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _walk_function(func: ast.FunctionDef, path: str, m: ParsedMigration) -> None:
    # Map with-aliases from `with op.batch_alter_table("users") as batch_op:`
    # to their table name, so calls on the alias resolve the right table.
    batch_aliases: dict[str, str] = {}

    for node in ast.walk(func):
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if (isinstance(ctx, ast.Call)
                        and isinstance(ctx.func, ast.Attribute)
                        and ctx.func.attr == "batch_alter_table"
                        and item.optional_vars is not None
                        and isinstance(item.optional_vars, ast.Name)):
                    table = _const(ctx.args[0] if ctx.args else None, "")
                    batch_aliases[item.optional_vars.id] = table or ""

    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if not isinstance(receiver, ast.Name):
                continue
            if receiver.id == "op":
                _handle_op_call(node, node.func.attr, path, m, batch_table=None)
            elif receiver.id in batch_aliases:
                _handle_op_call(node, node.func.attr, path, m,
                                batch_table=batch_aliases[receiver.id])


def _handle_op_call(
    call: ast.Call, method: str, path: str, m: ParsedMigration,
    batch_table: str | None,
) -> None:
    """batch_table is not None when the call is on a batch_alter_table alias —
    the table comes from the with-statement and positional args shift left."""
    line = call.lineno
    kwargs = _kwargs(call)
    in_batch = batch_table is not None

    def pos(index: int) -> ast.expr | None:
        return call.args[index] if len(call.args) > index else None

    def table_arg() -> str:
        if in_batch:
            return batch_table or ""
        return _const(kwargs.get("table_name") or pos(0), "") or ""

    def add(kind: OpKind, table: str = "", column: str = "", **details) -> None:
        m.ops.append(MigrationOp(
            kind=kind, file=path, framework="alembic", line=line,
            table=table, column=column, details=details,
            snippet=f"op.{method}(...)",
        ))

    if method == "add_column":
        column_node = kwargs.get("column") or pos(0 if in_batch else 1)
        name, nullable, has_default = _column_facts(column_node)
        add(OpKind.ADD_COLUMN, table=table_arg(), column=name,
            nullable=nullable, has_default=has_default)

    elif method == "drop_column":
        column = _const(kwargs.get("column_name") or pos(0 if in_batch else 1), "")
        add(OpKind.DROP_COLUMN, table=table_arg(), column=column)

    elif method == "drop_table":
        add(OpKind.DROP_TABLE, table=table_arg())

    elif method == "create_table":
        add(OpKind.CREATE_TABLE, table=table_arg())

    elif method == "alter_column":
        column = _const(kwargs.get("column_name") or pos(0 if in_batch else 1), "")
        table = table_arg()
        if "new_column_name" in kwargs:
            add(OpKind.RENAME_COLUMN, table=table, column=column,
                new_name=_const(kwargs["new_column_name"], ""))
        type_changed = "type_" in kwargs
        not_null = _const(kwargs.get("nullable"), None) is False
        if type_changed:
            add(OpKind.ALTER_COLUMN, table=table, column=column,
                type_changed=True, not_null=not_null)
        elif not_null:
            add(OpKind.SET_NOT_NULL, table=table, column=column)

    elif method == "create_index":
        table = batch_table if in_batch else _const(kwargs.get("table_name") or pos(1), "")
        concurrently = bool(_const(kwargs.get("postgresql_concurrently"), False))
        add(OpKind.CREATE_INDEX, table=table or "",
            concurrently=concurrently,
            unique=bool(_const(kwargs.get("unique"), False)))

    elif method == "rename_table":
        old = _const(kwargs.get("old_table_name") or pos(0), "")
        new = _const(kwargs.get("new_table_name") or pos(1), "")
        add(OpKind.RENAME_TABLE, table=old or "", new_name=new)

    elif method == "execute":
        sql_node = kwargs.get("sqltext") or pos(0)
        sql_text = _const(sql_node, None)
        if isinstance(sql_text, str):
            inner = parse_sql_migration(path, sql_text, framework="alembic",
                                        line_offset=line - 1)
            m.notes.extend(inner.notes)
            m.ops.extend(inner.ops)
            if not inner.ops:
                add(OpKind.RAW_SQL)
        else:
            m.notes.append(
                f"{path}:{line}: op.execute() argument is not a string literal — "
                "SQL not analyzed."
            )
            add(OpKind.RAW_SQL)

    elif method == "bulk_insert":
        table_node = kwargs.get("table") or pos(0)
        table = ""
        if isinstance(table_node, ast.Name):
            table = table_node.id
        add(OpKind.DATA_MIGRATION, table=table, dml="bulk_insert")

    elif method == "batch_alter_table":
        pass  # handled via the with-statement alias mapping

    else:
        add(OpKind.OTHER, alembic_op=method)


def _column_facts(node: ast.expr | None) -> tuple[str, bool, bool]:
    """(name, nullable, has_default) from a sa.Column(...) call.

    SQLAlchemy columns default to nullable=True (except primary keys), so a
    missing nullable kwarg means nullable.
    """
    if not isinstance(node, ast.Call):
        return "", True, False
    name = _const(node.args[0] if node.args else None, "") or ""
    kwargs = _kwargs(node)
    nullable = _const(kwargs.get("nullable"), True)
    has_default = "server_default" in kwargs or "default" in kwargs
    if _const(kwargs.get("primary_key"), False):
        nullable = False
    return name, bool(nullable), has_default
