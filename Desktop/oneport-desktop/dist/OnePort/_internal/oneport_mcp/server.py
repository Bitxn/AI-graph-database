"""
The OnePort MCP server — the suite's checks as tools any AI coding agent can call.

One server, every agent: Claude Code, Cursor, Codex CLI, VS Code agent mode,
JetBrains — anything that speaks MCP. The agent writes code; these tools let it
(and the human driving it) gate that code before it ships:

    "run the ship gate"            → ship_gate
    "did I just break the API?"    → check_api_breaks
    "any leaked keys in here?"     → scan_secrets

Detection runs locally through the same OnePort CLIs a human uses; nothing is
uploaded. Tools marked metered use AI judgment via the OnePort managed proxy
and need a login; everything else is free and deterministic.
"""
from __future__ import annotations

import json

from mcp.server import MCPServer

from oneport_mcp import __version__
from oneport_mcp.gates import GATES, Gate, installed, logged_in, run_gate, ship_gate


def _j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _single(gate_id: str, path: str = ".", base: str = "main") -> str:
    gate = next(g for g in GATES if g.id == gate_id)
    return _j(run_gate(gate, path=path, base=base))


def build_server() -> MCPServer:
    server = MCPServer(
        name="oneport",
        title="OnePort — pre-ship security gate",
        instructions=(
            "OnePort gates AI-generated code before it ships. Call ship_gate for the "
            "full verdict (READY/BLOCKED), or an individual check while working. "
            "Detection is local and deterministic; nothing is uploaded. If a gate "
            "reports 'skip', tell the user why (missing install or login) — a skipped "
            "check is not a passed check."
        ),
        version=__version__,
    )

    @server.tool()
    def oneport_status() -> str:
        """Which OnePort gates are installed on this machine, and login state."""
        return _j({
            "logged_in": logged_in(),
            "gates": [
                {"gate": g.id, "title": g.title, "installed": installed(g),
                 "metered": g.metered,
                 **({} if installed(g) else {"install": f"pip install {g.pip}"})}
                for g in GATES
            ],
        })

    @server.tool()
    def ship_gate_check(path: str = ".", base: str = "main",
                        include_metered: bool = True) -> str:
        """Run the FULL pre-ship gate on a repo and return one verdict:
        READY or BLOCKED, with per-gate results (secrets, dependency CVEs,
        migration safety, API breaking changes, test gaps, AI review).
        Use before committing/deploying, or when asked 'is this safe to ship?'.
        Set include_metered=False to run only the free deterministic gates."""
        return _j(ship_gate(path=path, base=base, include_metered=include_metered))

    @server.tool()
    def scan_secrets(path: str = ".") -> str:
        """Scan a repo for committed secrets and credentials (API keys, tokens,
        private keys) — deterministic, local, free. status 'block' means a
        likely-real secret is present and must be removed/rotated."""
        return _single("secrets", path=path)

    @server.tool()
    def check_dependencies(path: str = ".") -> str:
        """Check dependency manifests against OSV.dev for known CVEs —
        deterministic, free. status 'block' means confirmed vulnerabilities."""
        return _single("dependencies", path=path)

    @server.tool()
    def check_migrations(path: str = ".") -> str:
        """Check database migrations (Django/Alembic/SQL) for dangerous
        operations — table locks, destructive drops, NOT NULL without default.
        Deterministic, free."""
        return _single("migrations", path=path)

    @server.tool()
    def check_api_breaks(path: str = ".", base: str = "main") -> str:
        """Detect API breaking changes in the working tree vs a base branch —
        removed/renamed public functions and parameters that would break
        callers. Deterministic, free. Run after editing a library's public
        surface."""
        return _single("api-breaks", path=path, base=base)

    @server.tool()
    def check_test_gaps(path: str = ".") -> str:
        """Find changed lines with zero test coverage, ranked by risk
        (metered — uses the OnePort AI layer for ranking; needs login)."""
        return _single("test-gaps", path=path)

    @server.tool()
    def review_changes(path: str = ".") -> str:
        """AI senior-engineer review of the last commit — logic bugs, security
        holes, performance traps, with committable fixes (metered; needs
        login)."""
        return _single("review", path=path)

    return server


def serve() -> None:
    build_server().run("stdio")
