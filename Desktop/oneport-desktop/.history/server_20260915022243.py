"""
OnePort Desktop — local engine server.

Serves the cockpit UI and runs the REAL OnePort gates against a real repo.
Pure Python stdlib: no web framework, no dependencies — so the eventual desktop
bundle carries almost nothing. It reuses oneport_mcp.gates (the same engine the
MCP server drives), so nothing is reimplemented.

  GET /                     the cockpit UI
  GET /api/status           which gates are installed + login state
  GET /api/gate?id=&path=   run ONE gate (so the UI can light them up live)

Binds to 127.0.0.1 only — this is a local tool; nothing is exposed off-machine.
"""
from __future__ import annotations

import json
import os
import audit
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import projects
import repomap
import chat
import artifacts
import report
import fixes
import terminal
import settings
import autofix
import diagnose
import debt
import toolspec
import activity
import rulebook
import guard
import telemetry
import verify
import changeintel
from oneport_mcp.gates import GATES, installed, logged_in, run_gate

# Account/login is optional — if oneport_account isn't importable the app still
# runs the deterministic gates; login just reports unavailable rather than crash.
try:
    from oneport_account import fetch_balance, validate_token, AccountError
    from oneport_account.credentials import Credentials, clear, load, save
    try:
        from oneport_account.config import DEFAULT_BUY_URL as _BUY_URL
    except Exception:
        _BUY_URL = "https://version-4-production.d2tx07mbxfs880.amplifyapp.com/pricing"
    _ACCOUNT = True
except Exception:  # pragma: no cover
    _ACCOUNT = False
    _BUY_URL = "https://version-4-production.d2tx07mbxfs880.amplifyapp.com/pricing"


def _account_payload() -> dict:
    """Current login state + live user data (email/tier/balance) when available."""
    payload = {"logged_in": False, "buy_url": _BUY_URL}
    if not _ACCOUNT:
        activity.set_current_user(None)
        return payload
    payload["logged_in"] = logged_in()
    if not payload["logged_in"]:
        activity.set_current_user(None)
        return payload
    creds = load()
    payload["email"] = getattr(creds, "email", "") if creds else ""
    activity.set_current_user(payload.get("email"))
    telemetry.set_user(payload.get("email"))   # DAU/WAU keyed per user
    # Enrich with live Supabase-backed data; tolerate offline / revoked token.
    try:
        info = fetch_balance()
        payload.update(email=info.get("email", payload.get("email", "")),
                       tier=info.get("tier", "free"), balance=info.get("balance"))
    except Exception:
        pass
    return payload

# When frozen by PyInstaller, bundled data lives under sys._MEIPASS; otherwise
# it sits next to this file. Either way the UI is at <root>/ui/index.html.
if getattr(sys, "frozen", False):
    HERE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    HERE = Path(__file__).resolve().parent
UI = HERE / "ui" / "index.html"


def _internet_up() -> bool:
    """Real connectivity check (navigator.onLine lies on LAN-only). Tries a fast
    TCP connect to a couple of public DNS resolvers."""
    import socket
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53), ("208.67.222.222", 53)):
        try:
            socket.create_connection((host, port), timeout=2).close()
            return True
        except OSError:
            continue
    return False


def _balance() -> int | None:
    if not _ACCOUNT:
        return None
    try:
        return int(fetch_balance().get("balance"))
    except Exception:
        return None


def _trim_data(data) -> dict | None:
    """Keep just enough of a gate's JSON for the detail panel — the findings
    list (capped) + a summary — without bloating the stored history."""
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("findings", "issues", "gaps", "breaking_changes", "changes"):
        if isinstance(data.get(key), list):
            out["findings"] = data[key][:15]
            break
    if isinstance(data.get("summary"), dict):
        out["summary"] = data["summary"]
    for key in ("verdict", "counts", "reason"):
        if key in data:
            out[key] = data[key]
    return out or None


def _calibrate(r: dict) -> dict:
    """Make the verdict reflect REAL risk, not raw hit count — the difference
    between a tool people trust and one they mute.

    Secrets block ONLY on confirmed-real leaks; unreviewed placeholders/examples
    (AWS's own AKIA…EXAMPLE, test fixtures) become a non-blocking REVIEW, not a
    BLOCK. Dependency CVEs block ONLY on high/critical; low/medium become REVIEW.
    Everything is graded from the tool's own per-finding verdict/severity."""
    if r.get("status") not in ("block", "pass"):
        return r
    gid = r.get("gate")
    finds = (r.get("data") or {}).get("findings") or []
    if gid == "secrets":
        real = [f for f in finds if str(f.get("verdict")).lower() == "real"]
        review = [f for f in finds if str(f.get("verdict")).lower() in ("unreviewed", "suspected")]
        if real:
            r["status"], r["summary"] = "block", f"{len(real)} confirmed secret(s)"
        elif review:
            r["status"] = "warn"
            r["summary"] = (f"{len(review)} to review — run AI triage to sort real vs "
                            "example, or check them by hand")
        else:
            r["status"] = "pass"
            r["summary"] = "clean (examples/placeholders filtered)" if finds else "clean"
    elif gid == "dependencies":
        sev = lambda f: str(f.get("severity") or "").lower()
        crit = [f for f in finds if sev(f) in ("critical", "high")]
        if crit:
            extra = len(finds) - len(crit)
            r["status"] = "block"
            r["summary"] = f"{len(crit)} high/critical CVE(s)" + (f" (+{extra} lower)" if extra > 0 else "")
        elif finds:
            r["status"] = "warn"
            r["summary"] = f"{len(finds)} low/medium CVE(s) — worth reviewing, not blocking"
        else:
            r["status"], r["summary"] = "pass", "no known CVEs"
    return r


