"""
Python 3.12 readiness rules — removed and deprecated stdlib APIs.

Sources: the "Removed"/"Deprecated" sections of the CPython 3.10–3.12 changelogs.
Auto codemods are limited to pure, safe renames (unittest aliases, the
collections.abc moves) — anything needing new arguments or restructuring is
detection + guidance only.
"""

from __future__ import annotations

from oneport_upgrade.rules.schema import DEPRECATED, REMOVED, Migration, Rule

_ABC_NAMES = ("Mapping", "MutableMapping", "Sequence", "MutableSequence", "Set",
              "MutableSet", "Iterable", "Iterator", "Callable", "Hashable", "Container")

_RULES = [
    # collections.X → collections.abc.X (removed in 3.10)
    *[
        Rule(id=f"py-collections-abc-{n.lower()}", title=f"collections.{n} moved to collections.abc",
             kind="from_import", module="collections", match=n, severity=REMOVED,
             new_module="collections.abc", auto=True,
             hint=f"Import {n} from collections.abc.")
        for n in _ABC_NAMES
    ],
    # unittest method aliases removed in 3.12
    Rule(id="py-assertEquals", title="assertEquals() removed", kind="call", match="assertEquals",
         severity=REMOVED, replacement="assertEqual", auto=True),
    Rule(id="py-assertNotEquals", title="assertNotEquals() removed", kind="call", match="assertNotEquals",
         severity=REMOVED, replacement="assertNotEqual", auto=True),
    Rule(id="py-failUnless", title="failUnless() removed", kind="call", match="failUnless",
         severity=REMOVED, replacement="assertTrue", auto=True),
    Rule(id="py-assert_", title="assert_() removed", kind="call", match="assert_",
         severity=REMOVED, replacement="assertTrue", auto=True),
    # inspect.getargspec removed in 3.11 — both the call and the import.
    Rule(id="py-getargspec", title="inspect.getargspec() removed", kind="call", match="getargspec",
         severity=REMOVED, replacement="getfullargspec", auto=True,
         hint="getfullargspec has a superset signature."),
    Rule(id="py-getargspec-import", title="import of removed inspect.getargspec", kind="from_import",
         module="inspect", match="getargspec", severity=REMOVED, replacement="getfullargspec", auto=True),
    # datetime.utcnow / utcfromtimestamp deprecated in 3.12
    Rule(id="py-utcnow", title="datetime.utcnow() is deprecated", kind="call", match="utcnow",
         severity=DEPRECATED, auto=False,
         hint="Use datetime.now(datetime.timezone.utc) — utcnow() returns a naive datetime."),
    Rule(id="py-utcfromtimestamp", title="datetime.utcfromtimestamp() is deprecated",
         kind="call", match="utcfromtimestamp", severity=DEPRECATED, auto=False,
         hint="Use datetime.fromtimestamp(ts, datetime.timezone.utc)."),
    # removed modules
    Rule(id="py-imp", title="the 'imp' module was removed in 3.12", kind="import", match="imp",
         severity=REMOVED, auto=False, hint="Use importlib (importlib.util / importlib.machinery)."),
    Rule(id="py-distutils", title="'distutils' was removed in 3.12", kind="import", match="distutils",
         severity=REMOVED, auto=False, hint="Use setuptools or the 'packaging' library."),
    Rule(id="py-asynchat", title="'asynchat'/'asyncore' removed in 3.12", kind="import", match="asyncore",
         severity=REMOVED, auto=False, hint="Port to asyncio."),
    # ssl.wrap_socket removed in 3.12
    Rule(id="py-wrap-socket", title="ssl.wrap_socket() removed in 3.12", kind="call", match="wrap_socket",
         severity=REMOVED, auto=False, hint="Use ssl.SSLContext().wrap_socket()."),
    # asyncio.coroutine removed in 3.11
    Rule(id="py-asyncio-coroutine", title="@asyncio.coroutine removed in 3.11", kind="decorator",
         match="coroutine", severity=REMOVED, auto=False, hint="Use 'async def'."),
]

PYTHON_312 = Migration(
    id="python:3.12",
    title="Python 3.12 readiness",
    summary="Removed/deprecated stdlib APIs between Python 3.9 and 3.12.",
    rules=_RULES,
)
