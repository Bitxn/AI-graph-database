"""
The aggregator — roll gate runs up into an EvidenceReport for a framework + period.

Deterministic, no model. Ship events are reconstructed from run timestamps
(gate runs from the same repo within a short window are one deployment gate), which
gives the "N deployments gated, M blocked" headline auditors want.
"""

from __future__ import annotations

import time

from oneport_evidence.frameworks import Framework
from oneport_evidence.result import (
    ControlEvidence, EvidenceReport, GateRun, GateSummary,
)

_SHIP_BUCKET_S = 300   # gate runs within 5 min of each other = one ship event


def build_report(
    runs: list[GateRun], framework: Framework, org: str,
    period_start: int, period_end: int,
) -> EvidenceReport:
    runs = [r for r in runs if period_start <= r.ts <= period_end and r.ran]

    report = EvidenceReport(
        org=org, framework_id=framework.id, framework_name=framework.name,
        period_start=period_start, period_end=period_end, generated_at=int(time.time()))

    # ── per-gate summaries ───────────────────────────────────────────────────
    gates: dict[str, GateSummary] = {}
    for r in runs:
        g = gates.get(r.gate)
        if g is None:
            g = GateSummary(gate=r.gate, runs=0, passed=0, blocked=0, warned=0, last_run=0)
            gates[r.gate] = g
        g.runs += 1
        g.passed += int(r.passed)
        g.blocked += int(r.blocked)
        g.warned += int(r.status == "warn")
        g.last_run = max(g.last_run, r.ts)
    report.gate_summaries = sorted(gates.values(), key=lambda x: -x.runs)

    # ── per-control evidence ─────────────────────────────────────────────────
    for ctrl in framework.controls:
        ev = ControlEvidence(control_id=ctrl.id, name=ctrl.name,
                             description=ctrl.description, gates=list(ctrl.gates))
        for gate_key in ctrl.gates:
            g = gates.get(gate_key)
            if g:
                ev.runs += g.runs
                ev.passed += g.passed
                ev.blocked += g.blocked
                ev.warned += g.warned
                ev.last_run = max(ev.last_run, g.last_run)
        report.controls.append(ev)

    # ── headline: reconstruct ship events ────────────────────────────────────
    events = _reconstruct_events(runs)
    report.total_runs = len(runs)
    report.deploys_gated = len(events)
    report.blocked_deploys = sum(1 for blocked in events.values() if blocked)
    report.repos = len({r.repo_hash for r in runs if r.repo_hash})

    if not runs:
        report.notes.append("No gate runs recorded in this period.")
    return report


def _reconstruct_events(runs: list[GateRun]) -> dict[tuple, bool]:
    """Group runs into ship events by (repo, time-bucket); value = was it blocked."""
    events: dict[tuple, bool] = {}
    for r in runs:
        key = (r.repo_hash, r.ts // _SHIP_BUCKET_S)
        events[key] = events.get(key, False) or r.blocked
    return events


def collect_runs(since_ts: int, source: str, ledger_path=None, evidence_db=None) -> list[GateRun]:
    """Gate runs from the chosen source ('ledger' | 'ingested')."""
    from oneport_evidence.store import EvidenceStore, read_ledger_runs
    if source == "ingested":
        return EvidenceStore(evidence_db).read_runs(since_ts)
    return read_ledger_runs(since_ts, ledger_path)
