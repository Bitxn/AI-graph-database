"""
tools.py — the bundled-tool dispatcher.

The whole OnePort suite is packaged INSIDE the desktop exe. Rather than shipping
18 separate console apps the user must install, the one exe can *become* any of
them: `OnePort.exe __tool oneport-secrets scan .` imports that tool's CLI and
runs it in-process. Gates route through this, and the in-app terminal reaches it
via tiny .bat shims — so `oneport-secrets`, `oneport-review`, etc. all work with
zero installation.
"""
from __future__ import annotations

import importlib
import sys

# console-script name  →  "module:function"
TOOLS = {
    "oneport-secrets":     "oneport_secrets.cli:main",
    "oneport-depcheck":    "oneport_depcheck.cli:main",
    "oneport-migrate":     "oneport_migrate.cli:main",
    "oneport-apidiff":     "oneport_apidiff.cli:main",
    "oneport-testgap":     "oneport_testgap.cli:main",
    "oneport-review":      "oneport.cli:main",
    "oneport-account":     "oneport_account.cli:main",
    "oneport-conformance": "oneport_conformance.cli:main",
    "oneport-impact":      "oneport_impact.cli:main",
    "oneport-standup":     "oneport_standup.cli:main",
    "oneport-upgrade":     "oneport_upgrade.cli:main",
    "oneport-context":     "oneport_context.cli:main",
    "oneport-docgen":      "oneport_docgen.cli:main",
    "oneport-apiwatch":    "oneport_apiwatch.cli:main",
    "oneport-costwatch":   "costwatch.cli:main",
    "oneport-evidence":    "oneport_evidence.cli:main",
    "oneport-postmortem":  "postmortem.cli:main",
}

# Packages PyInstaller must bundle (for --collect-all in the build).
BUNDLED_PACKAGES = sorted({spec.split(".")[0] for spec in TOOLS.values()})


def dispatch(name: str, argv: list[str]) -> None:
    """Run a bundled tool as if it were its own CLI, then exit."""
    spec = TOOLS.get(name)
    if not spec:
        sys.stderr.write(f"OnePort: unknown tool '{name}'\n")
        sys.exit(2)
    mod_name, fn_name = spec.split(":")
    sys.argv = [name, *argv]
    try:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, fn_name)
    except Exception as exc:
        sys.stderr.write(f"OnePort: could not load '{name}': {exc}\n")
        sys.exit(2)
    fn()          # click/argparse mains normally call sys.exit themselves
    sys.exit(0)   # in case it returns cleanly


def maybe_dispatch() -> None:
    """If invoked as `<exe> __tool <name> <args…>`, run that tool and exit.
    Call this at the very top of the program, before anything heavy loads."""
    if len(sys.argv) >= 3 and sys.argv[1] == "__tool":
        dispatch(sys.argv[2], sys.argv[3:])