def scan_project(pid: str, mode: str = "quick", trigger: str = "manual",
                 changed=None) -> dict:
    """Run the gates for a project, log the result to its history, meter tokens.

    mode 'production' includes the AI gates and measures tokens by the balance
    delta; 'quick'/'auto' run only the free deterministic gates (0 tokens)."""
    proj = projects.get_project(pid)
    if not proj:
        return {"error": "unknown project"}
    path, base = proj["path"], proj.get("base", "main")
    include_metered = mode == "production"

    term = terminal.get_terminal()
    term.emit(f"$ oneport {'ship --production' if include_metered else 'scan'}  "
              f"# {proj['name']} ({trigger})", "cmd")

    bal_before = _balance() if include_metered else None
    results = []
    for gate in GATES:
        if gate.metered and not include_metered:
            results.append({"gate": gate.id, "title": gate.title, "status": "skip",
                            "summary": "AI gate — runs on Test for Production"})
            continue
        gargs = " ".join(a.replace("{path}", ".").replace("{base}", base) for a in gate.args)
        term.emit(f"  $ {gate.exe} {gargs}", "cmd")
        r = _calibrate(run_gate(gate, path=path, base=base))
        term.emit(f"  → {r['status'].upper()}: {r['summary']}",
                  "err" if r["status"] in ("block", "error") else "out")
        results.append(r)

    blocked = [r for r in results if r["status"] == "block"]
    errored = [r for r in results if r["status"] == "error"]
    warned = [r for r in results if r["status"] == "warn"]
    # warn never blocks — the verdict stays honest: BLOCKED only on real risk
    verdict = "BLOCKED" if blocked else ("INCONCLUSIVE" if errored else "READY")

    tokens = 0
    if include_metered and bal_before is not None:
        after = _balance()
        if after is not None:
            tokens = max(0, bal_before - after)

    entry = {
        "mode": mode, "trigger": trigger, "verdict": verdict,
        "changed": list(changed or []),
        "summary": {
            "pass": sum(1 for r in results if r["status"] == "pass"),
            "block": len(blocked), "error": len(errored), "warn": len(warned),
            "skip": sum(1 for r in results if r["status"] == "skip"),
        },
        "gates": [{"gate": r["gate"], "title": r["title"],
                   "status": r["status"], "summary": r["summary"],
                   "data": _trim_data(r.get("data"))} for r in results],
        "tokens": tokens,
    }
    projects.add_history(pid, entry, tokens=tokens)
    activity.record("scan", f"{mode}/{trigger} -> {verdict}")
    telemetry.capture("scan_run", {"mode": mode, "trigger": trigger, "verdict": verdict})
    return {"project": projects.get_project(pid), "entry": entry}


# ── chat agent: interpret intent, take REAL actions, stream progress ─────────
def _latest_gates(proj: dict) -> list:
    latest = (proj.get("history") or [None])[0] or {}
    return latest.get("gates", [])


