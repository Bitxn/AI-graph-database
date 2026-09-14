"""Markdown evidence pack — plain, reviewable, diffable."""

from __future__ import annotations

from datetime import datetime, timezone

from oneport_evidence.result import EvidenceReport


def _date(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def render_markdown(report: EvidenceReport) -> str:
    s = report.to_dict()["summary"]
    out: list[str] = []
    out.append(f"# {report.org} — Compliance Evidence Pack")
    out.append(f"**Framework:** {report.framework_name}  ")
    out.append(f"**Period:** {_date(report.period_start)} → {_date(report.period_end)}  ")
    out.append(f"**Generated:** {_date(report.generated_at)} (UTC)  ")
    out.append("")
    out.append("## Executive summary")
    out.append(
        f"During the reporting period, **{s['deploys_gated']} deployment(s)** across "
        f"**{s['repositories']} repositor(y/ies)** were subjected to Oneport pre-ship "
        f"controls, comprising **{s['total_gate_runs']} automated control execution(s)**. "
        f"**{s['blocked_deploys']}** deployment(s) were blocked for policy violations and "
        f"required remediation before release. "
        f"**{s['controls_operating']} of {s['controls_total']}** mapped controls were "
        f"operating (executed at least once) during the period.")
    out.append("")

    out.append("## Control coverage")
    out.append("| Control | Name | Status | Executions | Violations caught | Last run |")
    out.append("|---|---|---|---|---|---|")
    for c in report.controls:
        status = "Operating" if c.operating else "No activity"
        out.append(f"| {c.control_id} | {c.name} | {status} | {c.runs} | {c.blocked} "
                   f"| {_date(c.last_run)} |")
    out.append("")

    out.append("## Control detail")
    for c in report.controls:
        out.append(f"### {c.control_id} — {c.name}")
        out.append(c.description)
        out.append("")
        out.append(f"- **Enforcing gates:** {', '.join(c.gates)}")
        out.append(f"- **Executions in period:** {c.runs}  "
                   f"(passed {c.passed}, blocked {c.blocked}, warned {c.warned})")
        out.append(f"- **Operating effectively:** {'Yes' if c.operating else 'No activity in period'}")
        out.append("")

    out.append("## Gate activity")
    out.append("| Gate | Runs | Passed | Blocked | Last run |")
    out.append("|---|---|---|---|---|")
    for g in report.gate_summaries:
        out.append(f"| {g.gate} | {g.runs} | {g.passed} | {g.blocked} | {_date(g.last_run)} |")
    if not report.gate_summaries:
        out.append("| _no activity_ | | | | |")
    out.append("")

    out.append("## Attestation")
    out.append(
        f"This pack was generated deterministically from {report.org}'s local Oneport "
        "control ledger. Each execution count reflects an automated control that ran "
        "against a change before it was permitted to ship. No data left the "
        "organization's environment in producing this evidence, and no generative model "
        "was used — the figures are a direct, auditable count of control executions.")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append("| Prepared by | ______________________ |")
    out.append("| Reviewed by | ______________________ |")
    out.append("| Date | ______________________ |")
    for n in report.notes:
        out.append(f"\n> Note: {n}")
    return "\n".join(out) + "\n"
