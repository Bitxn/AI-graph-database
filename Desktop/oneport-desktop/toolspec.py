"""OnePort Desktop — the full tool catalog for the one-click Tools dashboard.

Every tool the app bundles, described so the UI can run it on the open repo and
show a REAL result. Honesty rules that hold for any repo:
  • runnable tools carry the exact, proven command (JSON where the tool supports
    it) and use {path}/{base} placeholders filled at run time — never repo-specific
  • tools that genuinely need an extra input (op-ship history, a checks file,
    incident logs) are marked `requires=` and are NOT auto-run with a faked green;
    the UI shows what they need instead
  • a tool's own non-zero exit with no parseable output is surfaced as an error,
    never dressed up as "clean" or as a finding

The command dispatch is shared with the gate engine: when the host sets
ONEPORT_TOOL_RUNNER (the frozen desktop exe does), each tool runs as
`<exe> __tool <toolname> <args>`; otherwise it resolves the console script on PATH
(dev). So this works identically bundled or from a dev checkout.
"""
from __future__ import annotations

import json
import os
import subprocess

try:
    from oneport_mcp.gates import _resolve, _NO_WINDOW, _git_ready
except Exception:  # pragma: no cover - fallback if the engine import shape changes
    import shutil

    def _resolve(exe):
        return shutil.which(exe)

    def _git_ready(path, min_commits=1):
        return True, ""

    _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# needs badges: ai=draws tokens, net=internet, git=needs commit history, base=needs base branch
TOOLS = [
    # ── Security ──────────────────────────────────────────────
    {"id": "secrets", "exe": "oneport-secrets", "title": "Secret scan", "cat": "Security",
     "desc": "Find committed API keys, tokens & passwords — in code and git history.",
     "cmd": ["scan", "{path}", "-f", "json", "--no-triage"], "kind": "findings", "needs": []},
    {"id": "dependencies", "exe": "oneport-depcheck", "title": "Dependency CVEs", "cat": "Security",
     "desc": "Check your dependencies against the OSV vulnerability database.",
     "cmd": ["scan", "{path}", "-f", "json", "--no-llm"], "kind": "findings", "needs": ["net"]},
    {"id": "migrations", "exe": "oneport-migrate", "title": "Migration safety", "cat": "Checking",
     "desc": "Catch unsafe DB migrations (table locks, data loss) before deploy.",
     "cmd": ["check", "--head", "-f", "json", "--no-llm"], "in_path": True,
     "kind": "findings", "needs": ["git"]},
    {"id": "api-breaks", "exe": "oneport-apidiff", "title": "API breaking changes", "cat": "Checking",
     "desc": "Diff your public API surface vs the base branch to catch breaks.",
     "cmd": ["check", "--base", "{base}", "-f", "json", "--no-llm"], "in_path": True,
     "kind": "findings", "needs": ["base"]},

    # ── Quality ───────────────────────────────────────────────
    {"id": "review", "exe": "oneport-review", "title": "AI code review", "cat": "Checking",
     "desc": "Senior-level review of your latest change, with suggested fixes.",
     "cmd": ["review", "--head", "-f", "json"], "in_path": True,
     "kind": "findings", "needs": ["ai", "git"]},
    {"id": "test-gaps", "exe": "oneport-testgap", "title": "Test coverage gaps", "cat": "Checking",
     "desc": "Find changed lines that have no test covering them.",
     "cmd": ["analyze", "--head", "-f", "json"], "in_path": True,
     "kind": "findings", "needs": ["ai", "git"]},
    {"id": "conformance", "exe": "oneport-conformance", "title": "Intent conformance", "cat": "Explanation",
     "desc": "Verify the change did what it was MEANT to (vs an intent doc).",
     "cmd": ["check", "{path}", "-f", "json"], "kind": "findings", "needs": ["ai", "git"],
     "note": "Auto-drafts .oneport/intent.md from your codebase on first run; needs git history (a change) to check against — review the draft to sharpen it."},

    # ── Velocity ──────────────────────────────────────────────
    {"id": "impact", "exe": "oneport-impact", "title": "Blast radius", "cat": "Explanation",
     "desc": "What breaks if you touch this? Call graph + git co-change.",
     "cmd": ["check", "--head", "-f", "json", "--no-llm"], "in_path": True,
     "kind": "findings", "needs": ["git"]},
    {"id": "standup", "exe": "oneport-standup", "title": "Weekly standup", "cat": "Explanation",
     "desc": "Turn the last 7 days of git history into standup / release notes.",
     "cmd": ["weekly", "-f", "markdown", "--no-llm"], "in_path": True, "kind": "text", "needs": [],
     "note": "Deterministic here. AI narration is available from the terminal (draws tokens)."},
    {"id": "upgrade", "exe": "oneport-upgrade", "title": "Framework upgrades", "cat": "Checking",
     "desc": "List framework migrations you can run safe codemods for.",
     "cmd": ["list"], "kind": "text", "needs": [],
     "note": "Lists targets. To scan for one: oneport-upgrade scan --to <target>."},
    {"id": "costwatch", "exe": "oneport-costwatch", "title": "Cloud cost gate", "cat": "Checking",
     "desc": "Read your Infrastructure-as-Code and flag cost waste. No cloud creds.",
     "cmd": ["analyze", "{path}", "-f", "json"], "kind": "findings", "needs": []},

    # ── Understanding ─────────────────────────────────────────
    {"id": "context", "exe": "oneport-context", "title": "Codebase map", "cat": "Onboarding",
     "desc": "Build a hierarchical understanding of the repo (cached locally).",
     "cmd": ["index", "{path}"], "kind": "text", "needs": ["ai"], "bg": True, "timeout": 900,
     "note": "Also does a PDF handbook & narrated walkthrough — from the terminal."},
    {"id": "docgen", "exe": "oneport-docgen", "title": "Design docs", "cat": "Onboarding",
     "desc": "Generate a formal design doc (PDF + Word + Markdown) from the code.",
     "cmd": ["generate", "{path}", "-y", "--no-open"], "kind": "text", "needs": ["ai"],
     "bg": True, "timeout": 900, "note": "Default type is TDD; output lands in <repo>/.docgen."},

    # ── Governance (need external input — never faked) ────────
    {"id": "evidence", "exe": "oneport-evidence", "title": "Compliance evidence", "cat": "Security",
     "desc": "SOC 2 / ISO 27001 evidence packs from your op ship history.", "needs": [],
     "requires": "Needs op-ship history. Run the gates (Quick scan / Test for Production) "
                 "a few times first, then: oneport-evidence report."},
    {"id": "apiwatch", "exe": "oneport-apiwatch", "title": "API health monitor", "cat": "Security",
     "desc": "Probe your live endpoints; AI explains failures. Serverless-friendly.", "needs": ["net"],
     "requires": "Needs a checks file listing your endpoints. Create one, then: "
                 "oneport-apiwatch check <checks.yaml>."},
    {"id": "postmortem", "exe": "oneport-postmortem", "title": "Incident post-mortem", "cat": "Security",
     "desc": "AI-generated incident post-mortems from logs or a Slack thread.", "needs": ["ai"],
     "requires": "Needs incident input (a log file or a Slack thread). Run: "
                 "oneport-postmortem generate --help to see the options."},
]