def _run_chat_agent(pid: str) -> None:
    """Worker: plan the user's message into real actions, run them, narrate."""
    proj = projects.get_project(pid)
    if not proj:
        return
    root = proj["path"]
    base = proj.get("base") or "main"
    message = chat._RUNS.get(pid, {}).get("message", "")
    model, key = settings.ai_opts()
    tokens = 0
    results: list[str] = []
    answer_text = ""
    answer_error = ""

    chat.step(pid, "Understanding your request")
    plan = chat.plan(message, model, key)
    chat.endstep(pid, plan.get("note") or "")
    actions = plan.get("actions", [{"do": "answer"}])

    for a in actions:
        do = a.get("do")
        try:
            if do == "build_context":
                chat.step(pid, "Building the repo context map")
                res = repomap.build(pid, root, model=model, gemini_key=key)
                n = res.get("file_count") or res.get("files") or 0
                tokens += int(res.get("tokens") or 0)
                if n:
                    repomap.generate_readme(pid, model=model, gemini_key=key)
                msg = f"mapped {n} files, README updated" if n else "nothing to map"
                chat.endstep(pid, msg); results.append("Context: " + msg)

            elif do == "scan":
                mode = "production" if a.get("mode") == "production" else "quick"
                chat.step(pid, f"Running {'full production' if mode=='production' else 'quick'} scan")
                out = scan_project(pid, mode, "chat")
                e = out.get("entry", {}); s = e.get("summary", {})
                v = e.get("verdict", "?")
                msg = f"{v} — {s.get('block',0)} blocking, {s.get('error',0)} error, {s.get('pass',0)} passed"
                chat.endstep(pid, msg, "error" if v != "READY" else "done")
                results.append("Scan: " + msg)
                proj = projects.get_project(pid)

            elif do == "run_tool":
                tid = a.get("tool", "")
                spec = next((t for t in toolspec.TOOLS if t["id"] == tid), None)
                if not spec:
                    continue
                chat.step(pid, f"Running {spec['title']}")
                if spec.get("bg"):
                    toolspec.run_background(tid, root, base, terminal.get_terminal())
                    chat.endstep(pid, "started — see the Terminal tab")
                    results.append(f"{spec['title']}: running in the terminal")
                else:
                    r = toolspec.run(tid, root, base)
                    if r.get("requires"):
                        chat.endstep(pid, r["requires"], "error")
                        results.append(f"{spec['title']}: {r['requires']}")
                    elif not r.get("ok"):
                        chat.endstep(pid, r.get("error", "failed"), "error")
                        results.append(f"{spec['title']}: {r.get('error','failed')}")
                    elif r.get("kind") == "findings":
                        f = r.get("findings") or []
                        msg = "clean" if r.get("clean") else f"{len(f)} finding(s)"
                        chat.endstep(pid, msg, "done" if r.get("clean") else "error")
                        results.append(f"{spec['title']}: {msg}")
                    else:
                        chat.endstep(pid, "done")
                        results.append(f"{spec['title']}: done")

            elif do == "debt":
                chat.step(pid, "Scanning technical debt")
                d = debt.scan(root, _latest_gates(proj))
                c = d.get("counts", {})
                msg = (f"{d.get('total',0)} items — {c.get('markers',0)} markers, "
                       f"{c.get('long_functions',0)} long fns, {c.get('oversized',0)} big files")
                chat.endstep(pid, msg); results.append("Tech debt: " + msg)

            elif do == "fix":
                gid = a.get("gate", "")
                gentry = next((g for g in _latest_gates(proj) if g.get("gate") == gid), None)
                chat.step(pid, f"Auto-fixing “{gid}” with AI")
                if not gentry:
                    chat.endstep(pid, "no scan result for that gate — scan first", "error")
                    results.append(f"Fix {gid}: scan first")
                else:
                    term = terminal.get_terminal(); term.set_cwd(root)
                    fr = autofix.run_fix(proj, gentry, repomap.load_manifest(pid), term,
                                         rescan=lambda: scan_project(pid, "quick", "autofix"))
                    ok = fr.get("ok")
                    chat.endstep(pid, "started — watch the Terminal" if ok else fr.get("error", "unavailable"),
                                 "done" if ok else "error")
                    results.append(f"Fix {gid}: " + ("running in terminal" if ok else fr.get("error", "unavailable")))

            elif do == "guide":
                chat.step(pid, "Checking the rulebook")
                hits = []
                for g in _latest_gates(proj):
                    if g.get("status") in ("skip", "error", "block"):
                        rule = rulebook.match(g.get("summary", ""))
                        if rule and rule not in hits:
                            hits.append(rule)
                rule = rulebook.match(message)
                if rule and rule not in hits:
                    hits.append(rule)
                if hits:
                    chat.endstep(pid, f"{len(hits)} matching rule(s)")
                    for h in hits:
                        fixes = "\n".join(f"  • {s}" for s in h.get("fix", []))
                        results.append(f"**{h.get('title','Guidance')}** — {h.get('why','')}\n{fixes}")
                else:
                    chat.endstep(pid, "no rule matched — answering directly")
                    actions.append({"do": "answer"})  # fall through to a grounded answer

            elif do == "answer":
                # Product/onboarding + safety/status questions are answered
                # deterministically — no AI, no tokens, never fails. Only open-ended
                # repo questions hit the LLM.
                if chat.is_meta_question(message):
                    chat.step(pid, "Getting you oriented")
                    answer_text = chat.meta_answer(message)
                    chat.endstep(pid, "answered — no tokens used")
                elif chat.is_status_question(message):
                    chat.step(pid, "Reading your latest scan")
                    answer_text = chat.status_answer(proj)
                    chat.endstep(pid, "answered from your scan — no tokens used")
                else:
                    chat.step(pid, "Answering from the repo context")
                    res = chat.ask(pid, root, message,
                                   gate_entry=(proj.get("history") or [None])[0],
                                   model=model, gemini_key=key)
                    if res.get("error"):
                        answer_error = res["error"]
                        chat.endstep(pid, res["error"], "error")
                    else:
                        tokens += int(res.get("tokens") or 0)
                        answer_text = res.get("answer", "")
                        chat.endstep(pid, "done")
        except Exception as exc:
            chat.endstep(pid, f"error: {exc}", "error")
            results.append(f"{do}: error — {exc}")

    # Compose the final reply: a direct answer wins; else a tidy summary.
    if answer_text:
        reply = answer_text
        if results:
            reply += "\n\n" + "\n".join("- " + r for r in results if not r.startswith("**"))
        for r in results:
            if r.startswith("**"):
                reply += "\n\n" + r
    elif results:
        reply = "Here's what I did:\n\n" + "\n".join(
            ("- " + r) if not r.startswith("**") else ("\n" + r) for r in results)
    elif answer_error:
        # The AI answer failed (tokens/rate-limit) — say so honestly + actionably,
        # never the misleading "couldn't determine an action".
        reply = chat.friendly_error(answer_error)
    else:
        reply = "I couldn't determine an action for that — try asking a question, or "\
                "\"build the context and scan again\"."

    if tokens:
        projects.add_tokens(pid, tokens)
    activity.record("chat", message[:120])
    steps = chat.progress(pid).get("steps", [])
    chat.add_message(pid, "assistant", reply, tokens, steps=steps)
    chat.finish_run(pid, reply, tokens)


