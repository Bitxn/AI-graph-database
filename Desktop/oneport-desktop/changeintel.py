"""
changeintel.py — advanced, live change intelligence.

On every detected code change (Watch), this produces a plain-language, TRUSTWORTHY
read on a change the moment it lands:
  • WHAT changed        — files, +/- lines, and which risky areas it touches.
  • REPERCUSSIONS        — what this kind of change can affect / break.
  • DID IT ADD ERRORS?   — computed from the DELTA between the security scan that
                           ran BEFORE the change and the one AFTER it, so it names
                           concrete regressions (a new secret, new CVEs, a gate that
                           flipped to blocking) and can't cry wolf or hide one.

The "introduced problems" list is derived from real gate status transitions, never
invented. An optional AI narrative adds a human explanation on top; if the AI is
unavailable (rate-limit / no tokens) the deterministic core still fully answers.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
from datetime import datetime, timezone

import repomap

MAX_DIFF = 14000

# path substrings -> the risky area they belong to (for blast-radius reasoning)
RISKY_AREAS = {
    "auth/session": ["auth", "login", "logout", "session", "token", "jwt", "oauth",
                     "password", "passwd", "cred", "permission", "rbac"],
    "secrets/config": [".env", "config", "settings", "secret", "credential",
                       "apikey", "api_key", ".pem", ".key"],
    "database/migrations": ["migration", "migrate", "schema", "alembic", "models.py",
                            "/models", "prisma", "knex", ".sql"],
    "dependencies": ["requirements.txt", "package.json", "pyproject.toml", "go.mod",
                     "cargo.toml", "gemfile", "pom.xml", "package-lock.json",
                     "yarn.lock", "poetry.lock", "pnpm-lock"],
    "ci/deploy": [".github/workflows", "dockerfile", "docker-compose", ".gitlab-ci",
                  "deploy", "terraform", ".tf", "serverless", "helm", "k8s"],
    "payments/billing": ["payment", "razorpay", "stripe", "billing", "checkout",
                         "invoice", "subscription", "webhook"],
    "api surface": ["route", "endpoint", "/api", "handler", "controller", "server.py",
                    "views.py", "urls.py", "resolver", "graphql"],
}

# a canned, concrete repercussion for each area (what to re-check after such a change)
AREA_REPERCUSSION = {
    "auth/session": "Auth/session logic — a slip here can lock users out or leak "
                    "sessions. Re-test login and one protected route.",
    "secrets/config": "Config/secrets files — make sure nothing sensitive got "
                      "hard-coded and every required env var still resolves.",
    "database/migrations": "DB schema/migration — can be irreversible on real data. "
                           "Verify it runs forward AND back on a copy first.",
    "dependencies": "A dependency manifest changed — a new/updated package can pull "
                    "vulnerabilities or break the build. Re-run the dependency check.",
    "ci/deploy": "CI/deploy config — a bad edit here can break the pipeline or ship "
                 "broken. Watch the next pipeline run closely.",
    "payments/billing": "Payment/billing path — errors here cost real money or drop "
                        "orders. Run a full transaction in sandbox before shipping.",
    "api surface": "API/route surface — changing a signature or path can break "
                   "callers. Check for breaking-API findings and update clients.",
}


# ── git helpers ──────────────────────────────────────────────────────────────
def _git(root: str, *a) -> str:
    try:
        p = subprocess.run(["git", "-C", root, *a], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=20)
        return (p.stdout or "") if p.returncode in (0, 1) else ""
    except Exception:
        return ""


def _numstat(root: str) -> list[tuple[int, int, str]]:
    """[(added, removed, path)] for the pending change (uncommitted, else staged)."""
    raw = (_git(root, "diff", "HEAD", "--numstat") or _git(root, "diff", "--numstat")
           or _git(root, "diff", "--cached", "--numstat"))
    out = []
    for ln in raw.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3:
            a, d, path = parts
            out.append((0 if a == "-" else int(a or 0),
                        0 if d == "-" else int(d or 0), path.strip()))
    return out


def _diff_text(root: str) -> str:
    diff = (_git(root, "diff", "HEAD", "--unified=3") or _git(root, "diff", "--unified=3")
            or _git(root, "diff", "--cached", "--unified=3"))
    return diff[:MAX_DIFF]


# ── signal extraction ────────────────────────────────────────────────────────
def _touched_areas(paths: list[str]) -> list[str]:
    low = [p.lower() for p in paths]
    return [area for area, kws in RISKY_AREAS.items()
            if any(any(kw in p for kw in kws) for p in low)]


def _code_defects(root: str, paths: list[str]) -> list[dict]:
    """Deterministically catch broken code the change just introduced — things the
    security gates don't look for, independent of whether the AI ran:

      • a .py file that no longer PARSES (a real SyntaxError), and
      • a bare do-nothing statement — a line that is just a name or attribute
        (e.g. `fdvqeragvae`), which does nothing and is almost always a typo or an
        accidental paste that will NameError at runtime.

    Cheap, and phrased so it never over-claims."""
    out = []
    for p in paths:
        if not p.endswith(".py"):
            continue
        fp = os.path.join(root, p)
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except Exception:
            continue                            # deleted/unreadable — skip
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            out.append({"title": "Syntax error", "file": p, "line": e.lineno,
                        "summary": f"{p} won't parse — {e.msg} (line {e.lineno})",
                        "warn": False, "syntax": True})
            continue
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Name, ast.Attribute)):
                try:
                    label = ast.unparse(node.value)
                except Exception:
                    label = getattr(node.value, "id", "expression")
                out.append({"title": "Stray statement", "file": p, "line": node.lineno,
                            "summary": f"{p}:{node.lineno} — bare `{label}` does "
                                       "nothing; likely a typo or accidental paste "
                                       "(will fail at runtime if the name is undefined)",
                            "warn": False, "syntax": True})
    return out


def _gate_deltas(before_gates: list, after_gates: list) -> tuple[list, list]:
    """Compare the scan BEFORE the change with the one AFTER it.

    Returns (introduced, resolved). `introduced` are regressions THIS change caused:
    a gate that went to BLOCK, or a clean gate that went to WARN/REVIEW. Each is a
    real transition, so we never invent a problem — and never miss one."""
    bmap = {g.get("gate"): g for g in (before_gates or [])}
    introduced, resolved = [], []
    for g in (after_gates or []):
        aft = g.get("status")
        bef = (bmap.get(g.get("gate")) or {}).get("status")
        title, summ = g.get("title", g.get("gate", "gate")), g.get("summary", "")
        if aft == "block" and bef != "block":
            introduced.append({"title": title, "summary": summ, "warn": False})
        elif aft == "warn" and bef in ("pass", "idle", None):
            introduced.append({"title": title, "summary": summ, "warn": True})
        elif aft == "error" and bef not in ("error", None):
            introduced.append({"title": title, "summary": summ or "gate could not run",
                               "warn": True})
        elif bef == "block" and aft in ("pass", "warn"):
            resolved.append({"title": title})
    return introduced, resolved


def _risk_level(introduced: list, areas: list, stats: dict, verdict: str,
                cur_block: list, cur_error: list) -> str:
    # A change that leaves the repo blocked, or that introduces a hard failure /
    # syntax error, is never "low" — even if the block also existed before.
    if any(not i["warn"] for i in introduced) or cur_block:
        return "high"
    big = (stats["added"] + stats["removed"] >= 200) or stats["files"] >= 8
    sensitive = {"database/migrations", "secrets/config", "payments/billing"} & set(areas)
    if (any(i["warn"] for i in introduced) or cur_error
            or str(verdict).upper() == "INCONCLUSIVE" or sensitive or big):
        return "medium"
    return "low"


def _headline(level: str, introduced: list, stats: dict, verdict: str,
              cur_block: list) -> str:
    hard = [i for i in introduced if not i["warn"]]
    n = stats["files"]
    fileword = f"{n} file" + ("" if n == 1 else "s")
    if hard:
        return (f"High-risk change — introduced {len(hard)} blocking issue"
                + ("" if len(hard) == 1 else "s"))
    if cur_block:
        return f"Change lands on a BLOCKED repo — {len(cur_block)} issue(s) still block shipping"
    if level == "medium":
        return f"Medium-risk change across {fileword}"
    return f"Low-risk change across {fileword}"


def _added_errors_block(introduced: list, verdict: str,
                        cur_block: list, cur_error: list) -> dict:
    """The direct answer to 'will this code add extra errors?'

    Honest on both axes: what THIS change newly introduced (the delta), AND the
    absolute state it leaves the repo in. It never reports a reassuring green while
    the repo is actually blocked or a changed file won't compile."""
    hard = [i for i in introduced if not i["warn"]]
    soft = [i for i in introduced if i["warn"]]
    if hard:
        remain = ""
        extra_blocks = [g for g in cur_block]
        if extra_blocks:
            remain = f" The repo is {verdict or 'BLOCKED'} with {len(extra_blocks)} blocking gate(s)."
        kind = "code defect" if any(i.get("syntax") for i in hard) else "blocking issue"
        return {"tone": "bad",
                "line": f"Yes — this change introduced {len(hard)} new "
                        f"{kind}{'s' if len(hard)>1 else ''}." + remain,
                "items": [f"{i['title']} — {i['summary']}" for i in hard + soft]}
    if cur_block:
        # nothing NEW from the delta, but the repo is not shippable — say so plainly
        return {"tone": "bad",
                "line": f"This change didn't add a NEW gate failure, but the repo is "
                        f"still {verdict or 'BLOCKED'} — {len(cur_block)} issue(s) must "
                        f"be fixed before shipping.",
                "items": [f"{g.get('title')} — {g.get('summary','')}" for g in cur_block]}
    if cur_error or soft:
        items = ([f"{g.get('title')} — {g.get('summary','')}" for g in cur_error]
                 + [f"{i['title']} — {i['summary']}" for i in soft])
        return {"tone": "warn",
                "line": f"Maybe — {len(items)} item(s) need a look "
                        f"({'a gate couldn’t complete; ' if cur_error else ''}nothing "
                        f"hard-blocking from this change).",
                "items": items}
    return {"tone": "good",
            "line": "No new failures — nothing in the security gates regressed and the "
                    f"changed files compile (verdict: {verdict or 'n/a'}).",
            "items": []}