CAT_ORDER = ["Onboarding", "Explanation", "Checking", "Security"]


def _spec(tool_id: str):
    return next((t for t in TOOLS if t["id"] == tool_id), None)


def catalog() -> list[dict]:
    """Display metadata for every tool + whether it can run here."""
    runner = bool(os.environ.get("ONEPORT_TOOL_RUNNER"))
    out = []
    for t in TOOLS:
        installed = True if runner else (_resolve(t["exe"]) is not None)
        out.append({
            "id": t["id"], "title": t["title"], "desc": t["desc"], "cat": t["cat"],
            "needs": t.get("needs", []), "note": t.get("note", ""),
            "requires": t.get("requires", ""), "kind": t.get("kind", "text"),
            "bg": bool(t.get("bg")), "installed": installed,
            "runnable": not t.get("requires"),
        })
    # Technical debt isn't a bundled CLI — it's the app's own deterministic
    # scanner (debt.py). Surface it as a card so nothing is hidden; the UI routes
    # its Run to the Tech Debt tab.
    out.append({
        "id": "tech-debt", "title": "Technical debt", "desc":
        "TODO/FIXME markers, oversized files & over-long functions. Offline, no tokens.",
        "cat": "Checking", "needs": [], "note": "", "requires": "",
        "kind": "internal", "bg": False, "installed": True, "runnable": True,
    })
    return out


def _cmd_for(t: dict, path: str, base: str):
    args = [a.replace("{path}", path).replace("{base}", base) for a in t["cmd"]]
    runner = os.environ.get("ONEPORT_TOOL_RUNNER")
    if runner:
        return [*runner.split("|"), t["exe"], *args], args
    return [_resolve(t["exe"]) or t["exe"], *args], args


