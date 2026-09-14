"""Printable HTML evidence pack — monochrome, prints cleanly to PDF from a browser."""

from __future__ import annotations

from datetime import datetime, timezone

from oneport_evidence.result import EvidenceReport


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"


_CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0d0d0d;
max-width:860px;margin:32px auto;padding:0 24px;line-height:1.5}
h1{font-size:26px;margin-bottom:4px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid #e5e5e5;padding-bottom:4px}
.muted{color:#6e6e80}.banner{background:#0d0d0d;color:#fff;text-align:center;font-size:11px;
letter-spacing:1px;padding:5px;border-radius:4px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
th{background:#0d0d0d;color:#fff;text-align:left;padding:6px 8px;font-size:11px}
td{padding:6px 8px;border-bottom:1px solid #ececed}tr:nth-child(even) td{background:#f7f7f8}
.kpis{display:flex;gap:1px;background:#e5e5e5;border:1px solid #e5e5e5;border-radius:8px;overflow:hidden;margin:12px 0}
.kpi{background:#fff;flex:1;padding:12px;text-align:center}.kpi .n{font-size:22px;font-weight:700}
.kpi .l{font-size:10px;text-transform:uppercase;color:#6e6e80;letter-spacing:.5px}
.sig{display:flex;gap:40px;margin-top:24px}.sig div{flex:1;border-top:1px solid #0d0d0d;padding-top:6px;
font-size:12px;color:#6e6e80}@media print{body{margin:0}}
"""


def render_html(report: EvidenceReport, classification: str = "CONFIDENTIAL") -> str:
    s = report.to_dict()["summary"]
    rows = "".join(
        f"<tr><td>{c.control_id}</td><td>{c.name}</td>"
        f"<td>{'Operating' if c.operating else 'No activity'}</td>"
        f"<td>{c.runs}</td><td>{c.blocked}</td><td>{_date(c.last_run)}</td></tr>"
        for c in report.controls)
    detail = "".join(
        f"<p><b>{c.control_id} — {c.name}</b><br><span class='muted'>{c.description}</span><br>"
        f"Enforcing gates: {', '.join(c.gates)} · Executions: {c.runs} "
        f"(passed {c.passed}, blocked {c.blocked}) · "
        f"Operating: {'Yes' if c.operating else 'No activity'}</p>"
        for c in report.controls)
    gate_rows = "".join(
        f"<tr><td>{g.gate}</td><td>{g.runs}</td><td>{g.passed}</td>"
        f"<td>{g.blocked}</td><td>{_date(g.last_run)}</td></tr>"
        for g in report.gate_summaries) or "<tr><td colspan=5 class='muted'>No activity</td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{report.org} — Compliance Evidence Pack</title><style>{_CSS}</style></head><body>
<div class="banner">{classification}</div>
<h1>{report.org}</h1>
<div class="muted">Compliance Evidence Pack · {report.framework_name}</div>
<div class="muted">Period {_date(report.period_start)} → {_date(report.period_end)} ·
generated {_date(report.generated_at)} (UTC)</div>
<div class="kpis">
  <div class="kpi"><div class="n">{s['deploys_gated']}</div><div class="l">Deploys gated</div></div>
  <div class="kpi"><div class="n">{s['blocked_deploys']}</div><div class="l">Blocked</div></div>
  <div class="kpi"><div class="n">{s['total_gate_runs']}</div><div class="l">Control runs</div></div>
  <div class="kpi"><div class="n">{s['controls_operating']}/{s['controls_total']}</div><div class="l">Controls operating</div></div>
</div>
<h2>Executive summary</h2>
<p>During the reporting period, <b>{s['deploys_gated']}</b> deployment(s) across
<b>{s['repositories']}</b> repositor(y/ies) were subjected to Oneport pre-ship controls,
comprising <b>{s['total_gate_runs']}</b> automated control execution(s).
<b>{s['blocked_deploys']}</b> were blocked for policy violations and required remediation
before release.</p>
<h2>Control coverage</h2>
<table><tr><th>Control</th><th>Name</th><th>Status</th><th>Runs</th><th>Caught</th><th>Last run</th></tr>{rows}</table>
<h2>Control detail</h2>{detail}
<h2>Gate activity</h2>
<table><tr><th>Gate</th><th>Runs</th><th>Passed</th><th>Blocked</th><th>Last run</th></tr>{gate_rows}</table>
<h2>Attestation</h2>
<p>Generated deterministically from {report.org}'s local Oneport control ledger. No data
left the organization's environment; no generative model was used.</p>
<div class="sig"><div>Prepared by<br><br></div><div>Reviewed by<br><br></div><div>Date<br><br></div></div>
</body></html>
"""