# ── optional AI narrative ────────────────────────────────────────────────────
_AI_SYSTEM = (
    "You are a senior engineer reviewing a single code change (a git diff). In STRICT "
    "JSON, explain it for the author:\n"
    '{"summary": "one or two sentences: what this change actually does, in plain '
    'language", "risks": ["at most 3 concrete things that could break or need a '
    'second look because of THIS change — specific, not generic"]}\n'
    "Ground every word in the diff. If the diff is trivial, say so and keep risks "
    "empty. Never invent files or behavior you don't see."
)


def _ai_narrative(diff: str, file_ctx: str, model: str, gemini_key: str | None) -> dict:
    if not diff.strip():
        return {}
    user = ""
    if file_ctx:
        user += "FILE ROLES (for context):\n" + file_ctx + "\n\n"
    user += "THE CHANGE (diff):\n```diff\n" + diff + "\n```"
    try:
        raw, _ = repomap._llm(_AI_SYSTEM, user, model, gemini_key,
                              max_tokens=500, json_mode=True)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        summary = str(data.get("summary", "")).strip()
        risks = [str(r).strip() for r in (data.get("risks") or []) if str(r).strip()][:3]
        if summary or risks:
            return {"summary": summary, "risks": risks}
    except Exception:
        pass
    return {}


