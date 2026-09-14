"""
Pydantic v1 → v2 rules.

Only the pydantic-specific, unambiguous names are auto (parse_obj, __fields__,
update_forward_refs). Generic method names (.dict(), .json(), .copy()) are
detection + guidance because we can't statically prove the receiver is a model —
auto-renaming them would corrupt code that calls dict()/json() on other objects.

Source: the official Pydantic v1→v2 migration guide.
"""

from __future__ import annotations

from oneport_upgrade.rules.schema import DEPRECATED, REMOVED, Migration, Rule

_RULES = [
    # decorators — signatures changed, so not a pure swap
    Rule(id="pyd-validator", title="@validator → @field_validator", kind="decorator", match="validator",
         severity=REMOVED, auto=False,
         hint="Use @field_validator; add @classmethod, and 'values' becomes a ValidationInfo arg."),
    Rule(id="pyd-root-validator", title="@root_validator → @model_validator", kind="decorator",
         match="root_validator", severity=REMOVED, auto=False,
         hint="Use @model_validator(mode='before'|'after')."),
    # safe, pydantic-specific renames
    Rule(id="pyd-parse-obj", title="Model.parse_obj() → model_validate()", kind="call", match="parse_obj",
         severity=DEPRECATED, replacement="model_validate", auto=True),
    Rule(id="pyd-fields", title="Model.__fields__ → model_fields", kind="attr", match="__fields__",
         severity=DEPRECATED, replacement="model_fields", auto=True),
    Rule(id="pyd-update-forward-refs", title="update_forward_refs() → model_rebuild()", kind="call",
         match="update_forward_refs", severity=DEPRECATED, replacement="model_rebuild", auto=True),
    # Ambiguous receivers — detect + guide, never auto-rename. requires_import
    # gates them to files that actually import pydantic: `.json()` is httpx/
    # requests' method far more often than a model's (FastAPI's test suite alone
    # has 1,707 `response.json()` calls), and firing on all of them buries the
    # real signal the same way an over-broad migrate/secrets scan does.
    Rule(id="pyd-dict", title=".dict() → .model_dump()", kind="call", match="dict",
         severity=DEPRECATED, auto=False, requires_import="pydantic",
         hint="On a pydantic model, use .model_dump(). (Not auto-applied: .dict() is also a builtin.)"),
    Rule(id="pyd-json", title=".json() → .model_dump_json()", kind="call", match="json",
         severity=DEPRECATED, auto=False, requires_import="pydantic",
         hint="On a pydantic model, use .model_dump_json(). (Not auto-applied: httpx/requests "
              "responses also have .json().)"),
    Rule(id="pyd-parse-raw", title=".parse_raw() → .model_validate_json()", kind="call", match="parse_raw",
         severity=DEPRECATED, auto=False, requires_import="pydantic",
         hint="Use .model_validate_json()."),
    Rule(id="pyd-copy", title=".copy() → .model_copy()", kind="call", match="copy",
         severity=DEPRECATED, auto=False, requires_import="pydantic",
         hint="On a pydantic model, use .model_copy(). (Not auto-applied: .copy() is generic.)"),
    # base class moved to a separate package
    Rule(id="pyd-basesettings", title="BaseSettings moved to pydantic-settings", kind="base",
         match="BaseSettings", severity=REMOVED, auto=False,
         hint="pip install pydantic-settings; import BaseSettings from pydantic_settings."),
    Rule(id="pyd-schema", title=".schema() → .model_json_schema()", kind="call", match="schema",
         severity=DEPRECATED, auto=False, requires_import="pydantic",
         hint="On a model class, use .model_json_schema()."),
]

PYDANTIC_2 = Migration(
    id="pydantic:2",
    title="Pydantic v1 → v2",
    summary="Renamed methods, changed validators, and moved BaseSettings.",
    rules=_RULES,
)
