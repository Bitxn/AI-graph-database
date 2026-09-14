"""
OnePort Verify — the intent × implementation verification layer.

The user states what the repo is *supposed* to do; the AI reads the actual code
and verifies whether it does that — claim by claim, with file:line evidence.
It also reports, from the code alone, what the repo *actually* does (so drift is
visible), plus behaviours the user never mentioned (risks / extras / dead code)
and the implicit assumptions the code makes.

One structured LLM call returns the whole report. Reuses the managed AI engine
and the repo map from repomap.py. Reports are cached per project so a run
survives an app restart and can be compared over time.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import repomap
from projects import DATA_DIR

VERIFY_DIR = DATA_DIR / "verify"
MAX_CTX_CHARS = 26000        # total code context budget sent to the model
MAX_EVIDENCE_FILES = 14      # how many real source files to include (numbered)
MAX_FILE_LINES = 140         # lines per included file


# ── store ────────────────────────────────────────────────────────────────────
def _path(pid: str) -> Path:
    return VERIFY_DIR / f"{pid}.json"


def load_last(pid: str) -> dict:
    p = _path(pid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"intent": "", "report": None, "ran_at": None}


def _save(pid: str, intent: str, report: dict, tokens: int) -> None:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    _path(pid).write_text(json.dumps({
        "intent": intent, "report": report, "tokens": tokens,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")


# ── context ──────────────────────────────────────────────────────────────────
def _rank_files(manifest: dict, files: list[str], root: str) -> list[str]:
    """Prefer entrypoints / api / core files so the evidence is where behaviour
    actually lives, then fill with the rest."""
    weight = {"entrypoint": 0, "api": 1, "route": 1, "core": 2, "service": 2,
              "model": 3, "config": 4, "util": 5, "test": 8}
    fmap = manifest.get("files", {})

    def key(rel: str):
        role = (fmap.get(rel, {}) or {}).get("role", "")
        w = min((weight[k] for k in weight if k in role.lower()), default=6)
        return (w, len(rel))

    rels = []
    rlen = len(root.rstrip("/\\")) + 1
    for f in files:
        rel = f[rlen:].replace("\\", "/") if f.startswith(root) else f.replace("\\", "/")
        rels.append(rel)
    rels.sort(key=key)
    return rels


def _numbered(root: str, rel: str) -> str:
    try:
        txt = Path(root, rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = txt.splitlines()[:MAX_FILE_LINES]
    return "\n".join(f"{i + 1:>4}| {ln}" for i, ln in enumerate(lines))


def _gather_context(pid: str, root: str) -> str:
    manifest = repomap.load_manifest(pid)
    parts: list[str] = []

    if manifest.get("readme"):
        parts.append("REPO OVERVIEW (auto-generated):\n" + manifest["readme"][:1800])

    fmap = manifest.get("files", {})
    if fmap:
        lines = [f"- {r} [{i.get('role','?')}] — {i.get('summary','')}"
                 for r, i in sorted(fmap.items())]
        parts.append("FILE MAP:\n" + "\n".join(lines[:260]))

    # real, numbered source for citeable evidence
    try:
        srcs = repomap._source_files(root)
    except Exception:
        srcs = []
    ranked = _rank_files(manifest, srcs, root)

    parts.append("SOURCE (numbered for file:line evidence):")
    used = len("\n\n".join(parts))
    for rel in ranked[:MAX_EVIDENCE_FILES]:
        body = _numbered(root, rel)
        if not body:
            continue
        block = f"\n=== {rel} ===\n{body}"
        if used + len(block) > MAX_CTX_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


# ── prompts ──────────────────────────────────────────────────────────────────
_VERIFY_SYS = (
    "You are OnePort Verify, a rigorous code-verification auditor. You are given "
    "(1) a developer's stated INTENT — what the repository is supposed to do — and "
    "(2) the repository itself: an overview, a file map, and numbered source files.\n\n"
    "Your job is to VERIFY whether the code actually does what the intent claims. "
    "Judge ONLY from the evidence in the provided code. Never invent files, lines, "
    "or behaviour. Every claim verdict MUST cite concrete evidence as \"path:line\" "
    "using the numbered source; if you cannot find evidence, say so and mark it "
    "MISSING or INCONCLUSIVE rather than guessing.\n\n"
    "Return STRICT JSON, no prose, with exactly this shape:\n"
    "{\n"
    '  "coverage": <int 0-100, how much of the intent the code actually implements>,\n'
    '  "verdict": "VERIFIED" | "DIVERGENT" | "INCONCLUSIVE",\n'
    '  "summary": "<=2 sentences, plain English, the bottom line",\n'
    '  "reverse_readout": "2-4 sentences describing what the code ACTUALLY does, '
    'derived from the code alone, ignoring the stated intent",\n'
    '  "claims": [ { "claim": "one capability drawn from the intent", '
    '"verdict": "VERIFIED"|"PARTIAL"|"MISSING"|"CONTRADICTED", '
    '"confidence": <int 0-100>, "evidence": ["path:line", ...], '
    '"note": "one sentence: how the code supports or fails this claim" } ],\n'
    '  "unclaimed": [ { "kind": "RISK"|"EXTRA"|"DEAD", "title": "short", '
    '"detail": "what the code does that the intent never mentioned", '
    '"evidence": ["path:line", ...] } ],\n'
    '  "assumptions": [ { "assumption": "an implicit assumption the code makes '
    '(env var, external service, input shape)", "validated": true|false, '
    '"note": "is it guarded/checked?" } ],\n'
    '  "next_steps": [ "concrete action to close the biggest gap", ... ]\n'
    "}\n\n"
    "Rules: verdict is VERIFIED only if coverage>=80 AND there are no CONTRADICTED "
    "claims; DIVERGENT if any CONTRADICTED claim exists or coverage<55; otherwise "
    "INCONCLUSIVE. Extract 4-9 concrete claims from the intent. Prefer specific, "
    "falsifiable claims over vague ones. Keep every string tight."
)

_ALLOWED_CLAIM = {"VERIFIED", "PARTIAL", "MISSING", "CONTRADICTED"}
_ALLOWED_KIND = {"RISK", "EXTRA", "DEAD"}


def _coerce(report: dict, intent: str) -> dict:
    """Make the model output safe to render, and enforce the verdict rule."""
    r = report or {}
    try:
        cov = int(round(float(r.get("coverage", 0))))
    except (TypeError, ValueError):
        cov = 0
    cov = max(0, min(100, cov))

    claims = []
    for c in (r.get("claims") or [])[:12]:
        v = str(c.get("verdict", "")).upper()
        if v not in _ALLOWED_CLAIM:
            v = "MISSING"
        try:
            conf = max(0, min(100, int(round(float(c.get("confidence", 0))))))
        except (TypeError, ValueError):
            conf = 0
        ev = [str(e) for e in (c.get("evidence") or []) if e][:6]
        claims.append({"claim": str(c.get("claim", "")).strip() or "(claim)",
                       "verdict": v, "confidence": conf, "evidence": ev,
                       "note": str(c.get("note", "")).strip()})

    unclaimed = []
    for u in (r.get("unclaimed") or [])[:12]:
        k = str(u.get("kind", "")).upper()
        if k not in _ALLOWED_KIND:
            k = "EXTRA"
        unclaimed.append({"kind": k, "title": str(u.get("title", "")).strip() or "(item)",
                          "detail": str(u.get("detail", "")).strip(),
                          "evidence": [str(e) for e in (u.get("evidence") or []) if e][:6]})

    assumptions = []
    for a in (r.get("assumptions") or [])[:12]:
        assumptions.append({"assumption": str(a.get("assumption", "")).strip(),
                            "validated": bool(a.get("validated")),
                            "note": str(a.get("note", "")).strip()})

    steps = [str(s).strip() for s in (r.get("next_steps") or [])[:8] if str(s).strip()]

    # enforce the verdict rule regardless of what the model said
    has_contra = any(c["verdict"] == "CONTRADICTED" for c in claims)
    if cov >= 80 and not has_contra:
        verdict = "VERIFIED"
    elif has_contra or cov < 55:
        verdict = "DIVERGENT"
    else:
        verdict = "INCONCLUSIVE"

    return {
        "coverage": cov, "verdict": verdict,
        "summary": str(r.get("summary", "")).strip() or "No summary produced.",
        "reverse_readout": str(r.get("reverse_readout", "")).strip(),
        "claims": claims, "unclaimed": unclaimed,
        "assumptions": assumptions, "next_steps": steps,
        "counts": {
            "verified": sum(c["verdict"] == "VERIFIED" for c in claims),
            "partial": sum(c["verdict"] == "PARTIAL" for c in claims),
            "missing": sum(c["verdict"] == "MISSING" for c in claims),
            "contradicted": sum(c["verdict"] == "CONTRADICTED" for c in claims),
            "risks": sum(u["kind"] == "RISK" for u in unclaimed),
        },
    }


# ── public API ───────────────────────────────────────────────────────────────
def draft_intent(pid: str, model: str, gemini_key: str | None = None) -> dict:
    """Reverse-engineer a first-draft intent from the code, so the user has
    something to edit instead of a blank box."""
    try:
        res = repomap.generate_intent(pid, model=model, gemini_key=gemini_key)
        return {"ok": True, "intent": res.get("markdown", ""),
                "tokens": res.get("tokens", 0), "source": res.get("source", "ai")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "intent": "", "tokens": 0}


def run(pid: str, root: str, intent: str, model: str,
        gemini_key: str | None = None) -> dict:
    intent = (intent or "").strip()
    if not intent:
        return {"ok": False, "error": "Describe what the repo is supposed to do first."}

    manifest = repomap.load_manifest(pid)
    if not manifest.get("files"):
        return {"ok": False, "error": "Build the repo map first (Mind Map → Build map), "
                "then verify — I need to read the code to check it."}

    ctx = _gather_context(pid, root)
    user = (f"STATED INTENT (what this repo is supposed to do):\n{intent[:6000]}\n\n"
            f"REPOSITORY:\n{ctx}")

    try:
        raw, tokens = repomap._llm(_VERIFY_SYS, user, model, gemini_key,
                                   max_tokens=2600, json_mode=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"AI engine error: {e}"}

    parsed = repomap._parse_json(raw)
    if not parsed:
        return {"ok": False, "error": "Could not read the AI response — try again.",
                "tokens": tokens}

    report = _coerce(parsed, intent)
    _save(pid, intent, report, tokens)
    return {"ok": True, "report": report, "tokens": tokens,
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ── auto-verify on change: re-check the SAVED intent, alert only on drift ─────
_last_auto: dict[str, float] = {}   # pid -> monotonic ts of last auto run (debounce)


def auto_verify(pid: str, root: str, model: str, gemini_key: str | None = None,
                min_gap: float = 90.0) -> dict | None:
    """Called from the Watch loop after a change. Re-runs verification against
    the intent the user already saved, debounced so rapid saves don't burn
    tokens. Returns an ALERT dict only when the code has DRIFTED from intent —
    otherwise None (no news = no notification)."""
    st = load_last(pid)
    intent = (st.get("intent") or "").strip()
    if not intent:
        return None                              # user never set an intent — nothing to check against
    now = time.time()
    if now - _last_auto.get(pid, 0.0) < min_gap:
        return None
    prev = st.get("report")
    res = run(pid, root, intent, model, gemini_key=gemini_key)   # saves the fresh report
    _last_auto[pid] = now
    if not res.get("ok"):
        return None
    return _deviation_alert(prev, res["report"], intent, res.get("tokens", 0))


def _deviation_alert(prev: dict | None, new: dict, intent: str, tokens: int) -> dict | None:
    contradicted = [c for c in new["claims"] if c["verdict"] == "CONTRADICTED"]
    missing = [c for c in new["claims"] if c["verdict"] == "MISSING"]
    if new["verdict"] != "DIVERGENT" and not contradicted:
        return None                              # still on track — stay quiet

    prev_ok = ({c["claim"] for c in (prev or {}).get("claims", [])
                if c["verdict"] in ("VERIFIED", "PARTIAL")} if prev else set())
    lead = contradicted or missing
    regressed = [c for c in lead if c["claim"] in prev_ok]   # was fine before, broke now
    focus = regressed or lead

    lines, files = [], []
    for c in focus[:4]:
        ev = (c.get("evidence") or [""])[0]
        lines.append(f"- {c['verdict']}: {c['claim']}"
                     + (f"  ({ev})" if ev else "")
                     + (f" — {c['note']}" if c.get("note") else ""))
        for e in c.get("evidence", []):
            f = str(e).split(":")[0]
            if f and f not in files:
                files.append(f)

    steps = new.get("next_steps", [])[:3]
    prompt = (
        "OnePort Verify flagged that my code has drifted from what it's supposed to do "
        f"(verdict {new['verdict']}, {new['coverage']}% match). Get it back on track.\n\n"
        f"What it should do:\n{intent[:800]}\n\n"
        "What broke:\n" + "\n".join(lines)
        + (("\n\nSuggested fixes:\n" + "\n".join("- " + s for s in steps)) if steps else "")
        + "\n\nOpen these files: " + (", ".join(files[:6]) or "(see the evidence above)")
        + "\nFix the code so it matches the intent again."
    )
    label = "regressed from your intent" if regressed else "drifted from your intent"
    return {"kind": "verify", "level": "diverge",
            "title": f"Code {label}",
            "detail": new.get("summary", "") or f"{new['verdict']} — {new['coverage']}% match",
            "prompt": prompt, "files": files[:6],
            "verdict": new["verdict"], "coverage": new["coverage"], "tokens": tokens}
