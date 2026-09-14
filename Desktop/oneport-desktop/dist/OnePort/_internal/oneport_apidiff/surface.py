"""
Public-API surface extraction — pure `ast`, fully deterministic.

Given one Python source file, produce a map of every *public* symbol
(functions, classes, methods) to its signature facts: parameters (name, kind,
default, annotation), return annotation, sync/async, line number.

"Public" follows Python convention:
  - If the module defines `__all__`, exactly those top-level names are public.
  - Otherwise every top-level name not starting with "_" is public.
  - Methods of a public class are public when not underscore-prefixed;
    `__init__` is always included (it *is* the constructor signature).

The LLM never sees raw source here — this module is the ground truth for
"what changed"; the model is only ever asked what a change *means*.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Parameter kinds, in signature order.
POSONLY = "posonly"
POS = "pos"
VARARG = "vararg"
KWONLY = "kwonly"
KWARG = "kwarg"


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # posonly | pos | vararg | kwonly | kwarg
    default: str | None = None  # source text of the default, None if required
    annotation: str | None = None

    @property
    def required(self) -> bool:
        return self.default is None and self.kind in (POSONLY, POS, KWONLY)

    def render(self) -> str:
        prefix = {VARARG: "*", KWARG: "**"}.get(self.kind, "")
        text = f"{prefix}{self.name}"
        if self.annotation:
            text += f": {self.annotation}"
        if self.default is not None:
            text += f" = {self.default}" if self.annotation else f"={self.default}"
        return text


@dataclass
class Symbol:
    qualname: str  # "charge" | "Client" | "Client.request" | "DEFAULT_TIMEOUT"
    kind: str  # function | class | method | property | constant | field | enum_member | reexport  # noqa: E501
    line: int
    is_async: bool = False
    params: list[Param] = field(default_factory=list)
    returns: str | None = None
    bases: list[str] = field(default_factory=list)  # classes only
    annotation: str | None = None  # constants / fields: declared type, if any
    value: str | None = None  # constants / enum members: source of the value; reexport: "module:origname"  # noqa: E501
    has_default: bool = True  # fields: does the field have a default (optional in constructor)?

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    def signature(self) -> str:
        """Human-readable signature for reports and the classification prompt."""
        if self.kind == "class":
            bases = f"({', '.join(self.bases)})" if self.bases else ""
            return f"class {self.qualname}{bases}"
        if self.kind == "constant":
            ann = f": {self.annotation}" if self.annotation else ""
            val = f" = {self.value}" if self.value is not None else ""
            return f"{self.qualname}{ann}{val}"
        if self.kind == "enum_member":
            val = f" = {self.value}" if self.value is not None else ""
            return f"{self.qualname}{val}"
        if self.kind == "field":
            ann = f": {self.annotation}" if self.annotation else ""
            default = " = ..." if self.has_default else ""
            return f"{self.qualname}{ann}{default}"
        if self.kind == "reexport":
            src = self.value or ""
            module, _, orig = src.partition(":")
            orig = orig or self.name
            return f"from {module} import {orig}" + (
                f" as {self.name}" if orig != self.name else ""
            )
        parts: list[str] = []
        prev_kind = None
        for p in self.params:
            # Mark the posonly/positional boundary and the keyword-only bar the
            # way Python renders them.
            if prev_kind == POSONLY and p.kind != POSONLY:
                parts.append("/")
            if p.kind == KWONLY and prev_kind not in (KWONLY, VARARG):
                parts.append("*")
            parts.append(p.render())
            prev_kind = p.kind
        if prev_kind == POSONLY:
            parts.append("/")
        sig = f"def {self.qualname}({', '.join(parts)})"
        if self.is_async:
            sig = "async " + sig
        if self.returns:
            sig += f" -> {self.returns}"
        return sig


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on valid trees
        return None


def _extract_params(fn: ast.FunctionDef | ast.AsyncFunctionDef, skip_first: bool) -> list[Param]:
    """Flatten an ast.arguments into ordered Params. skip_first drops self/cls."""
    a = fn.args
    params: list[Param] = []

    positional = [(arg, POSONLY) for arg in a.posonlyargs] + [(arg, POS) for arg in a.args]
    # defaults right-align across posonlyargs + args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(
        a.defaults
    )
    for (arg, kind), default in zip(positional, defaults):
        params.append(
            Param(
                name=arg.arg,
                kind=kind,
                default=_unparse(default),
                annotation=_unparse(arg.annotation),
            )
        )

    if a.vararg:
        params.append(
            Param(name=a.vararg.arg, kind=VARARG, annotation=_unparse(a.vararg.annotation))
        )

    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append(
            Param(
                name=arg.arg,
                kind=KWONLY,
                default=_unparse(default),
                annotation=_unparse(arg.annotation),
            )
        )

    if a.kwarg:
        params.append(Param(name=a.kwarg.arg, kind=KWARG, annotation=_unparse(a.kwarg.annotation)))

    if skip_first and params and params[0].kind in (POSONLY, POS):
        params = params[1:]
    return params


def _module_all(tree: ast.Module) -> set[str] | None:
    """Names listed in a top-level `__all__ = [...]`, or None if not defined."""
    names: set[str] | None = None
    for node in tree.body:
        target_names = []
        if isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = [node.target.id]
            value = node.value
        else:
            continue
        if "__all__" not in target_names or value is None:
            continue
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            names = {
                elt.value
                for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    return names


def _is_staticmethod(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Name) and d.id == "staticmethod" for d in fn.decorator_list
    )


def _decorator_names(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d  # @dataclass or @dataclass(...)
        text = _unparse(target) or ""
        names.add(text.rsplit(".", 1)[-1])  # dataclasses.dataclass -> dataclass
    return names


def _base_names(node: ast.ClassDef) -> list[str]:
    out: list[str] = []
    for base in node.bases:
        text = _unparse(base)
        if text:
            out.append(text.rsplit(".", 1)[-1])  # enum.Enum -> Enum
    return out


def _is_enum_class(node: ast.ClassDef) -> bool:
    return any(b.endswith(("Enum", "Flag")) for b in _base_names(node))


def _is_field_class(node: ast.ClassDef) -> bool:
    """dataclasses.dataclass, attrs, or a pydantic-style model — field-bearing."""
    if _decorator_names(node) & {"dataclass", "define", "attrs", "attr", "frozen"}:
        return True
    return any(b.endswith(("BaseModel", "BaseSettings")) for b in _base_names(node))


def _target_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    """Single simple assignment target name, else None (skip tuple/attr targets)."""
    if isinstance(node, ast.AnnAssign):
        return node.target.id if isinstance(node.target, ast.Name) else None
    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _is_constant_name(name: str) -> bool:
    # Constant convention: UPPER_SNAKE. Lowercase singletons stay out to avoid
    # flagging every module-level variable; typed module names are caught below.
    return name.isupper() and name.replace("_", "").isalnum()


def _reexport_source(node: ast.ImportFrom) -> str:
    """A displayable module path for a re-export line ('.mod', '..pkg.x', 'pkg')."""
    return "." * node.level + (node.module or "")


def extract_surface(source: str, filename: str = "<string>") -> dict[str, Symbol]:
    """
    Parse `source` and return {qualname: Symbol} for the public API surface.

    Raises SyntaxError on unparseable source — callers decide whether to skip
    the file or fail loudly.
    """
    tree = ast.parse(source, filename=filename)
    explicit_all = _module_all(tree)
    is_init = filename.replace("\\", "/").endswith("__init__.py")
    surface: dict[str, Symbol] = {}

    def is_public_top_level(name: str) -> bool:
        if explicit_all is not None:
            return name in explicit_all
        return not name.startswith("_")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not is_public_top_level(node.name):
                continue
            surface[node.name] = Symbol(
                qualname=node.name,
                kind="function",
                line=node.lineno,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                params=_extract_params(node, skip_first=False),
                returns=_unparse(node.returns),
            )
        elif isinstance(node, ast.ClassDef):
            if not is_public_top_level(node.name):
                continue
            surface[node.name] = Symbol(
                qualname=node.name,
                kind="class",
                line=node.lineno,
                bases=[b for b in (_unparse(base) for base in node.bases) if b],
            )
            is_enum = _is_enum_class(node)
            is_fields = _is_field_class(node)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Public methods + __init__ (the constructor signature is API).
                    if item.name.startswith("_") and item.name != "__init__":
                        continue
                    is_prop = "property" in _decorator_names(item)
                    qualname = f"{node.name}.{item.name}"
                    surface[qualname] = Symbol(
                        qualname=qualname,
                        kind="property" if is_prop else "method",
                        line=item.lineno,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        params=_extract_params(item, skip_first=not _is_staticmethod(item)),
                        returns=_unparse(item.returns),
                    )
                elif is_enum and isinstance(item, ast.Assign):
                    name = _target_name(item)
                    if not name or name.startswith("_"):
                        continue
                    qn = f"{node.name}.{name}"
                    surface[qn] = Symbol(
                        qualname=qn, kind="enum_member", line=item.lineno,
                        value=_unparse(item.value),
                    )
                elif is_fields and isinstance(item, ast.AnnAssign):
                    name = _target_name(item)
                    if not name or name.startswith("_"):
                        continue
                    qn = f"{node.name}.{name}"
                    surface[qn] = Symbol(
                        qualname=qn, kind="field", line=item.lineno,
                        annotation=_unparse(item.annotation),
                        has_default=item.value is not None,
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Module-level public constant (UPPER_SNAKE, or explicitly typed).
            name = _target_name(node)
            if not name or not is_public_top_level(name):
                continue
            annotation = _unparse(node.annotation) if isinstance(node, ast.AnnAssign) else None
            if not (_is_constant_name(name) or annotation is not None):
                continue
            value_node = node.value
            surface[name] = Symbol(
                qualname=name, kind="constant", line=node.lineno,
                annotation=annotation, value=_unparse(value_node) if value_node else None,
            )
        elif is_init and isinstance(node, ast.ImportFrom):
            # __init__.py re-exports ARE the package's public surface.
            source_mod = _reexport_source(node)
            for alias in node.names:
                if alias.name == "*":
                    continue  # star re-export can't be resolved statically
                bound = alias.asname or alias.name
                if not is_public_top_level(bound):
                    continue
                if bound in surface:
                    continue  # a real definition wins over a re-export of the same name
                surface[bound] = Symbol(
                    qualname=bound, kind="reexport", line=node.lineno,
                    value=f"{source_mod}:{alias.name}",
                )

    return surface