def _on_change(pid: str, root: str, names: list) -> None:
    """Watch callback. Surfaces the change INSTANTLY (files + time) so the UI
    updates live, THEN runs the gates, computes advanced change intelligence
    (what changed, repercussions, whether it introduced errors), and refreshes
    the map."""
    # Snapshot the gates BEFORE this change's scan, so we can name what changed.
    before_proj = projects.get_project(pid) or {}
    before_gates = ((before_proj.get("history") or [None])[0] or {}).get("gates", [])

    projects.record_change(pid, names)          # instant — no waiting on the scan
    out = scan_project(pid, "quick", "auto", names)
    entry = out.get("entry") or {}
    verdict = entry.get("verdict", "")
    try:
        projects.mark_change_scanned(pid, verdict)
    except Exception:
        pass

    # Advanced change intelligence: deterministic core (gate delta + diff stats +
    # risky-area detection) with a best-effort AI narrative on top.
    model, key = settings.ai_opts()
    try:
        intel = changeintel.build(
            pid, root, changed=names,
            before_gates=before_gates, after_gates=entry.get("gates", []),
            verdict=verdict, manifest_files=repomap.load_manifest(pid).get("files", {}),
            model=model, gemini_key=key, want_ai=True)
        projects.set_change_intel(pid, intel)
    except Exception:
        pass

    try:
        res = repomap.build(pid, root, changed=names, model=model, gemini_key=key)
        if res.get("updated"):
            repomap.generate_readme(pid, model=model, gemini_key=key)
    except Exception:
        pass

    # ── live alerts: notify the user only when something is wrong ──
    try:
        _emit_change_alerts(pid, root, entry, names, model, key)
    except Exception:
        pass


def _gate_loc(g: dict) -> str:
    """Best-effort file:line for a gate finding, from its trimmed data."""
    d = g.get("data")
    if not isinstance(d, dict):
        return ""
    items = d.get("findings") or d.get("items") or d.get("issues") or []
    if isinstance(items, list) and items and isinstance(items[0], dict):
        it = items[0]
        p = it.get("path") or it.get("file") or ""
        ln = it.get("line") or it.get("lineno")
        return f"{p}:{ln}" if p and ln else (p or "")
    p = d.get("path") or d.get("file") or ""
    ln = d.get("line")
    return f"{p}:{ln}" if p and ln else (p or "")


def _emit_change_alerts(pid, root, entry, names, model, key) -> None:
    """After a watched change: raise a gate-blocker alert and/or an intent-drift
    alert. Silent on a clean, on-track change (no news = no notification)."""
    gates = entry.get("gates", [])
    blocked = [g for g in gates if g.get("status") == "block"]
    errored = [g for g in gates if g.get("status") == "error"]

    if blocked:
        lines, files = [], []
        for g in blocked[:4]:
            loc = _gate_loc(g)
            lines.append(f"- [{g.get('gate')}] {g.get('summary','')}" + (f"  ({loc})" if loc else ""))
            if loc:
                f = loc.split(":")[0]
                if f not in files:
                    files.append(f)
        prompt = ("OnePort blocked my last change — fix these before shipping:\n"
                  + "\n".join(lines)
                  + "\n\nOpen: " + (", ".join(files) or "(see the flagged gate)"))
        projects.add_alert(pid, {
            "kind": "gate", "level": "block",
            "title": f"{len(blocked)} blocker(s) after your change",
            "detail": "; ".join(g.get("title", "") for g in blocked[:3]),
            "prompt": prompt, "files": files})
    elif errored:
        gnames = ", ".join(g.get("gate", "") for g in errored[:3])
        projects.add_alert(pid, {
            "kind": "gate", "level": "error",
            "title": "A check couldn't run (INCONCLUSIVE)",
            "detail": "; ".join(g.get("title", "") for g in errored[:3]),
            "prompt": ("OnePort couldn't complete a check (" + gnames + "). That's usually a "
                       "git/tooling issue — the base branch or commit history. Help me make the "
                       "check run cleanly on this repo."),
            "files": []})

    # intent drift (only if the user has run Verify before → an intent is saved)
    try:
        alert = verify.auto_verify(pid, root, model, key)
        if alert:
            if alert.get("tokens"):
                projects.add_tokens(pid, alert["tokens"])
            projects.add_alert(pid, {k: v for k, v in alert.items() if k != "tokens"})
    except Exception:
        pass

