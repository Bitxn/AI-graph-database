"""
LLM triage — the judgment layer, and ONLY the judgment layer.

Input to the model: a CVE that OSV.dev has already confirmed affects the pinned
version, plus deterministic usage evidence (file:line snippets) collected by
usage.py. Output: an exploitability classification with a one-line reason.

The model never decides WHETHER a package is vulnerable. Deterministic
short-circuits also skip the model entirely when its answer is forced:

  - dev-only dependency            → DEV-ONLY   (no LLM call)
  - package never imported at all  → LIKELY-UNREACHABLE (no LLM call)

Only findings with real usage evidence reach the model, one call per package
(all of its CVEs batched) to stay inside free-tier quotas.
"""

from __future__ import annotations

import json
import re

from oneport_depcheck.config import Config
from oneport_depcheck.exceptions import DepcheckError
from oneport_depcheck.llm import call_llm
from oneport_depcheck.result import Finding, Reachability
from oneport_depcheck.usage import find_usage, is_dev_path

SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



def _build_user_prompt(
    package: str,
    version: str,
    findings: list[Finding],
    evidence_block: str,
    guidelines: str,
) -> str:
    vuln_lines = []
    for f in findings:
        details = f.details[:1200]
        vuln_lines.append(
            f"- id: {f.vuln_id} ({f.cve})\n"
            f"  severity: {f.severity.value}\n"
            f"  summary: {f.summary or '(none)'}\n"
            f"  advisory: {details or '(none)'}"
        )
    # Report the spec the manifest ACTUALLY declares. Hardcoding "==" told the
    # model scrapy pinned `cryptography==37.0.0` when it declares `>=37.0.0` and
    # almost certainly runs something newer — so the model reasoned about
    # reachability in a version that may not be installed. When it's a floor, say
    # so, and let the model weigh that uncertainty instead of hiding it.
    exact = findings[0].version_exact if findings else True
    if exact:
        pkg_line = f"Package: {package}=={version} (pinned in the manifest)"
    else:
        pkg_line = (
            f"Package: {package}>={version} (the manifest declares a MINIMUM version; "
            f"the installed version may be newer and may already contain the fix)"
        )
    parts = [
        pkg_line,
        "",
        "Confirmed vulnerabilities (from OSV.dev):",
        "\n".join(vuln_lines),
        "",
        "Actual usage in this codebase (deterministic grep, file:line):",
        evidence_block or "(none found)",
    ]
    if guidelines:
        parts += ["", "Team guidelines (context only, do not change the JSON schema):",
                  guidelines[:2000]]
    return "\n".join(parts)


# Keys a model might wrap the verdict list under instead of returning a bare array.
_VERDICT_LIST_KEYS = ("verdicts", "findings", "results", "classifications",
                      "items", "triage")


def _loads_lenient(text: str):
    """json.loads, then a bracket-carve fallback for JSON embedded in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("[", "]"), ("{", "}")):
            s, e = text.find(opener), text.rfind(closer)
            if s != -1 and e > s:
                try:
                    return json.loads(text[s:e + 1])
                except json.JSONDecodeError:
                    continue
    return None


def _as_verdict_list(data) -> list:
    """Coerce any shape the model returns into a list of verdict dicts.

    Handles a bare array, an object wrapping the array under a key, a single
    verdict object, and an {id: classification} or {id: {classification,...}}
    map. Anything unrecognisable yields [] → no verdicts → deterministic fallback.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in _VERDICT_LIST_KEYS:
            if isinstance(data.get(k), list):
                return data[k]
        if "id" in data and "classification" in data:      # single verdict object
            return [data]
        entries = []                                        # id -> classification map
        for key, val in data.items():
            if isinstance(val, str):
                entries.append({"id": key, "classification": val})
            elif isinstance(val, dict):
                entries.append({"id": key, **val})
        return entries
    return []


