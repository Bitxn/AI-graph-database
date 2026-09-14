"""
Deterministic diff of two API surfaces (base vs head) for one file.

Every Change produced here is a *fact* derivable from the two ASTs — removed
symbols, added/removed/renamed/reordered required params, default and
annotation changes, sync↔async flips. Verdicts start from DEFAULT_VERDICTS
(with a couple of principled overrides below); the LLM may later refine the
verdict and add narrative, but it never adds or removes changes.
"""

from __future__ import annotations

from oneport_apidiff.result import DEFAULT_VERDICTS, Change, ChangeKind, Verdict
from oneport_apidiff.surface import KWARG, VARARG, Param, Symbol

# Symbol.kind -> ChangeKind for a straight removal / addition. Callables and
# classes keep the generic SYMBOL_* kinds; data-shaped members get precise ones.
_REMOVED_KIND = {
    "constant": ChangeKind.CONSTANT_REMOVED,
    "field": ChangeKind.FIELD_REMOVED,
    "enum_member": ChangeKind.ENUM_MEMBER_REMOVED,
    "reexport": ChangeKind.REEXPORT_REMOVED,
}
_ADDED_KIND = {
    "constant": ChangeKind.CONSTANT_ADDED,
    "enum_member": ChangeKind.ENUM_MEMBER_ADDED,
    "reexport": ChangeKind.REEXPORT_ADDED,
}
_CALLABLE_KINDS = {"function", "method", "property"}


def diff_surfaces(
    base: dict[str, Symbol],
    head: dict[str, Symbol],
    file: str,
) -> list[Change]:
    """Compare two surfaces of the same file. Change.id is filled in later by the engine."""
    changes: list[Change] = []

    removed_names = [q for q in base if q not in head]
    added_names = [q for q in head if q not in base]

    # A removed/added class already implies its methods — don't double-report them.
    removed_classes = {q for q in removed_names if base[q].kind == "class"}
    added_classes = {q for q in added_names if head[q].kind == "class"}

    for qualname in removed_names:
        symbol = base[qualname]
        if "." in qualname and qualname.split(".", 1)[0] in removed_classes:
            continue
        kind = _REMOVED_KIND.get(symbol.kind, ChangeKind.SYMBOL_REMOVED)
        changes.append(
            _change(
                kind,
                file=file,
                line=symbol.line,
                symbol=qualname,
                detail=f"public {_noun(symbol.kind)} `{qualname}` was removed",
                old=symbol.signature(),
            )
        )

    for qualname in added_names:
        symbol = head[qualname]
        if "." in qualname and qualname.split(".", 1)[0] in added_classes:
            continue
        if symbol.kind == "field":
            kind = (
                ChangeKind.FIELD_ADDED_OPTIONAL
                if symbol.has_default
                else ChangeKind.FIELD_ADDED_REQUIRED
            )
        else:
            kind = _ADDED_KIND.get(symbol.kind, ChangeKind.SYMBOL_ADDED)
        changes.append(
            _change(
                kind,
                file=file,
                line=symbol.line,
                symbol=qualname,
                detail=f"public {_noun(symbol.kind)} `{qualname}` was added",
                new=symbol.signature(),
            )
        )

    for qualname in base:
        if qualname not in head:
            continue
        old_sym, new_sym = base[qualname], head[qualname]
        if old_sym.kind == "class":
            continue  # class-level API is its methods/fields, compared individually
        if old_sym.kind in _CALLABLE_KINDS:
            changes.extend(_diff_callable(old_sym, new_sym, file))
        elif old_sym.kind == "constant":
            changes.extend(_diff_constant(old_sym, new_sym, file))
        elif old_sym.kind == "field":
            changes.extend(_diff_field(old_sym, new_sym, file))
        elif old_sym.kind == "enum_member":
            changes.extend(_diff_enum_member(old_sym, new_sym, file))
        # reexport: same bound name still resolves — a changed source module is
        # not a public break, so nothing to report while the name survives.

    return changes


def _noun(kind: str) -> str:
    return {
        "reexport": "re-export",
        "enum_member": "enum member",
    }.get(kind, kind)


def _diff_constant(old: Symbol, new: Symbol, file: str) -> list[Change]:
    changes: list[Change] = []
    if old.annotation != new.annotation:
        changes.append(
            _change(
                ChangeKind.CONSTANT_TYPE_CHANGED, file, new.line, new.qualname,
                f"type of constant `{new.qualname}` changed: "
                f"{old.annotation or 'unannotated'} → {new.annotation or 'unannotated'}",
                old=old.signature(), new=new.signature(),
            )
        )
    if old.value != new.value:
        changes.append(
            _change(
                ChangeKind.CONSTANT_VALUE_CHANGED, file, new.line, new.qualname,
                f"value of constant `{new.qualname}` changed: {old.value} → {new.value}",
                old=old.signature(), new=new.signature(),
            )
        )
    return changes


def _diff_field(old: Symbol, new: Symbol, file: str) -> list[Change]:
    changes: list[Change] = []
    if old.annotation != new.annotation:
        changes.append(
            _change(
                ChangeKind.FIELD_TYPE_CHANGED, file, new.line, new.qualname,
                f"type of field `{new.qualname}` changed: "
                f"{old.annotation or 'unannotated'} → {new.annotation or 'unannotated'}",
                old=old.signature(), new=new.signature(),
            )
        )
    # A field that loses its default becomes a required constructor arg.
    if old.has_default and not new.has_default:
        changes.append(
            _change(
                ChangeKind.FIELD_ADDED_REQUIRED, file, new.line, new.qualname,
                f"field `{new.qualname}` lost its default and is now required",
                old=old.signature(), new=new.signature(),
            )
        )
    return changes


