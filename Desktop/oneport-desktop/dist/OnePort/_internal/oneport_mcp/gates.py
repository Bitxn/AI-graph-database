"""
Gate registry — subprocess wrappers around the installed OnePort CLIs.

The MCP server doesn't reimplement any check; it drives the same CLIs a human
would run, in their JSON modes, and normalises the results. A gate whose CLI
isn't installed reports SKIP with the install command — never a fake pass
(a skipped check must be visible, or "clean" would be a lie).

Exit-code contract shared by the suite: 0 = pass, 1 = findings that block,
2 = usage/config error.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# On Windows, launch child console apps with no window so a GUI parent (the
# desktop app) doesn't flash a command-prompt for every gate. 0 elsewhere.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _resolve(exe: str) -> str | None:
    """Find a gate CLI, preferring the environment THIS engine runs in.

    The console scripts live next to the interpreter (…/Scripts on Windows,
    …/bin on POSIX). Resolving there first guarantees the tool matches the
    Python running the engine — otherwise a stray copy of the same-named tool
    earlier on PATH (a different venv with a broken dependency) gets run
    instead, which is exactly how a working tool 'suddenly' fails."""
    scripts = Path(sys.executable).parent
    for cand in (scripts / exe, scripts / f"{exe}.exe"):
        if cand.exists():
            return str(cand)
    return shutil.which(exe)


def logged_in() -> bool:
    """True when the machine is logged in to the OnePort managed proxy."""
    try:
        from oneport_account import is_logged_in
        return bool(is_logged_in())
    except Exception:
        return False


@dataclass(frozen=True)
class Gate:
    id: str
    title: str
    pip: str                        # package that provides the CLI
    exe: str                        # executable name to look for
    args: list[str] = field(default_factory=list)   # {path}/{base} placeholders
    run_in_path: bool = False       # git-based tools run WITH cwd=path instead of a path arg
    metered: bool = False           # needs OnePort login (AI judgment draws tokens)
    timeout: int = 180


GATES: list[Gate] = [
    Gate("secrets", "Secret & credential scan", "oneport-secrets", "oneport-secrets",
         ["scan", "{path}", "-f", "json", "--no-triage"]),
    Gate("dependencies", "Dependency CVEs (OSV)", "oneport-depcheck", "oneport-depcheck",
         ["scan", "{path}", "-f", "json", "--no-llm"], timeout=420),
    # migrate's default --head mode runs `git diff HEAD~1..HEAD` in the CWD, so
    # it must run WITH cwd=path (like the other git-based gates) — passing the
    # repo as a positional TARGET instead left git running in the wrong dir and
    # every scan errored "Not a git repository".
    Gate("migrations", "Migration safety", "oneport-migrate", "oneport-migrate",
         ["check", "--head", "-f", "json", "--no-llm"], run_in_path=True),
    Gate("api-breaks", "API breaking changes", "oneport-apidiff", "oneport-apidiff",
         ["check", "--base", "{base}", "-f", "json", "--no-llm"], run_in_path=True),
    Gate("test-gaps", "Test coverage gaps", "oneport-testgap", "oneport-testgap",
         ["analyze", "--head", "-f", "json"], run_in_path=True, metered=True),
    Gate("review", "AI code review", "oneport-review", "oneport-review",
         ["review", "--head", "-f", "json"], run_in_path=True, metered=True),
]


def installed(gate: Gate) -> bool:
    # Bundled into the host exe → always available, nothing to install.
    if os.environ.get("ONEPORT_TOOL_RUNNER"):
        return True
    return _resolve(gate.exe) is not None


def _summarize(gate_id: str, data) -> str:
    """One human line from a tool's JSON output, best-effort and honest."""
    if not isinstance(data, dict):
        return ""
    if data.get("skipped"):
        return f"skipped: {data.get('reason', '')}"[:140]
    for key in ("findings", "issues", "gaps"):
        if isinstance(data.get(key), list):
            n = len(data[key])
            return f"{n} finding(s)" if n else "clean"
    if isinstance(data.get("summary"), dict):
        s = data["summary"]
        total = s.get("total", s.get("findings"))
        if total is not None:
            return f"{total} finding(s)" if total else "clean"
    return ""