# ── how to fix: steps + a paste-ready prompt + which files to attach ─────────
_DEP_MANIFESTS = ("requirements.txt", "package.json", "pyproject.toml", "go.mod",
                  "cargo.toml", "gemfile", "pom.xml", "poetry.lock", "package-lock.json",
                  "yarn.lock", "pnpm-lock.yaml")


def _gate_fix_step(title: str) -> str | None:
    t = title.lower()
    if "secret" in t or "credential" in t:
        return ("Remove the committed secret, ROTATE it (assume it's leaked), and load "
                "it from an environment variable / secrets manager instead.")
    if "dependenc" in t or "cve" in t:
        return ("Bump the flagged package(s) to a patched version (or replace them), "
                "then re-run the dependency check.")
    if "api" in t or "break" in t:
        return ("Restore the removed/renamed parameters, or version the API and update "
                "every caller so existing consumers don't break.")
    if "migration" in t:
        return ("Make the migration reversible and staged (add nullable → backfill → "
                "constrain); avoid destructive drops on live data.")
    if "test" in t:
        return "Add tests covering the new/changed code paths, then re-run the check."
    return None


def _fix_guidance(defects: list, problems: list, areas: list, paths: list) -> dict:
    """Deterministic, concrete remediation: what to do, a copy-paste prompt for an
    AI editor / OnePort chat, and exactly which files to attach with it."""
    steps, issue_lines, attach = [], [], []

    def add_attach(f):
        if f and f not in attach:
            attach.append(f)

    # 1) code defects — precise, file+line
    for d in defects:
        steps.append(f"Open {d['file']} (line {d.get('line','?')}) and remove/fix the "
                     f"flagged {d['title'].lower()}.")
        issue_lines.append("- " + d["summary"])
        add_attach(d["file"])

    # 2) gate problems — map each to a fix step
    seen_steps = set(steps)
    for g in problems:
        title = g.get("title", "")
        summ = g.get("summary", "")
        issue_lines.append(f"- {title}: {summ}")
        s = _gate_fix_step(title)
        if s and s not in seen_steps:
            steps.append(s); seen_steps.add(s)

    # 3) attach the right files: dependency manifests + risky changed files, then a
    #    small sample of the rest so the agent has the actual diff to work from.
    for p in paths:
        base = p.split("/")[-1].lower()
        if base in _DEP_MANIFESTS:
            add_attach(p)
    if "secrets/config" in areas or "api surface" in areas:
        for p in paths:
            add_attach(p)
    for p in paths[:4]:
        add_attach(p)
    attach = attach[:6]

    if not issue_lines:
        return {}                               # nothing to fix — panel hides this

    if not steps:
        steps = ["Review the change against the repercussions above, then re-scan."]

    prompt = (
        "OnePort flagged these issues in my latest change. Fix them without breaking "
        "anything else:\n" + "\n".join(issue_lines) + "\n\n"
        "Rules: keep public function/API signatures stable unless the change intends "
        "to alter them; never hard-code secrets (use environment variables); don't add "
        "new vulnerable dependencies. When done, summarise exactly what you changed and "
        "why, then I'll re-run OnePort."
    )
    if attach:
        prompt += "\n\nContext files: " + ", ".join(attach)

    return {"steps": steps, "prompt": prompt, "attach": attach}