def _findings(data) -> list[dict]:
    if not isinstance(data, dict):
        return []
    items = None
    for k in ("findings", "issues", "gaps", "results", "vulnerabilities"):
        if isinstance(data.get(k), list):
            items = data[k]
            break
    if items is None:
        return []
    out = []
    for f in items[:200]:
        if not isinstance(f, dict):
            out.append({"title": str(f)[:240], "file": "", "line": "", "severity": ""})
            continue
        title = (f.get("title") or f.get("message") or f.get("rule") or f.get("name")
                 or f.get("detail") or f.get("description") or f.get("id")
                 or json.dumps(f)[:120])
        out.append({
            "title": str(title)[:240],
            "file": f.get("file") or f.get("path") or "",
            "line": f.get("line") or f.get("lineno") or "",
            "severity": str(f.get("severity") or f.get("level") or "").lower(),
        })
    return out


def run(tool_id: str, path: str, base: str = "main") -> dict:
    """Run one tool on `path`. Returns a normalised, honest result dict."""
    t = _spec(tool_id)
    if not t:
        return {"ok": False, "error": "unknown tool"}
    if t.get("requires"):
        return {"ok": False, "requires": t["requires"]}
    # git-diff tools can't run without real git history — surface a clear,
    # fixable note instead of git's cryptic "not a valid object" error.
    if t.get("in_path") or "git" in (t.get("needs") or []):
        need = 2 if "--head" in (t.get("cmd") or []) else 1
        ok, why = _git_ready(path, need)
        if not ok:
            return {"ok": False, "requires": why}

    cmd, args = _cmd_for(t, path, base)
    kind = t.get("kind", "text")
    label = " ".join([t["exe"], *args])
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=t.get("timeout", 240), cwd=path if t.get("in_path") else None,
            creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "cmd": label, "error": f"timed out after {t.get('timeout', 240)}s"}
    except OSError as exc:
        return {"ok": False, "cmd": label, "error": f"could not run: {exc}"}

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    rc = proc.returncode
    err_tail = err.splitlines()[-1][:220] if err else ""

    if kind == "findings":
        data = None
        if "{" in out:
            try:
                data = json.loads(out[out.index("{"):])
            except (ValueError, json.JSONDecodeError):
                data = None
        # rc 0 = clean, rc 1 with data = findings, anything else = a real error
        if rc == 0:
            return {"ok": True, "cmd": label, "kind": "findings", "clean": True,
                    "findings": _findings(data)}
        if rc == 1 and data is not None:
            return {"ok": True, "cmd": label, "kind": "findings", "clean": False,
                    "findings": _findings(data)}
        # A change-based tool with nothing to diff (fresh clone, no local edits) is
        # NOT an error — it just has nothing to check. Show a clear note, not red.
        blob = (err + " " + out).lower()
        if any(s in blob for s in ("no changed files", "no changes found",
                                   "nothing to compare", "nothing to check",
                                   "no change set", "no staged changes")):
            return {"ok": True, "cmd": label, "kind": "text",
                    "text": "No changes to compare.\n\nThis check looks at your pending "
                            "or last-committed changes against the base branch — and "
                            "there are none right now. Make or stage a change, then run "
                            "it again to see what it would flag."}
        return {"ok": False, "cmd": label, "kind": "findings",
                "error": err_tail or (out[:220] if out else "failed with no output")}

    # text tools
    text = out or err or "(no output)"
    if rc not in (0, 1) and not out:
        return {"ok": False, "cmd": label, "kind": "text", "error": err_tail or "failed"}
    return {"ok": True, "cmd": label, "kind": "text", "text": text[-12000:]}


def run_background(tool_id: str, path: str, base: str, term) -> dict:
    """Fire a slow tool (AI doc/context builders) in a thread, streaming to the
    in-app terminal. Returns immediately so the UI never hangs on a long AI run."""
    import threading
    t = _spec(tool_id)
    if not t:
        return {"ok": False, "error": "unknown tool"}
    if t.get("requires"):
        return {"ok": False, "requires": t["requires"]}

    def worker():
        term.emit(f"→ {t['title']}: running {t['exe']} … (this can take a while)", "cmd")
        res = run(tool_id, path, base)
        if res.get("ok"):
            term.emit(res.get("text") or "done.", "out")
            term.emit(f"✓ {t['title']} finished.", "cmd")
        else:
            term.emit(f"✕ {t['title']}: {res.get('error') or res.get('requires') or 'failed'}", "err")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "background": True}
