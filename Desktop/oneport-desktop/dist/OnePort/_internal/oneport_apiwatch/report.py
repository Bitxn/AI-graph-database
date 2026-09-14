"""Rendering: turn a Report into JSON or a human-readable inline summary."""

from __future__ import annotations

import json

from rich.console import Console
from rich.text import Text

from oneport_apiwatch.result import CheckResult, Report


def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)


def _plabel(value: str) -> str:
    """Escape a Prometheus label value (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_prometheus(report: Report) -> str:
    """Prometheus text-exposition gauges — scrape via node_exporter textfile or
    push to a Pushgateway from CI/cron. One series per check."""
    metrics: list[tuple[str, str, str, list[tuple[str, float]]]] = [
        ("apiwatch_up", "gauge", "1 if the check passed this run, else 0",
         [(c.name, 1.0 if c.ok else 0.0) for c in report.checks]),
        ("apiwatch_latency_ms", "gauge", "Observed round-trip latency in milliseconds",
         [(c.name, float(c.latency_ms)) for c in report.checks]),
        ("apiwatch_status_code", "gauge", "HTTP status code (0 = no response)",
         [(c.name, float(c.status_code or 0)) for c in report.checks]),
        ("apiwatch_consecutive_failures", "gauge", "Consecutive failed runs including this one",
         [(c.name, float(c.consecutive_failures)) for c in report.checks]),
        ("apiwatch_tripped", "gauge", "1 if the check has tripped its failure threshold",
         [(c.name, 1.0 if c.tripped else 0.0) for c in report.checks]),
        ("apiwatch_muted", "gauge", "1 if the check is under a maintenance mute",
         [(c.name, 1.0 if c.muted else 0.0) for c in report.checks]),
    ]
    by_name = {c.name: c for c in report.checks}
    lines: list[str] = []
    for metric, kind, help_text, samples in metrics:
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} {kind}")
        for name, value in samples:
            method = by_name[name].method
            val = f"{value:g}"
            lines.append(f'{metric}{{check="{_plabel(name)}",method="{_plabel(method)}"}} {val}')
    return "\n".join(lines) + "\n"


def render_inline(report: Report, console: Console | None = None) -> None:
    """Print a coloured per-check summary to the console."""
    console = console or Console()
    total = report.summary["total"]

    for check in report.checks:
        _render_check(check, console)

    console.print()
    if report.gate_ok:
        extra = ""
        if report.muted:
            extra += f", {len(report.muted)} muted"
        if any(not c.ok and not c.tripped and not c.muted for c in report.checks):
            extra += ", some degraded (below failure threshold)"
        console.print(Text.assemble(
            ("PASS ", "bold green"),
            (f"{total - len(report.tripped)}/{total} check(s) healthy{extra}", "green"),
        ))
    else:
        console.print(Text.assemble(
            ("FAIL ", "bold red"),
            (f"{len(report.tripped)}/{total} check(s) DOWN (tripped failure threshold)", "red"),
        ))
    for c in report.recovered:
        console.print(Text.assemble(("RECOVERED ", "bold green"), (c.name, "green")))


def _render_check(check: CheckResult, console: Console) -> None:
    if check.muted:
        reason = f"  ({check.mute_reason})" if check.mute_reason else ""
        console.print(Text.assemble(
            ("  [MUTED] ", "bold yellow"), (check.name, "bold"),
            (f"  {check.method} {check.url}{reason}", "dim"),
        ))
        return
    if check.ok:
        console.print(Text.assemble(
            ("  [PASS] ", "bold green"), (check.name, "bold"),
            (f"  {check.method} {check.url}", "dim"),
            (f"  [{check.status_code}, {check.latency_ms:.0f}ms]", "green"),
        ))
        return

    status = f"{check.status_code}" if check.status_code is not None else "no response"
    # Failed this run but not yet tripped = degraded (riding out transient blips).
    tag, style = ("[DOWN] ", "bold red") if check.tripped else ("[DEGRADED] ", "bold yellow")
    suffix = ""
    if not check.tripped and check.threshold > 1:
        suffix = f"  ({check.consecutive_failures}/{check.threshold} before it trips)"
    console.print(Text.assemble(
        ("  " + tag, style), (check.name, "bold"),
        (f"  {check.method} {check.url}", "dim"),
        (f"  [{status}, {check.latency_ms:.0f}ms]{suffix}", style),
    ))
    for reason in check.failures:
        console.print(Text.assemble(("      - ", style), (reason, style)))

    if check.diagnosis is not None:
        _render_diagnosis(check, console)


def _render_diagnosis(check: CheckResult, console: Console) -> None:
    diag = check.diagnosis
    assert diag is not None
    if diag.error:
        console.print(Text(f"      AI diagnosis unavailable: {diag.error}", "dim yellow"))
        return
    console.print(Text.assemble(("      AI diagnosis: ", "bold cyan"), (diag.summary, "cyan")))
    for i, cause in enumerate(diag.likely_causes, 1):
        console.print(Text(f"        {i}. {cause}", "cyan"))
    if diag.suggested_action:
        console.print(
            Text.assemble(("        first action: ", "bold cyan"), (diag.suggested_action, "cyan"))
        )