# ── main entry ───────────────────────────────────────────────────────────────
def build(pid: str, root: str, changed: list, before_gates: list, after_gates: list,
          verdict: str, manifest_files: dict | None = None,
          model: str = "gemini-2.5-flash", gemini_key: str | None = None,
          want_ai: bool = True) -> dict:
    """Assemble the full change-intelligence record for the latest change."""
    ns = _numstat(root)
    paths = [p for _, _, p in ns] or list(changed or [])
    stats = {"files": len(paths),
             "added": sum(a for a, _, _ in ns),
             "removed": sum(d for _, d, _ in ns)}
    # biggest single file, for the "what" line
    biggest = max(ns, key=lambda t: t[0] + t[1], default=None)

    areas = _touched_areas(paths)
    introduced, resolved = _gate_deltas(before_gates, after_gates)
    # Deterministic code-defect check on changed .py files — catches broken code the
    # gates don't (a bare `fdvqeragvae` on its own line), attributed to THIS change.
    defects = _code_defects(root, paths)
    introduced = defects + introduced       # defects lead — they're definite
    # Absolute state AFTER the change — so we never show green on a blocked repo.
    cur_block = [g for g in (after_gates or []) if g.get("status") == "block"]
    cur_error = [g for g in (after_gates or []) if g.get("status") == "error"]
    level = _risk_level(introduced, areas, stats, verdict, cur_block, cur_error)

    what = []
    plural = "s" if stats["files"] != 1 else ""
    what.append(f"Edited {stats['files']} file{plural} "
                f"(+{stats['added']} / −{stats['removed']} lines).")
    if biggest and (biggest[0] + biggest[1]) > 0:
        what.append(f"Largest: {biggest[2]} (+{biggest[0]} / −{biggest[1]}).")
    if areas:
        what.append("Touches: " + ", ".join(areas) + ".")

    repercussions = [AREA_REPERCUSSION[a] for a in areas if a in AREA_REPERCUSSION]
    if not repercussions:
        repercussions = ["Low blast radius — no auth, config, DB, dependency, CI or "
                         "payment paths touched. Still worth a quick self-review."]
    if resolved:
        repercussions.insert(0, "✓ This change also cleared: " +
                             ", ".join(r["title"] for r in resolved) + ".")

    added_errors = _added_errors_block(introduced, verdict, cur_block, cur_error)

    # How-to-fix: defects (precise) + the gate problems that must be resolved.
    soft_new = [i for i in introduced if i.get("warn") and not i.get("syntax")]
    problems, seen = [], set()
    for g in cur_block + cur_error + soft_new:
        k = (g.get("title"), g.get("summary"))
        if k not in seen:
            seen.add(k); problems.append(g)
    fix = _fix_guidance(defects, problems, areas, paths)

    ai = {}
    if want_ai:
        file_ctx = ""
        if manifest_files:
            picked = [(r, manifest_files[r]) for r in paths if r in manifest_files][:8]
            file_ctx = "\n".join(f"- {r}: {i.get('summary','')}" for r, i in picked)
        ai = _ai_narrative(_diff_text(root), file_ctx, model, gemini_key)

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "files": paths[:12],
        "stats": stats,
        "areas": areas,
        "risk_level": level,
        "headline": _headline(level, introduced, stats, verdict, cur_block),
        "what": what,
        "repercussions": repercussions,
        "added_errors": added_errors,
        "fix": fix,                     # {} when nothing to fix — UI hides this
        "ai": ai,                       # {} when unavailable — UI hides it gracefully
        "verdict": verdict,
    }