def _parse_reply(text: str) -> dict[str, tuple[str, str]]:
    """Model reply → {vuln_id: (classification, reason)}.

    Shape-tolerant: the model may return a bare array, an object wrapping the
    list, a single verdict, or an id→classification map, fenced or in prose. A
    wobble must yield {} (→ deterministic 'no verdict' fallback), never raise and
    mark a whole package group UNTRIAGED — scrapy's docs-lxml group failed with
    'expected a JSON array' when the model wrapped its answer in an object.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    data = _loads_lenient(cleaned)
    # Genuinely unparseable output ("sorry, I can't") is a real triage failure →
    # raise so the group is marked UNTRIAGED (honest "we couldn't judge"). Only a
    # valid-JSON-but-wrong-SHAPE reply (an object instead of an array — scrapy's
    # bug) is tolerated below and normalised into verdicts.
    if data is None:
        raise ValueError("model reply was not valid JSON")
    entries = _as_verdict_list(data)

    out: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        vuln_id = str(entry.get("id", ""))
        cls = str(entry.get("classification", "")).upper().replace("_", "-")
        reason = str(entry.get("reason", ""))[:400]
        if vuln_id and cls in ("REACHABLE", "LIKELY-UNREACHABLE"):
            out[vuln_id] = (cls, reason)
    return out


def triage_findings(
    findings: list[Finding],
    project_root: str,
    config: Config,
    guidelines: str = "",
) -> int:
    """Classify every finding in place. Returns total model tokens used."""
    total_tokens = 0

    # Group findings by (package, version) — one evidence pass and at most one
    # model call per package.
    by_package: dict[tuple[str, str, str], list[Finding]] = {}
    for f in findings:
        by_package.setdefault((f.package, f.version, f.ecosystem), []).append(f)

    for (package, version, ecosystem), group in by_package.items():
        evidence = find_usage(
            project_root, package, ecosystem,
            max_snippets=config.max_evidence_snippets,
        )
        for f in group:
            f.evidence = evidence

        # Deterministic short-circuit 1: dev-only dependency.
        if all(f.is_dev for f in group):
            for f in group:
                f.reachability = Reachability.DEV_ONLY
                f.reachability_reason = (
                    f"Declared in a dev/test manifest section ({group[0].manifest})."
                )
            continue

        runtime_evidence = [e for e in evidence if not is_dev_path(e.file)]

        # Deterministic short-circuit 2: never imported in scanned source.
        if not evidence:
            for f in group:
                f.reachability = Reachability.LIKELY_UNREACHABLE
                f.reachability_reason = (
                    f"'{package}' is never imported in the scanned source tree."
                )
            continue

        # Deterministic short-circuit 3: only imported under tests/fixtures.
        if not runtime_evidence:
            for f in group:
                f.reachability = Reachability.DEV_ONLY
                f.reachability_reason = (
                    f"'{package}' is only used in test/dev paths "
                    f"(e.g. {evidence[0].file}:{evidence[0].line})."
                )
            continue

        # Judgment call — this is what the model is for.
        evidence_block = "\n".join(
            f"- {e.file}:{e.line}: {e.snippet}" for e in runtime_evidence
        )
        user = _build_user_prompt(package, version, group, evidence_block, guidelines)
        try:
            text, tokens = call_llm(
                config.model, config.api_key, SYSTEM_PROMPT, user, config.max_tokens
            )
            total_tokens += tokens
            verdicts = _parse_reply(text)
        except (DepcheckError, ValueError, json.JSONDecodeError) as exc:
            for f in group:
                f.reachability = Reachability.UNTRIAGED
                f.reachability_reason = f"Triage failed: {exc}"
            continue

        for f in group:
            cls, reason = verdicts.get(f.vuln_id, ("", ""))
            if cls == "REACHABLE":
                f.reachability = Reachability.REACHABLE
            elif cls == "LIKELY-UNREACHABLE":
                f.reachability = Reachability.LIKELY_UNREACHABLE
            else:
                # Model skipped this id — fail toward attention, with evidence.
                f.reachability = Reachability.REACHABLE
                reason = (
                    f"Package is used at runtime ({runtime_evidence[0].file}:"
                    f"{runtime_evidence[0].line}); model gave no verdict for this id."
                )
            f.reachability_reason = reason

    return total_tokens