def _build_initial_context(pid: str, root: str) -> None:
    """Right after a repo is attached: build the full codebase mind-map
    (repomap → manifest.json) plus an overview, so the app understands the code
    BEFORE any edits land. Runs on a background thread; flips the project's
    context flags when done. Best-effort — never crashes the attach."""
    projects.set_context_state(pid, building=True, ready=False)
    try:
        model, key = settings.ai_opts()
        res = repomap.build(pid, root, model=model, gemini_key=key)   # summarizes every file → manifest.json
        if isinstance(res, dict) and res.get("tokens"):
            projects.add_tokens(pid, res["tokens"])
        try:
            rr = repomap.generate_readme(pid, model=model, gemini_key=key)
            if isinstance(rr, dict) and rr.get("tokens"):
                projects.add_tokens(pid, rr["tokens"])
        except Exception:
            pass
    except Exception:
        pass
    finally:
        projects.set_context_state(pid, building=False, ready=True)

def _start_watch(pid: str) -> bool:
    proj = projects.get_project(pid)
    if not proj:
        return False
    root = proj["path"]
    projects.start_watch(pid, root, lambda names: _on_change(pid, root, names))
    return True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)


        if u.path == "/api/projects/audit/progress":
            self._send(200, audit.progress((q.get("id", [""])[0]).strip()))
            return

        if u.path in ("/", "/index.html"):
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            return

        if u.path == "/api/online":
            self._send(200, {"online": _internet_up()})
            return

        if u.path == "/api/account":
            self._send(200, _account_payload())
            return

        if u.path == "/api/settings":
            self._send(200, settings.public())
            return

        if u.path == "/api/tools":
            self._send(200, {"tools": toolspec.catalog(),
                             "categories": toolspec.CAT_ORDER})
            return

        if u.path == "/api/activity/heatmap":
            self._send(200, activity.heatmap(105))
            return

        if u.path == "/api/activity/recent":
            self._send(200, {"events": activity.recent(50)})
            return

        if u.path == "/api/rulebook":
            self._send(200, rulebook.all_rules())
            return

        if u.path == "/api/projects":
            self._send(200, {"projects": projects.list_projects()})
            return

        if u.path == "/api/projects/get":
            proj = projects.get_project((q.get("id", [""])[0]).strip())
            if not proj:
                self._send(404, {"error": "unknown project"})
                return
            proj = {**proj, "watching": projects.is_watching(proj["id"])}
            self._send(200, proj)
            return

        if u.path == "/api/projects/activity":
            pid = (q.get("id", [""])[0]).strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            self._send(200, activity.build(proj))
            return

        if u.path == "/api/guard/status":
            proj = projects.get_project((q.get("id", [""])[0]).strip())
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            self._send(200, guard.status(proj["path"]))
            return

        if u.path == "/api/projects/map":
            self._send(200, repomap.mindmap((q.get("id", [""])[0]).strip()))
            return

        if u.path == "/api/verify/last":
            self._send(200, verify.load_last((q.get("id", [""])[0]).strip()))
            return

        if u.path == "/api/projects/chat":
            self._send(200, {"messages": chat.load_chat((q.get("id", [""])[0]).strip())})
            return

        if u.path == "/api/projects/chat/progress":
            self._send(200, chat.progress((q.get("id", [""])[0]).strip()))
            return

        if u.path == "/api/terminal/output":
            try:
                since = int((q.get("since", ["0"])[0]))
            except ValueError:
                since = 0
            self._send(200, terminal.get_terminal().read_since(since))
            return

        if u.path == "/api/projects/artifacts":
            self._send(200, {"artifacts": artifacts.list_artifacts((q.get("id", [""])[0]).strip())})
            return

        if u.path == "/api/projects/artifact":
            pid = (q.get("id", [""])[0]).strip()
            aid = (q.get("aid", [""])[0]).strip()
            got = artifacts.get_content(pid, aid)
            if not got:
                self._send(404, {"error": "artifact not found"})
                return
            content, ext = got
            ctype = {"html": "text/html", "md": "text/markdown",
                     "json": "application/json"}.get(ext, "text/plain")
            self._send(200, content.encode("utf-8"), f"{ctype}; charset=utf-8")
            return

        if u.path == "/api/status":
            self._send(200, {
                "logged_in": logged_in(),
                "gates": [
                    {"id": g.id, "title": g.title, "sub": _sub(g),
                     "installed": installed(g), "metered": g.metered}
                    for g in GATES
                ],
            })
            return

        if u.path == "/api/gate":
            gid = (q.get("id", [""])[0]).strip()
            path = (q.get("path", ["."])[0]).strip() or "."
            base = (q.get("base", ["main"])[0]).strip() or "main"
            gate = next((g for g in GATES if g.id == gid), None)
            if gate is None:
                self._send(404, {"error": f"unknown gate '{gid}'"})
                return
            if not Path(path).exists():
                self._send(200, {"gate": gid, "title": gate.title, "status": "error",
                                 "summary": f"path not found: {path}", "data": None})
                return
            self._send(200, run_gate(gate, path=path, base=base))
            return

        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        body = self._read_json()

        if u.path == "/api/login":
            if not _ACCOUNT:
                self._send(200, {"ok": False, "error": "Login is unavailable in this build."})
                return
            token = (body.get("token") or "").strip()
            if not token:
                self._send(200, {"ok": False, "error": "Enter your access token."})
                return
            try:
                info = validate_token(token)              # checks against the backend/Supabase
                save(Credentials(token=token, email=info.get("email", ""), api_url=""))
            except AccountError as exc:
                self._send(200, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                self._send(200, {"ok": False, "error": f"Could not sign in: {exc}"})
                return
            self._send(200, {"ok": True, "logged_in": True,
                             "email": info.get("email", ""), "tier": info.get("tier", "free"),
                             "balance": info.get("balance"), "buy_url": _BUY_URL})
            return

        if u.path == "/api/projects/audit/progress":
            self._send(200, audit.progress((q.get("id", [""])[0]).strip()))
            return
        
        if u.path == "/api/logout":
            if _ACCOUNT:
                try:
                    clear()
                except Exception:
                    pass
            self._send(200, {"ok": True})
            return

        if u.path == "/api/settings":
            # No BYOK: any gemini_key/anthropic_key in the patch is ignored
            # (settings.save only persists known DEFAULTS keys). AI always runs
            # on the managed proxy.
            self._send(200, settings.save(dict(body or {})) and settings.public())
            return

        if u.path == "/api/projects/create":
            path = (body.get("path") or "").strip()
            base = (body.get("base") or "main").strip() or "main"
            if not path or not Path(path).is_dir():
                self._send(200, {"ok": False, "error": f"Not a folder: {path or '(empty)'}"})
                return
            # Size guard — reject repos over the guideline unless the user forces it.
            if not body.get("force"):
                over, size_mb = projects.measure_size_mb(path)
                if over:
                    self._send(200, {
                        "ok": False, "too_big": True, "size_mb": size_mb,
                        "error": (f"This repo is over the {projects.REPO_SIZE_LIMIT_MB} MB guideline "
                                  f"(~{size_mb}+ MB of source). Large repos are slow and costly to map.")})
                    return
            proj = projects.create_project(path, base)
            # Build the full codebase context (mind-map → manifest.json) in the
            # background so OnePort "understands" the repo before any edits.
            if not proj.get("context_ready"):
                projects.set_context_state(proj["id"], building=True)
                threading.Thread(
                    target=_build_initial_context,
                    args=(proj["id"], proj["path"]),
                    daemon=True,
                ).start()
                proj = projects.get_project(proj["id"]) or proj
            self._send(200, {"ok": True, "project": proj})
            return

        if u.path == "/api/projects/import":
            url = (body.get("url") or "").strip()
            term = terminal.get_terminal()
            term.emit(f"→ Cloning {url} …", "cmd")
            res = projects.clone_repo(url)
            if not res.get("ok"):
                term.emit(res.get("error", "clone failed"), "err")
                self._send(200, res)
                return
            term.emit("done" if not res.get("existed") else "already cloned — attaching",
                      "out")
            proj = projects.create_project(res["path"])
            activity.record("scan", f"imported {url}")
            self._send(200, {"ok": True, "project": proj, "imported": True})
            return

        if u.path == "/api/projects/scan":
            pid = (body.get("id") or "").strip()
            mode = (body.get("mode") or "quick").strip()
            self._send(200, scan_project(pid, mode=mode, trigger="manual"))
            return

        if u.path == "/api/projects/watch":
            pid = (body.get("id") or "").strip()
            on = bool(body.get("on"))
            ok = _start_watch(pid) if on else (projects.stop_watch(pid) or True)
            self._send(200, {"ok": bool(ok), "watching": projects.is_watching(pid)})
            return

        if u.path == "/api/projects/map/build":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            model, key = settings.ai_opts()
            res = repomap.build(pid, proj["path"], model=model, gemini_key=key)
            if res.get("file_count"):
                repomap.generate_readme(pid, model=model, gemini_key=key)
            self._send(200, res)
            return

        if u.path == "/api/projects/map/readme":
            pid = (body.get("id") or "").strip()
            model, key = settings.ai_opts()
            self._send(200, repomap.generate_readme(pid, model=model, gemini_key=key))
            return

        if u.path == "/api/projects/chat":
            pid = (body.get("id") or "").strip()
            msg = (body.get("message") or "").strip()
            proj = projects.get_project(pid)
            if not proj or not msg:
                self._send(200, {"ok": False, "error": "unknown project or empty message"})
                return
            if chat.is_active(pid):
                self._send(200, {"ok": False, "busy": True,
                                 "error": "still working on your last request"})
                return
            # The chat is an AGENT: start a worker that plans + runs REAL actions
            # and streams progress; the UI polls /chat/progress.
            chat.start_run(pid, msg)
            chat.add_message(pid, "user", msg)
            threading.Thread(target=_run_chat_agent, args=(pid,), daemon=True).start()
            self._send(200, {"ok": True, "started": True})
            return

        if u.path == "/api/projects/chat/clear":
            chat.clear_chat((body.get("id") or "").strip())
            self._send(200, {"ok": True})
            return

        if u.path == "/api/terminal/exec":
            cmd = (body.get("command") or "")
            terminal.get_terminal().send(cmd)
            activity.record("terminal", cmd[:120])
            self._send(200, {"ok": True})
            return

        if u.path == "/api/guide":
            text = (body.get("text") or "").strip()
            what = (body.get("what") or "").strip()
            rule = rulebook.match(text) or rulebook.match(what)
            if rule:
                self._send(200, {"source": rule.get("source", "builtin"), "rule": rule})
                return
            # Unknown error → ask the AI, then LEARN it so it's instant next time.
            model, key = settings.ai_opts()
            res = diagnose.explain(what or "A OnePort check reported an issue.",
                                   text or "(no detail)", model=model, gemini_key=key)
            guidance = {"title": "AI guidance", "why": "",
                        "fix": [(res.get("guidance") or "").strip()]}
            if res.get("guidance") and not res.get("error"):
                rulebook.learn(text or what, guidance)
            self._send(200, {"source": "ai", "rule": guidance,
                             "error": res.get("error")})
            return

        if u.path == "/api/projects/diagnose":
            pid = (body.get("id") or "").strip()
            gate_id = (body.get("gate") or "").strip()
            proj = projects.get_project(pid)
            what = (body.get("what") or "").strip()
            error_text = (body.get("error") or "").strip()
            if proj and gate_id and not error_text:
                latest = (proj.get("history") or [None])[0]
                g = next((x for x in (latest or {}).get("gates", [])
                          if x.get("gate") == gate_id), None)
                if g:
                    what = what or f"The '{g.get('title')}' security gate returned {g.get('status')}."
                    import json as _json
                    error_text = f"{g.get('summary')}\n{_json.dumps(g.get('data') or {})[:1500]}"
            model, key = settings.ai_opts()
            res = diagnose.explain(what or "A OnePort check failed.",
                                   error_text or "(no detail)", model=model, gemini_key=key)
            if res.get("tokens") and proj:
                projects.add_tokens(pid, res["tokens"])
            self._send(200, res)
            return

        if u.path == "/api/verify/draft":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            model, key = settings.ai_opts()
            res = verify.draft_intent(pid, model=model, gemini_key=key)
            if res.get("tokens"):
                projects.add_tokens(pid, res["tokens"])
            self._send(200, res)
            return

        if u.path == "/api/alerts/clear":
            projects.clear_alerts((body.get("id") or "").strip())
            self._send(200, {"ok": True})
            return

        if u.path == "/api/verify/run":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            model, key = settings.ai_opts()
            res = verify.run(pid, proj["path"], (body.get("intent") or ""),
                             model=model, gemini_key=key)
            if res.get("tokens"):
                projects.add_tokens(pid, res["tokens"])
            if res.get("ok"):
                activity.record("scan", "verify run")
                telemetry.capture("verify_run", {"verdict": (res.get("report") or {}).get("verdict")})
            self._send(200, res)
            return

        if u.path == "/api/projects/autofix":
            pid = (body.get("id") or "").strip()
            gate_id = (body.get("gate") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            latest = (proj.get("history") or [None])[0]
            gate_entry = next((g for g in (latest or {}).get("gates", [])
                               if g.get("gate") == gate_id), None)
            if not gate_entry:
                self._send(200, {"ok": False, "error": "no scan result for that gate — scan first"})
                return
            term = terminal.get_terminal()
            term.set_cwd(proj["path"])
            res = autofix.run_fix(
                proj, gate_entry, repomap.load_manifest(pid), term,
                rescan=lambda: scan_project(pid, "quick", "autofix"))
            activity.record("fix", f"gate={gate_id}")
            self._send(200, res)
            return

        if u.path == "/api/terminal/cwd":
            terminal.get_terminal().set_cwd((body.get("path") or "").strip())
            self._send(200, {"ok": True})
            return

        if u.path == "/api/guard/install":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            layers = body.get("layers") or list(guard.LAYERS)
            res = guard.install(proj["path"], layers)
            activity.record("scan", "guard installed")
            telemetry.capture("guard_install", {"layers": layers})
            terminal.get_terminal().emit(
                f"→ OnePort Guard protecting {proj['name']} ({', '.join(layers)})", "cmd")
            self._send(200, res)
            return

        if u.path == "/api/guard/uninstall":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            self._send(200, guard.uninstall(proj["path"]))
            return

        if u.path == "/api/tools/run":
            pid = (body.get("id") or "").strip()
            tool_id = (body.get("tool") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            base = proj.get("base") or "main"
            spec = next((t for t in toolspec.TOOLS if t["id"] == tool_id), None)
            if not spec:
                self._send(200, {"ok": False, "error": "unknown tool"})
                return
            term = terminal.get_terminal()
            term.set_cwd(proj["path"])
            # Intent conformance needs an intent spec. Rather than error out on a
            # new repo, auto-draft one from the codebase map so the tool can do its
            # job — clearly flagged as an AI draft the user should review.
            if tool_id == "conformance":
                # Conformance needs git history to check a change against intent —
                # if there's none (ZIP download), say so cleanly instead of drafting
                # a pointless intent doc then hitting a raw git error.
                gok, gwhy = toolspec._git_ready(proj["path"])
                if not gok:
                    term.emit(gwhy, "err")
                    activity.record("tool", tool_id)
                    self._send(200, {"ok": False, "requires": gwhy,
                                     "cmd": "oneport-conformance"})
                    return
                intent = Path(proj["path"]) / ".oneport" / "intent.md"
                if not intent.exists():
                    model, key = settings.ai_opts()
                    term.emit("No intent doc found — drafting .oneport/intent.md from your "
                              "codebase map…", "cmd")
                    drafted = repomap.generate_intent(pid, model=model, gemini_key=key)
                    try:
                        intent.parent.mkdir(parents=True, exist_ok=True)
                        intent.write_text(drafted.get("markdown", ""), encoding="utf-8")
                        if drafted.get("tokens"):
                            projects.add_tokens(pid, drafted["tokens"])
                        src = drafted.get("source")
                        term.emit(f"Wrote .oneport/intent.md ({'AI draft' if src=='ai' else 'template'}) "
                                  "— review & edit it, then re-run for a sharper check. "
                                  "Running conformance now…", "out")
                    except OSError as exc:
                        term.emit(f"Couldn't write intent doc: {exc}", "err")
            if spec.get("bg"):
                res = toolspec.run_background(tool_id, proj["path"], base, term)
            else:
                term.emit(f"→ {spec['title']}: {spec.get('exe','')} …", "cmd")
                res = toolspec.run(tool_id, proj["path"], base)
                if res.get("ok") and res.get("kind") == "findings":
                    n = len(res.get("findings") or [])
                    term.emit("clean" if res.get("clean") else f"{n} finding(s)", "out")
                elif res.get("ok"):
                    term.emit("done.", "out")
                else:
                    term.emit(res.get("error") or res.get("requires") or "failed", "err")
            activity.record("tool", tool_id)
            self._send(200, res)
            return

        if u.path == "/api/projects/debt":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            latest = (proj.get("history") or [None])[0] or {}
            activity.record("debt", "scan")
            self._send(200, debt.scan(proj["path"], latest.get("gates")))
            return

        if u.path == "/api/projects/debt/fix":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"ok": False, "error": "unknown project"})
                return
            latest = (proj.get("history") or [None])[0] or {}
            res = debt.scan(proj["path"], latest.get("gates"))
            if res.get("total", 0) == 0:
                self._send(200, {"ok": False, "error": "no technical debt found to fix"})
                return
            term = terminal.get_terminal()
            term.set_cwd(proj["path"])
            out = autofix.run_fix(
                proj, debt.fix_gate_entry(res), repomap.load_manifest(pid), term,
                rescan=lambda: scan_project(pid, "quick", "autofix"))
            self._send(200, out)
            return

        if u.path == "/api/projects/report":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            html_doc = report.generate(proj, repomap.load_manifest(pid))
            title = f"Readiness report · {proj.get('last_verdict') or 'unscanned'}"
            meta = artifacts.add_artifact(pid, "report", title, html_doc, "html")
            activity.record("report", "readiness")
            self._send(200, {"ok": True, "artifact": meta})
            return

        if u.path == "/api/projects/fixes":
            pid = (body.get("id") or "").strip()
            proj = projects.get_project(pid)
            if not proj:
                self._send(200, {"error": "unknown project"})
                return
            model, key = settings.ai_opts()
            res = fixes.generate(pid, proj["path"], proj, repomap.load_manifest(pid),
                                 model=model, gemini_key=key)
            if res.get("error"):
                self._send(200, res)
                return
            title = f"Fix bundle · {res['issues']} issue(s)"
            html_doc = report.md_page(title, res["markdown"], "OnePort — AI remediation bundle")
            meta = artifacts.add_artifact(pid, "fixes", title, html_doc, "html")
            if res.get("tokens"):
                projects.add_tokens(pid, res["tokens"])
            self._send(200, {"ok": True, "artifact": meta, "tokens": res["tokens"]})
            return

        if u.path == "/api/projects/artifact/open":
            # Write to an exports folder and open in the default browser (for
            # printing to PDF / sharing) — the webview sandbox blocks downloads.
            pid = (body.get("id") or "").strip()
            aid = (body.get("aid") or "").strip()
            fp = artifacts.artifact_file(pid, aid)
            if not fp:
                self._send(200, {"ok": False, "error": "artifact not found"})
                return
            try:
                import webbrowser
                webbrowser.open(fp.resolve().as_uri())
            except Exception as exc:
                self._send(200, {"ok": False, "error": str(exc)})
                return
            self._send(200, {"ok": True, "path": str(fp)})
            return

        if u.path == "/api/projects/artifact/delete":
            pid = (body.get("id") or "").strip()
            aid = (body.get("aid") or "").strip()
            self._send(200, {"ok": artifacts.delete_artifact(pid, aid)})
            return

        if u.path == "/api/projects/delete":
            pid = (body.get("id") or "").strip()
            projects.stop_watch(pid)
            self._send(200, {"ok": projects.delete_project(pid)})
            return

        self._send(404, {"error": "not found"})

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def log_message(self, *args) -> None:  # keep the console clean
        pass


def _sub(gate) -> str:
    return {
        "secrets": "working tree + git history",
        "dependencies": "manifests vs OSV.dev",
        "migrations": "Django / Alembic / SQL",
        "api-breaks": "public surface vs base branch",
        "test-gaps": "changed lines · needs login",
        "review": "last commit · needs login",
    }.get(gate.id, "")


def main() -> None:
    port = int(os.environ.get("PORT", "7333"))
    rulebook.refresh_async()   # best-effort: pull the latest guidance rules
    telemetry.start_heartbeat()  # desktop DAU/WAU: app_open + app_active pings
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"OnePort Desktop engine on http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