def _diff_enum_member(old: Symbol, new: Symbol, file: str) -> list[Change]:
    if old.value != new.value:
        return [
            _change(
                ChangeKind.ENUM_MEMBER_VALUE_CHANGED, file, new.line, new.qualname,
                f"value of enum member `{new.qualname}` changed: {old.value} → {new.value}",
                old=old.signature(), new=new.signature(),
            )
        ]
    return []


# ── Callable comparison ────────────────────────────────────────────────────────


def _diff_callable(old: Symbol, new: Symbol, file: str) -> list[Change]:
    changes: list[Change] = []
    old_sig, new_sig = old.signature(), new.signature()

    def add(kind: ChangeKind, detail: str, verdict: Verdict | None = None) -> None:
        changes.append(
            _change(
                kind,
                file=file,
                line=new.line,
                symbol=new.qualname,
                detail=detail,
                old=old_sig,
                new=new_sig,
                verdict=verdict,
            )
        )

    if old.is_async != new.is_async:
        if new.is_async:
            add(ChangeKind.SYNC_TO_ASYNC, f"`{new.qualname}` changed from sync to async")
        else:
            add(ChangeKind.ASYNC_TO_SYNC, f"`{new.qualname}` changed from async to sync")

    old_by_name = {p.name: p for p in old.params}
    new_by_name = {p.name: p for p in new.params}

    removed = [p for p in old.params if p.name not in new_by_name]
    added = [p for p in new.params if p.name not in old_by_name]

    # Rename heuristic: a removed and an added param at the same position with
    # the same kind is one rename, not a remove + add.
    renames: list[tuple[Param, Param]] = []
    old_positions = {p.name: i for i, p in enumerate(old.params)}
    new_positions = {p.name: i for i, p in enumerate(new.params)}
    for r in list(removed):
        match = next(
            (
                a
                for a in added
                if a.kind == r.kind and new_positions[a.name] == old_positions[r.name]
            ),
            None,
        )
        if match is None and len(removed) == 1 and len(added) == 1 and added[0].kind == r.kind:
            match = added[0]  # single swapped param — same kind, position may have shifted
        if match is not None:
            renames.append((r, match))
            removed.remove(r)
            added.remove(match)

    for r, a in renames:
        add(
            ChangeKind.PARAM_RENAMED,
            f'param `{r.name}` was renamed to `{a.name}`',
        )

    for p in removed:
        label = {VARARG: f"*{p.name}", KWARG: f"**{p.name}"}.get(p.kind, p.name)
        add(
            ChangeKind.PARAM_REMOVED,
            f"param `{label}`{'' if p.required else ' (optional)'} was removed",
        )

    for p in added:
        if p.required:
            add(ChangeKind.PARAM_ADDED_REQUIRED, f"required param `{p.name}` was added")
        else:
            label = {VARARG: f"*{p.name}", KWARG: f"**{p.name}"}.get(p.kind, p.name)
            add(
                ChangeKind.PARAM_ADDED_OPTIONAL,
                f"optional param `{label}` was added",
                verdict=Verdict.COMPATIBLE,
            )

    # Reorder of required positional params (the ones positional callers depend on).
    old_req = [p.name for p in old.params if p.required and p.kind != "kwonly"]
    new_req = [p.name for p in new.params if p.required and p.kind != "kwonly"]
    common_old = [n for n in old_req if n in new_req]
    common_new = [n for n in new_req if n in common_old]
    if common_old != common_new:
        add(
            ChangeKind.PARAMS_REORDERED,
            f"positional params were reordered: ({', '.join(common_old)}) → "
            f"({', '.join(common_new)})",
        )

    # Per-param changes for params present in both versions.
    for name, old_p in old_by_name.items():
        new_p = new_by_name.get(name)
        if new_p is None:
            continue
        if old_p.default != new_p.default:
            if old_p.default is not None and new_p.default is None:
                add(
                    ChangeKind.DEFAULT_REMOVED,
                    f"param `{name}` lost its default ({old_p.default}) and is now required",
                )
            elif old_p.default is None and new_p.default is not None:
                # Required → optional never breaks an existing call site.
                add(
                    ChangeKind.DEFAULT_CHANGED,
                    f"param `{name}` gained a default ({new_p.default}) and is now optional",
                    verdict=Verdict.COMPATIBLE,
                )
            else:
                add(
                    ChangeKind.DEFAULT_CHANGED,
                    f"default of `{name}` changed: {old_p.default} → {new_p.default}",
                )
        if old_p.annotation != new_p.annotation:
            add(
                ChangeKind.ANNOTATION_CHANGED,
                f"type of `{name}` changed: {old_p.annotation or 'unannotated'} → "
                f"{new_p.annotation or 'unannotated'}",
            )
        if old_p.kind != new_p.kind and not any(name in (r.name, a.name) for r, a in renames):
            add(
                ChangeKind.PARAMS_REORDERED,
                f"param `{name}` moved from {old_p.kind} to {new_p.kind}",
            )

    if old.returns != new.returns:
        add(
            ChangeKind.RETURN_ANNOTATION_CHANGED,
            f"return type changed: {old.returns or 'unannotated'} → "
            f"{new.returns or 'unannotated'}",
        )

    return changes


def _change(
    kind: ChangeKind,
    file: str,
    line: int,
    symbol: str,
    detail: str,
    old: str = "",
    new: str = "",
    verdict: Verdict | None = None,
) -> Change:
    return Change(
        id=0,
        kind=kind,
        file=file,
        line=line,
        symbol=symbol,
        detail=detail,
        old_signature=old,
        new_signature=new,
        verdict=verdict or DEFAULT_VERDICTS[kind],
    )