def _git_ready(path: str, min_commits: int = 1) -> tuple[bool, str]:
    """Is `path` itself a git repo WITH enough history? The diff-based gates
    (migrate, apidiff, testgap, review) can only work on real git history — a ZIP
    download (no .git) or a repo with too few commits has nothing to diff. That's
    an honest SKIP with a clear, fixable reason — never a scary red ERROR, and
    never a fake pass. `--head` gates diff HEAD~1..HEAD, so they need >= 2 commits.

    Also guards against git walking UP to a stray parent repo: we require the
    repo root to BE this folder, so attaching a ZIP that happens to sit under some
    other .git (e.g. a stray `git init` in the home folder) doesn't diff against
    the wrong, unrelated history."""
    try:
        top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False, "git isn't available — install Git to enable history-based checks"
    if top.returncode != 0:
        return False, ("not a git repository — if you downloaded a ZIP, use "
                       "`git clone` instead so history checks can run")
    root = (top.stdout or "").strip()
    if root and os.path.realpath(root) != os.path.realpath(path):
        return False, ("this folder has no .git of its own (looks like a ZIP download) — "
                       "attach the cloned repo, not the ZIP")
    try:
        cnt = subprocess.run(["git", "-C", path, "rev-list", "--count", "HEAD"],
                            capture_output=True, text=True, timeout=15,
                            encoding="utf-8", errors="replace",
                            creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False, "could not read git history"
    n = (cnt.stdout or "").strip()
    if cnt.returncode != 0 or not n.isdigit():
        return False, "could not read git history"
    have = int(n)
    if have < 1:
        return False, "this repo has no commits yet — make a commit to enable history checks"
    if have < min_commits:
        return False, ("this repo has only one commit — commit another change "
                       "(or stage some edits) so there's a diff to check")
    return True, ""


def run_gate(gate: Gate, path: str = ".", base: str = "main") -> dict:
    """Run one gate; normalise to {gate, title, status, summary, data}.

    status: pass | block | skip | error. A missing CLI or a missing login is a
    SKIP with the reason spelled out — visible, never silently green."""
    result = {"gate": gate.id, "title": gate.title, "status": "skip",
              "summary": "", "data": None}

    if not installed(gate):
        result["summary"] = f"not installed — pip install {gate.pip}"
        return result
    if gate.metered and not logged_in():
        result["summary"] = ("needs OnePort login (AI judgment) — "
                             "run: oneport-account login <token>")
        return result
    # Diff-based gates need real git history. If there's none (a ZIP download, an
    # empty repo), SKIP with a fixable reason instead of surfacing git's cryptic
    # "fatal: HEAD unknown / not a valid object name" as a red ERROR.
    if gate.run_in_path:
        need = 2 if "--head" in gate.args else 1
        git_ok, git_why = _git_ready(path, need)
        if not git_ok:
            result["summary"] = git_why
            return result

    # Explicit placeholder substitution, not str.format — an arg containing a
    # literal '{' (JSON, code snippets) must never crash the runner.
    args = [a.replace("{path}", path).replace("{base}", base) for a in gate.args]
    # A host (e.g. the desktop app) can bundle the tools inside itself and set
    # ONEPORT_TOOL_RUNNER="<exe>|__tool" — then a gate runs as
    # `<exe> __tool <toolname> <args>` instead of a PATH-resolved CLI, so no tool
    # needs installing and a broken external env can't shadow it.
    runner = os.environ.get("ONEPORT_TOOL_RUNNER")
    if runner:
        cmd = [*runner.split("|"), gate.exe, *args]
    else:
        cmd = [_resolve(gate.exe) or gate.exe, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=gate.timeout,
            cwd=path if gate.run_in_path else None,
            # No flashing console windows when a GUI app (the desktop cockpit)
            # runs these console CLIs. Suppresses the child's window and any
            # git/tool subprocesses it spawns too.
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        result.update(status="error", summary=f"timed out after {gate.timeout}s")
        return result
    except OSError as exc:
        result.update(status="error", summary=f"could not run: {exc}")
        return result

    data = None
    out = (proc.stdout or "").strip()
    if out:
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else None
        except (ValueError, json.JSONDecodeError):
            data = None

    err_tail = (proc.stderr or "").strip().splitlines()
    if proc.returncode == 0:
        result.update(status="pass", data=data,
                      summary=_summarize(gate.id, data) or "clean")
    elif proc.returncode == 1 and data is not None:
        result.update(status="block", data=data,
                      summary=_summarize(gate.id, data) or "blocking findings")
    elif proc.returncode == 1:
        # Exit 1 with no parseable findings = the tool failed, not "findings".
        # An error must never be reported as a blocking finding (or vice versa).
        result.update(status="error",
                      summary=(err_tail[-1][:200] if err_tail else "failed with no output"))
    else:
        result.update(status="error",
                      summary=(err_tail[-1][:200] if err_tail
                               else (out or f"exit {proc.returncode}")[:200]))
    return result


def ship_gate(path: str = ".", base: str = "main",
              include_metered: bool = True) -> dict:
    """Run every available gate and collapse to ONE verdict.

    READY only when nothing blocked. Skipped gates are listed in the verdict —
    'ready with 2 gates skipped' is the honest answer, not plain 'ready'."""
    results = []
    for gate in GATES:
        if gate.metered and not include_metered:
            continue
        results.append(run_gate(gate, path=path, base=base))

    blocked = [r for r in results if r["status"] == "block"]
    errored = [r for r in results if r["status"] == "error"]
    skipped = [r for r in results if r["status"] == "skip"]
    verdict = "BLOCKED" if blocked else "READY"

    note = ""
    if skipped or errored:
        bits = []
        if skipped:
            bits.append(f"{len(skipped)} gate(s) skipped")
        if errored:
            bits.append(f"{len(errored)} gate(s) errored")
        note = " (" + ", ".join(bits) + " — not a full clean bill)"

    return {
        "verdict": verdict,
        "headline": f"Ship readiness: {verdict}"
                    + (f" — fix {len(blocked)} gate(s) first" if blocked else note),
        "path": path,
        "gates": results,
    }
