"""
Core orchestrator.

Analyzer.analyze() is the single entry point:
  1. Parse IaC in the repo (deterministic) → priced Resource inventory
  2. Build the waste-detection prompt from that inventory
  3. Call the model (Gemini or Claude)
  4. Parse the response → CostReport

analyze_paths() runs the deterministic + LLM pass on an explicit resource list,
so the cost-diff (--post) path can reuse it for a base and a head revision.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from costwatch.config import Config, load_config
from costwatch.exceptions import ParseError
from costwatch.guidelines import load_guidelines
from costwatch.llm import call_model
from costwatch.parsers import discover_and_parse
from costwatch.prompt_builder import build_prompt
from costwatch.result import CostReport, Finding, Resource, Severity


# Keys a model might use for the finding list instead of the canonical "findings".
_FINDINGS_KEYS = ("findings", "results", "issues", "items", "recommendations",
                  "waste", "problems")


def _parse_findings_json(raw: str) -> dict:
    """Parse the model's JSON into a canonical {"findings": [...]} envelope.

    Models do NOT reliably honor the requested shape: some return a bare array
    `[{...}]`, some wrap it under `results`/`issues`, some emit one finding
    object, some fence it or wrap it in prose. Every sibling tool normalizes
    these (a bare array is exactly what crashed secrets/migrate before they were
    hardened); cost analysis must too — a shape wobble from the LLM must never
    crash the run. A genuinely non-JSON reply still raises ParseError, so op
    ship's "model unreadable → not fully verified" handling is preserved.
    """
    text = raw.strip()
    if text.startswith("```"):                       # strip a ```json / ``` fence
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        data = _carve_json(text)                     # last resort: dig it out of prose
        if data is None:
            raise ParseError("Model returned non-JSON output", raw=raw) from exc
    return _as_findings_envelope(data)


def _carve_json(text: str) -> Any:
    """Pull the outermost JSON array/object out of surrounding prose."""
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _as_findings_envelope(data: Any) -> dict:
    """Coerce any parsed JSON shape into {"findings": [<dict>, ...]}."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k in _FINDINGS_KEYS:                     # canonical or synonym list key
            if isinstance(data.get(k), list):
                items = data[k]
                break
        else:
            # A single finding object → wrap it; anything else has no findings.
            items = [data] if any(
                key in data for key in ("resource", "severity", "message", "category")
            ) else []
    else:
        items = []
    return {"findings": [it for it in items if isinstance(it, dict)]}


class Analyzer:
    def __init__(self, config: Config | None = None, config_path: str | None = None) -> None:
        self.config = config or load_config(config_path=config_path)
        self._guidelines = load_guidelines(self.config.guidelines_path)
        self._last_tokens = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(
        self, path: str | Path, plan_path: str | Path | None = None
    ) -> CostReport:
        """Analyze the IaC under `path` (a file or directory) and return findings."""
        start = time.monotonic()
        resources, notes = discover_and_parse(
            path, plan_path=plan_path, ignore_paths=self.config.ignore_paths
        )
        report = self._analyze_resources(resources, context="")
        report.path = str(path)
        report.notes = notes + report.notes
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report

    def analyze_resources(
        self, resources: list[Resource], context: str = ""
    ) -> CostReport:
        """Run the deterministic + LLM pass on an explicit resource list."""
        start = time.monotonic()
        report = self._analyze_resources(resources, context=context)
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report

    # ── Internal ───────────────────────────────────────────────────────────────

    def _analyze_resources(self, resources: list[Resource], context: str) -> CostReport:
        if not resources:
            return CostReport(
                resources=[], findings=[], model=self.config.model, priced=False,
                notes=["No priced resources to analyze."],
            )

        prompt = build_prompt(resources, guidelines=self._guidelines, context=context)
        raw, tokens = call_model(
            model=self.config.model,
            api_key=self.config.api_key,
            system=prompt.system,
            user=prompt.user,
            max_tokens=self.config.max_tokens,
        )
        self._last_tokens = tokens
        findings = self._parse_findings(raw, resources)
        return CostReport(
            resources=resources,
            findings=findings,
            model=self.config.model,
            total_tokens=tokens,
            priced=True,
        )

    def _parse_findings(self, raw: str, resources: list[Resource]) -> list[Finding]:
        data = _parse_findings_json(raw)
        # Fill in file/line from the matching resource when the model omits them.
        by_ref = {f"{r.kind}.{r.name}": r for r in resources}

        findings: list[Finding] = []
        for item in data.get("findings", []):
            try:
                severity = Severity(str(item.get("severity", "warning")).lower())
            except ValueError:
                severity = Severity.WARNING

            ref = item.get("resource", "")
            match = by_ref.get(ref)
            findings.append(Finding(
                severity=severity,
                category=item.get("category", "over-provisioned"),
                resource=ref,
                message=item.get("message", ""),
                current_config=item.get("current_config", ""),
                suggested_config=item.get("suggested_config", ""),
                estimated_monthly_saving=_to_float(item.get("estimated_monthly_saving")),
                file=item.get("file") or (match.file if match else ""),
                line=_to_int(item.get("line")) or (match.line if match else 0),
            ))

        # Biggest savings first, then severity.
        findings.sort(key=lambda f: (-f.estimated_monthly_saving, -f.severity.rank))
        return findings


def _to_float(val: Any) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _to_int(val: Any) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0
