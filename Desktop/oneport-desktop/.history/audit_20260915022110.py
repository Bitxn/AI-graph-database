"""
OnePort — Full Audit pipeline.

Runs the whole desktop tool suite on a repo in proper phases and produces one
final report, on a BACKGROUND thread with live progress the UI can poll:

  Phase 1  CHECKS      every runnable check tool (deterministic + AI), each
                       output recorded honestly (pass / findings / skip / error).
  Phase 2  SYNTHESIS   one AI pass reads ALL Phase-1 outputs and writes an
                       executive summary + ranked risks.
  Phase 3  REPORT      the slow AI doc generators (context, docgen) then the
                       Production-Readiness report artifact. The run is written to
                       project history so the verdict + report reflect it.

Tools that genuinely need external input (evidence, apiwatch, postmortem) are
shown as skipped-with-reason — never faked, per the catalog's honesty rules.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import toolspec
import debt
import report
import artifacts
import repomap
import projects
import settings

# Phase order (ids from toolspec.TOOLS + the app's internal tech-debt scanner).
_CHECK_ORDER = ["secrets", "dependencies", "costwatch", "migrations", "api-breaks",
                "impact", "tech-debt", "conformance", "test-gaps", "review",
                "standup", "upgrade"]
_DOC_ORDER = ["context", "docgen"]                 # slow AI doc generators → phase 3
_SKIP_IDS = ["evidence", "apiwatch", "postmortem"]  # need external input — shown, never faked

_runs: dict[str, dict] = {}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bal():
    """Balance probe, imported lazily to avoid a circular import with server.py."""
    try:
        import server
        return server._balance()
    except Exception:
        return None


def progress(pid: str) -> dict:
    with _lock:
        return dict(_runs.get(pid) or {"status": "idle"})


def is_running(pid: str) -> bool:
    with _lock:
        return (_runs.get(pid) or {}).get("status") == "running"


def _set(pid, **kw):
    with _lock:
        _runs.setdefault(pid, {}).update(kw)


def _set_tool(pid, tid, **kw):
    with _lock:
        for t in (_runs.get(pid) or {}).get("tools", []):
            if t["id"] == tid:
                t.update(kw)
                break


def _title(tid: str) -> str:
    t = toolspec._spec(tid)
    if t:
        return t["title"]
    return "Technical debt" if tid == "tech-debt" else tid


def _status_from(tid: str, res: dict) -> tuple[str, str]:
    """Map a toolspec.run() result to (status, one-line summary)."""
    if not res.get("ok"):
        if res.get("requires"):
            return "skip", res["requires"]
        return "error", (res.get("error") or "failed")[:200]
    if res.get("kind") == "findings":
        finds = res.get("findings") or []
        if res.get("clean") or not finds:
            return "pass", "clean"
        sev = [str(f.get("severity") or "").lower() for f in finds if isinstance(f, dict)]
        hi = sum(1 for s in sev if s in ("critical", "high", "real", "blocker"))
        if tid in ("secrets", "dependencies", "api-breaks") and hi:
            return "block", f"{hi} high/critical of {len(finds)}"
        return "warn", f"{len(finds)} finding(s)"
    txt = (res.get("text") or "").strip()
    return "done", (txt.splitlines()[0][:120] if txt else "done")


def start(pid: str) -> dict:
    proj = projects.get_project(pid)
    if not proj:
        return {"ok": False, "error": "unknown project"}
    if is_running(pid):
        return {"ok": True, "already": True}

    tools = []
    for tid in _CHECK_ORDER:
        tools.append({"id": tid, "title": _title(tid), "phase": "checks",
                      "status": "pending", "summary": ""})
    for tid in _DOC_ORDER:
        tools.append({"id": tid, "title": _title(tid), "phase": "report",
                      "status": "pending", "summary": ""})
    for tid in _SKIP_IDS:
        spec = toolspec._spec(tid) or {}
        tools.append({"id": tid, "title": _title(tid), "phase": "governance",
                      "status": "skip", "summary": spec.get("requires", "needs external input")})

    with _lock:
        _runs[pid] = {"status": "running", "phase": "checks", "started": _now(),
                      "finished": None, "verdict": None, "tools": tools,
                      "summary_md": "", "report_aid": None, "tokens": 0}
    threading.Thread(target=_run, args=(pid, proj), daemon=True).start()
    return {"ok": True}


_SYNTH_SYS = (
    "You are OnePort's audit synthesizer. You are given the raw results of every "
    "check tool run on a repository. Write a tight executive summary in Markdown: "
    "one line on overall risk, then the top issues ranked by severity (name the tool "
    "and file:line where known), then what to fix first. Ground everything ONLY in "
    "the results provided — invent nothing. Keep it under 250 words."
)


def _synthesize(results: dict, model, key) -> str:
    lines = []
    for r in results.values():
        lines.append(f"### {r['title']} [{r['status']}] — {r['summary']}")
        for f in ((r.get("data") or {}).get("findings") or [])[:6]:
            if isinstance(f, dict):
                loc = (f.get("file") or "") + (f":{f.get('line')}" if f.get("line") else "")
                ttl = f.get("title") or f.get("message") or ""
                lines.append(f"- {ttl} {('(' + loc + ')') if loc else ''}".rstrip())
    user = "TOOL RESULTS:\n" + "\n".join(lines)[:14000]
    try:
        raw, _tok = repomap._llm(_SYNTH_SYS, user, model, key, max_tokens=900, json_mode=False)
        return (raw or "").strip()
    except Exception:
        return ""


def _run(pid: str, proj: dict) -> None:
    path, base = proj["path"], proj.get("base", "main")
    model, key = settings.ai_opts()
    bal0 = _bal()
    results: dict[str, dict] = {}

    # ── Phase 1: checks ──
    _set(pid, phase="checks")
    for tid in _CHECK_ORDER:
        _set_tool(pid, tid, status="running")
        try:
            if tid == "tech-debt":
                d = debt.scan(path)
                items = d.get("items") or d.get("findings") or d.get("hotspots") or []
                status = "warn" if items else "pass"
                summary = f"{len(items)} debt item(s)" if items else "clean"
                data = {"findings": items[:15]}
            else:
                res = toolspec.run(tid, path, base)
                status, summary = _status_from(tid, res)
                if res.get("kind") == "findings":
                    data = {"findings": (res.get("findings") or [])[:15]}
                else:
                    data = {"text": (res.get("text") or "")[:2000]}
        except Exception as e:  # noqa: BLE001
            status, summary, data = "error", str(e)[:200], None
        _set_tool(pid, tid, status=status, summary=summary)
        results[tid] = {"gate": tid, "title": _title(tid), "status": status,
                        "summary": summary, "data": data}

    # ── Phase 2: AI synthesis over all check outputs ──
    _set(pid, phase="synthesis")
    summary_md = _synthesize(results, model, key)
    _set(pid, summary_md=summary_md)

    statuses = [r["status"] for r in results.values()]
    verdict = ("BLOCKED" if "block" in statuses
               else "INCONCLUSIVE" if "error" in statuses else "READY")

    # record to history so report.generate + the verdict reflect this audit
    entry = {"mode": "audit", "trigger": "full-audit", "verdict": verdict, "changed": [],
             "summary": {"pass": statuses.count("pass"), "block": statuses.count("block"),
                         "error": statuses.count("error"), "warn": statuses.count("warn"),
                         "skip": statuses.count("skip")},
             "gates": list(results.values()), "synthesis": summary_md}
    projects.add_history(pid, entry, tokens=0)
    _set(pid, verdict=verdict)

    # ── Phase 3: slow doc generators, then the final report artifact ──
    _set(pid, phase="report")
    for tid in _DOC_ORDER:
        _set_tool(pid, tid, status="running")
        try:
            res = toolspec.run(tid, path, base)          # context / docgen (up to 900s each)
            status, summary = _status_from(tid, res)
        except Exception as e:  # noqa: BLE001
            status, summary = "error", str(e)[:200]
        _set_tool(pid, tid, status=status, summary=summary)

    report_aid = None
    try:
        proj2 = projects.get_project(pid) or proj
        html_doc = report.generate(proj2, repomap.load_manifest(pid))
        art = artifacts.add_artifact(pid, "report", f"Full audit · {verdict}", html_doc, "html")
        report_aid = art.get("id") or art.get("aid")
    except Exception:
        pass

    tokens = 0
    bal1 = _bal()
    if bal0 is not None and bal1 is not None:
        tokens = max(0, bal0 - bal1)
    if tokens:
        projects.add_tokens(pid, tokens)

    _set(pid, status="done", phase="done", verdict=verdict, finished=_now(),
         report_aid=report_aid, tokens=tokens)