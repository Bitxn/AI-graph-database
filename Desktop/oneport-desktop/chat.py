"""
chat.py — the change assistant.

Answers questions about a repo grounded in THREE real sources: the mind-map
(what each file is), the current git diff (what just changed), and the latest
gate results (whether it's safe). Every answer is tied to that evidence — if the
answer isn't in the context, it says so rather than inventing.

History persists per project. Uses the same LLM path as the rest of the app
(managed proxy, or a BYOK Gemini key).
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import repomap
from projects import DATA_DIR

CHAT_DIR = DATA_DIR / "chats"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_DIFF = 12000
MAX_TURNS = 8            # conversation turns fed back for continuity
MAX_STORED = 120         # messages kept on disk


# ── history store ────────────────────────────────────────────────────────────
def _path(pid: str) -> Path:
    return CHAT_DIR / f"{pid}.json"


def load_chat(pid: str) -> list[dict]:
    p = _path(pid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
    return []


def _save(pid: str, msgs: list[dict]) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    _path(pid).write_text(json.dumps(msgs[-MAX_STORED:], indent=2), encoding="utf-8")


def add_message(pid: str, role: str, text: str, tokens: int = 0,
                steps: list | None = None) -> None:
    msgs = load_chat(pid)
    msg = {"role": role, "text": text, "tokens": tokens,
           "ts": datetime.now(timezone.utc).isoformat()}
    if steps:
        msg["steps"] = steps
    msgs.append(msg)
    _save(pid, msgs)


def clear_chat(pid: str) -> None:
    _save(pid, [])


# ── context assembly ─────────────────────────────────────────────────────────
def _current_diff(root: str) -> str:
    """All pending changes (uncommitted, staged+unstaged) — 'what just changed'."""
    def git(*a):
        try:
            p = subprocess.run(["git", "-C", root, *a], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=20)
            return (p.stdout or "").strip() if p.returncode in (0, 1) else ""
        except Exception:
            return ""
    diff = git("diff", "HEAD", "--unified=4") or git("diff", "--unified=4") \
        or git("diff", "--cached", "--unified=4")
    return diff[:MAX_DIFF]


def _changed_files(diff: str) -> list[str]:
    return [ln[6:].strip() for ln in diff.splitlines()
            if ln.startswith("+++ b/") and ln[6:].strip() != "/dev/null"]


def _context(pid: str, root: str, gate_entry: dict | None) -> str:
    manifest = repomap.load_manifest(pid)
    files = manifest.get("files", {})
    diff = _current_diff(root)
    changed = _changed_files(diff)

    parts = []
    if manifest.get("readme"):
        parts.append("REPO OVERVIEW:\n" + manifest["readme"][:1500])

    # Prefer the summaries of files that actually changed; else a sample.
    picked = [(r, files[r]) for r in changed if r in files]
    if not picked:
        picked = list(files.items())[:15]
    if picked:
        parts.append("RELEVANT FILES (role — what it does):\n" + "\n".join(
            f"- {r} [{i.get('role')}] {i.get('summary')}" for r, i in picked[:25]))

    parts.append("CURRENT DIFF (uncommitted changes):\n" +
                 (f"```diff\n{diff}\n```" if diff else "(no uncommitted changes right now)"))

    if gate_entry:
        gl = "\n".join(f"- {g['title']}: {g['status']} — {g['summary']}"
                       for g in gate_entry.get("gates", []))
        parts.append(f"LATEST GATE RESULTS — verdict {gate_entry.get('verdict', '?')}:\n{gl}")

    return "\n\n".join(parts)


# Stable, factual knowledge about the product itself. This lets the assistant
# answer a new user's first questions ("what is this?", "who built it?", "what do
# I do?") instead of refusing them — these are NOT repo facts, so they were never
# in the repo context. Kept short and truthful; no marketing invention.
ABOUT_ONEPORT = (
    "ABOUT ONEPORT (product facts you always know):\n"
    "- OnePort is a security & verification layer for code — especially AI-written "
    "code. This desktop app is its \"Security Cockpit\": you point it at a repo and "
    "it gives you a trustworthy ship / don't-ship verdict.\n"
    "- It runs a suite of gates/tools over the repo: secret & credential scanning "
    "(working tree + full git history), dependency CVEs (via OSV), breaking API-change "
    "detection, risky DB migrations, test-coverage gaps, AI code review, "
    "blast-radius/impact, intent-conformance, technical-debt, and doc/context-map "
    "generation. It also has OnePort Guard, which makes a repo unable to commit a "
    "secret (git + agent hooks + CI).\n"
    "- Why it's useful: AI writes code fast but can leak secrets, pull vulnerable "
    "deps, break APIs, or skip tests. OnePort catches that BEFORE it ships, and "
    "explains each finding in plain language so a non-expert can act on it.\n"
    "- Who built it: OnePort is built by its founder, Bitan. This assistant is "
    "OnePort's built-in agent (running on OnePort's managed AI).\n"
    "- What a new user does: open a repo (New project or Import from Git), run "
    "Quick scan (free, deterministic gates) or Test for Production (adds the AI "
    "gates), read the verdict, then use this chat to understand and fix findings."
)

SYSTEM = (
    "You are OnePort's assistant for a developer using the OnePort desktop app. "
    "You have two jobs:\n"
    "1) Answer questions ABOUT ONEPORT itself — what it does, whether it's useful, "
    "who built it, and what the user should do next — using the ABOUT ONEPORT facts "
    "below. Always answer these; they are general product questions, not repo facts. "
    "A new user asking \"what is this / is it useful / who made you / what do I do\" "
    "gets a warm, concrete answer that helps them get started.\n"
    "2) Answer questions about THIS repository grounded in the provided context: the "
    "repo overview, file summaries, the current diff, and the latest gate results.\n"
    "- Explain what a change does and its impact, citing specific files.\n"
    "- For 'is it safe?' questions, reason from the gate results and the diff — "
    "state the verdict plainly and what would need fixing.\n"
    "- For REPO-SPECIFIC facts not in the provided context, say you don't see it in "
    "this repo yet (and suggest building context / scanning) — but NEVER refuse a "
    "product or onboarding question for lack of repo context, and never invent "
    "files, functions, or findings.\n"
    "- Be concise, concrete and friendly. Developer audience.\n\n"
    + ABOUT_ONEPORT
)


def ask(pid: str, root: str, question: str, gate_entry: dict | None = None,
        model: str = DEFAULT_MODEL, gemini_key: str | None = None) -> dict:
    ctx = _context(pid, root, gate_entry)
    history = load_chat(pid)[-MAX_TURNS * 2:]
    convo = "\n".join(f"{m['role'].upper()}: {m['text']}" for m in history)

    user = ctx + "\n\n---\n"
    if convo:
        user += "CONVERSATION SO FAR:\n" + convo + "\n\n"
    user += "DEVELOPER QUESTION: " + question

    try:
        answer, tokens = repomap._llm(SYSTEM, user, model, gemini_key,
                                      max_tokens=900, json_mode=False)
    except Exception as exc:
        return {"error": f"Chat failed: {exc}"}

    answer = answer.strip()
    return {"answer": answer, "tokens": tokens}


# ── agent: live progress store (per project, in-memory) ──────────────────────
_RUNS: dict[str, dict] = {}
_RUN_LOCK = threading.Lock()


def start_run(pid: str, message: str) -> None:
    with _RUN_LOCK:
        _RUNS[pid] = {"active": True, "done": False, "message": message,
                      "steps": [], "reply": "", "tokens": 0}


def is_active(pid: str) -> bool:
    with _RUN_LOCK:
        r = _RUNS.get(pid)
        return bool(r and r["active"])


def step(pid: str, text: str) -> None:
    """Begin a step (auto-completes any still-running step before it)."""
    with _RUN_LOCK:
        r = _RUNS.get(pid)
        if not r:
            return
        for s in r["steps"]:
            if s["status"] == "running":
                s["status"] = "done"
        r["steps"].append({"text": text, "status": "running", "result": ""})


def endstep(pid: str, result: str = "", status: str = "done") -> None:
    with _RUN_LOCK:
        r = _RUNS.get(pid)
        if r and r["steps"]:
            r["steps"][-1]["status"] = status
            r["steps"][-1]["result"] = result[:400]


def finish_run(pid: str, reply: str, tokens: int = 0) -> None:
    with _RUN_LOCK:
        r = _RUNS.get(pid)
        if r:
            for s in r["steps"]:
                if s["status"] == "running":
                    s["status"] = "done"
            r.update(active=False, done=True, reply=reply, tokens=tokens)


def progress(pid: str) -> dict:
    with _RUN_LOCK:
        r = _RUNS.get(pid)
        if not r:
            return {"active": False, "done": True, "steps": [], "reply": ""}
        return {"active": r["active"], "done": r["done"],
                "steps": [dict(s) for s in r["steps"]],
                "reply": r["reply"], "tokens": r["tokens"]}


# ── agent: intent planner ────────────────────────────────────────────────────
# The chat is an AGENT: it decides which real actions to take, executes them
# (same code paths as the buttons), and reports. It never fabricates results.
ACTIONS = ("build_context", "scan", "run_tool", "debt", "fix", "guide", "answer")

PLAN_SYSTEM = (
    "You are the planner for OnePort's repo agent. Read the developer's message and "
    "decide which REAL actions to take, in order. Reply with STRICT JSON only:\n"
    '{"actions": [{"do": "...", ...}], "note": "one short line on what you will do"}\n'
    "Allowed actions (use only these):\n"
    "- build_context  : (re)build the repo's context map / README. Use for "
    "\"generate/build/update context\", \"map the repo\", \"index\".\n"
    "- scan           : run the security gates. Add \"mode\":\"production\" for the "
    "full AI check, else \"quick\". Use for \"scan\", \"check\", \"run it again\", "
    "\"test for production\".\n"
    "- run_tool       : run ONE named tool. Add \"tool\":\"<id>\" from: secrets, "
    "dependencies, migrations, api-breaks, review, test-gaps, conformance, impact, "
    "standup, upgrade, costwatch, context, docgen.\n"
    "- debt           : scan technical debt.\n"
    "- fix            : auto-fix a blocking gate with AI. Add \"gate\":\"<id>\".\n"
    "- guide          : look up the rulebook for what the user should do about an "
    "error/warning. Use for \"what do I do\", \"how do I fix this\", \"what's this error\".\n"
    "- answer         : just answer a question about the repo (the default).\n"
    "Chain actions when asked (e.g. \"build context and scan again\" -> "
    "[build_context, scan]). If it's only a question, use a single answer action. "
    "Keep it minimal — don't add actions the user didn't ask for."
)


_TOOLKW = {  # keyword -> tool id (checked before the generic "scan")
    "secret": "secrets", "credential": "secrets",
    "dependency": "dependencies", "dependencies": "dependencies", "cve": "dependencies",
    "migration": "migrations", "api break": "api-breaks", "api-break": "api-breaks",
    "code review": "review", "review": "review", "test gap": "test-gaps",
    "test coverage": "test-gaps", "conformance": "conformance",
    "blast radius": "impact", "impact": "impact", "standup": "standup",
    "upgrade": "upgrade", "cost": "costwatch", "docs": "docgen",
    "documentation": "docgen", "design doc": "docgen",
}


def _heuristic_plan(msg: str) -> list[dict]:
    """No-LLM fallback so the agent still routes basic intents. The LLM planner is
    primary; this keeps the agent useful offline / on rate-limit."""
    m = msg.lower()
    # 1) "what should I do / how to fix / what's this error" -> rulebook guidance
    if re.search(r"(what should i|what do i do|how do i fix|how to fix|what.?s this error|"
                 r"what is this error|help me fix|guidance)", m):
        return [{"do": "guide"}]
    acts: list[dict] = []
    # 2) context map
    if re.search(r"(context map|build .*context|generate .*context|update .*context|"
                 r"mind ?map|re-?index|index the repo|understand the repo)", m):
        acts.append({"do": "build_context"})
    # 3) technical debt (exclusive of the generic scan)
    debt = bool(re.search(r"technical debt|\bdebt\b|\btodos?\b|\bfixmes?\b", m))
    # 4) a specific named tool, before the generic scan
    tool = next((tid for kw, tid in _TOOLKW.items() if kw in m), None)
    if debt:
        acts.append({"do": "debt"})
    elif tool:
        acts.append({"do": "run_tool", "tool": tool})
    # 5) generic scan (explicit verbs only — never "safe"/"ship", those are questions)
    if not tool and not debt and re.search(
            r"\b(scan|re-?scan|run it again|run again|check it|run the gates?|test for production)\b", m):
        acts.append({"do": "scan", "mode": "production" if "production" in m else "quick"})
    if not acts:
        acts.append({"do": "answer"})
    return acts


def is_status_question(msg: str) -> bool:
    """A safety/status question we can answer from the scan results WITHOUT AI —
    so the most common chat query is instant, free, and never fails."""
    m = msg.lower()
    return bool(re.search(
        r"\b(safe|safety|is it ok|is it okay|status|verdict|ready to ship|"
        r"ready for prod|can i ship|can i deploy|good to ship|ship it|"
        r"how safe|what.s blocking|whats blocking|any blockers)\b", m))


def status_answer(proj: dict) -> str:
    """Deterministic safety summary from the latest scan — zero tokens."""
    hist = proj.get("history") or []
    if not hist:
        return ("This repo hasn't been scanned yet, so I can't judge safety.\n\n"
                "Run **Quick scan** (free, deterministic gates) or **Test for "
                "Production** (adds the AI code-review & test-gap gates) — then ask "
                "me again and I'll give you the verdict.")
    e = hist[0]
    verdict = e.get("verdict", "?")
    gates = e.get("gates", [])
    block = [g for g in gates if g.get("status") == "block"]
    err = [g for g in gates if g.get("status") == "error"]
    skip = [g for g in gates if g.get("status") == "skip"]
    out = [f"**Last scan verdict: {verdict}.**"]
    if block:
        out.append("Blocking — fix these before shipping:\n" +
                   "\n".join(f"- **{g['title']}** — {g.get('summary','')}" for g in block))
    if err:
        out.append("Couldn't complete (so I can't vouch for these):\n" +
                   "\n".join(f"- **{g['title']}** — {g.get('summary','')}" for g in err))
    if not block and not err:
        if any(g.get("status") == "skip" and "Test for Production" in (g.get("summary") or "")
               for g in skip):
            out.append("Nothing is blocking. Some AI gates were deferred — run **Test "
                       "for Production** for the full verdict.")
        else:
            out.append("Nothing is blocking — you're clear to ship. ✅")
    return "\n\n".join(out)


def is_meta_question(msg: str) -> bool:
    """A product / onboarding question we can answer WITHOUT AI — what OnePort is,
    whether it's useful, who built it, and what a new user should do. These are the
    most common first questions, so we answer them instantly and never fail."""
    m = msg.lower()
    return bool(re.search(
        r"\b(what (is|does) (this|oneport|it)|what.?s this (app|product|tool|thing)|"
        r"what can (you|this|it) do|what do you do|is (it|this|oneport) (useful|worth|good)|"
        r"why (should i|would i) use|who (built|made|created|are) (you|this|it|u)|"
        r"who (built|made|created) oneport|what am i (looking at|supposed to do)|"
        r"how (do i|to) (start|begin|get started|use this)|getting started|new here|"
        r"i.?m new|tell me (about|what) (this|it|oneport))\b", m))


def meta_answer(msg: str) -> str:
    """Deterministic product/onboarding answer — zero tokens, always works."""
    return (
        "**OnePort is a security & verification layer for code — especially "
        "AI-written code.** This desktop app is its *Security Cockpit*: point it at a "
        "repo and it tells you, in plain language, whether it's safe to ship.\n\n"
        "**What it checks** — committed secrets & credentials (working tree + full git "
        "history), vulnerable dependencies (CVEs), breaking API changes, risky DB "
        "migrations, test-coverage gaps, code-review issues, blast radius, technical "
        "debt, and more. **OnePort Guard** can even make a repo *unable* to commit a "
        "secret.\n\n"
        "**Why it's useful** — AI writes code fast, but it can leak keys, pull unsafe "
        "packages, or break things. OnePort catches that *before* it ships and explains "
        "each finding so you know exactly what to do.\n\n"
        "**Who built it** — OnePort's founder, Bitan. I'm OnePort's built-in agent.\n\n"
        "**What to do now:**\n"
        "1. Make sure a repo is open (you have one).\n"
        "2. Hit **Quick scan** (free) or **Test for Production** (adds AI gates).\n"
        "3. Read the verdict up top, then ask me *\"what's blocking?\"* or *\"how do I "
        "fix this?\"* — I'll walk you through it."
    )


def friendly_error(err: str) -> str:
    """Turn a raw AI failure into a clear, honest reply that keeps the user moving."""
    e = (err or "").lower()
    if "out of tokens" in e or "top up" in e:
        return ("You're out of AI tokens, so I can't run the AI answer right now.\n\n"
                "Top up your OnePort balance to re-enable AI answers. In the meantime I "
                "can still answer **what OnePort does**, **safety/status** questions, and "
                "**run any tool** — all without tokens.")
    if "rate" in e and "limit" in e:
        return ("The AI model is busy for a moment. Wait ~a minute and try again. I can "
                "still answer product, safety and status questions and run tools now.")
    return "I hit an error answering that: " + err


def plan(message: str, model: str = DEFAULT_MODEL,
         gemini_key: str | None = None) -> dict:
    """Decide the action list. Heuristic-FIRST so planning is instant and free —
    the LLM is spent on the actual work (answering, building), not on routing.

    Only when the heuristic can't tell it's an action (it defaults to a plain
    'answer') AND an AI path is configured do we let the LLM look for a hidden
    action request — and only for short messages, so a normal question stays snappy."""
    acts = _heuristic_plan(message)
    if acts != [{"do": "answer"}]:
        return {"actions": acts, "note": ""}
    if len(message) <= 60 and re.search(
            r"\b(run|build|generate|make|create|fix|scan|check|update|do)\b", message.lower()):
        try:
            raw, _ = repomap._llm(PLAN_SYSTEM, "DEVELOPER MESSAGE: " + message,
                                  model, gemini_key, max_tokens=200, json_mode=True)
            data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            llm_acts = [a for a in data.get("actions", [])
                        if isinstance(a, dict) and a.get("do") in ACTIONS]
            if llm_acts:
                return {"actions": llm_acts, "note": data.get("note", "")}
        except Exception:
            pass
    return {"actions": acts, "note": ""}
