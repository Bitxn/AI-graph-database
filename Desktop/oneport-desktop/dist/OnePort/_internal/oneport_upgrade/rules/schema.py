"""
The rule schema — the data model behind every migration.

A Rule declares one deprecated/removed API and how to detect it (and, when safe,
how to rewrite it). The catalog is pure data so adding a migration is adding rules,
never touching the engine.

`kind` decides how the scanner matches:
  import       `import <match>`                       (module import)
  from_import  `from <module> import <match>`
  call         a call to `<match>(...)`  (Name or Attribute .match())
  attr         an attribute access `....<match>`
  decorator    `@<match>` on a def/class
  base         `class X(<match>)`
  name         a bare use of the name `<match>`

`auto=True` means the codemod is a safe, purely-mechanical swap the `apply`
command may perform. Everything else is detection + guidance only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Severity: a removed API is a hard break (won't run on the target); a deprecated
# one still runs but should be migrated. Maps to error / warning downstream.
REMOVED = "removed"
DEPRECATED = "deprecated"

_VALID_KINDS = {"import", "from_import", "call", "attr", "decorator", "base", "name"}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    kind: str
    match: str                    # the symbol detected
    module: str = ""              # context module (from_import), or expected owner
    owner_from: str = ""          # for attr rules: module the receiver must be imported from
                                  # (e.g. django.utils) so stdlib `datetime.timezone` isn't matched
    requires_import: str = ""     # only fire in files that import this module. For generic
                                  # method names (.json/.dict/.copy) whose receiver type can't
                                  # be inferred: a .json() in a file that never imports pydantic
                                  # is httpx/requests, not a pydantic model. Cuts 1,707 FastAPI
                                  # test-suite false positives to near zero.
    severity: str = DEPRECATED
    replacement: str = ""         # new symbol name, when a simple swap
    new_module: str = ""          # new module, for import moves
    auto: bool = False            # safe to auto-apply?
    hint: str = ""                # human guidance (esp. for non-auto rules)
    docs: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"rule {self.id}: bad kind {self.kind!r}")


@dataclass(frozen=True)
class Migration:
    id: str                       # "django:5.0"
    title: str
    summary: str
    rules: list[Rule] = field(default_factory=list)

    @property
    def framework(self) -> str:
        return self.id.split(":", 1)[0]
