"""
Compliance frameworks — the control catalogs, and which Oneport gate provides
evidence for each control.

This mapping is the domain knowledge that makes the evidence credible: an auditor
doesn't care that "secrets scanning ran 143 times", they care that *Control CC6.1
(logical access / credential protection) was operating*. Each control lists the
gate keys whose runs demonstrate it.

Control text is paraphrased from the public criteria (AICPA SOC 2 Trust Services
Criteria; ISO/IEC 27001:2022 Annex A) — enough to anchor the evidence, not a
substitute for the standards themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

from oneport_evidence.exceptions import UnknownFramework


@dataclass(frozen=True)
class Control:
    id: str
    name: str
    description: str
    gates: tuple[str, ...]        # oneport gate keys that evidence this control


@dataclass(frozen=True)
class Framework:
    id: str
    name: str
    controls: tuple[Control, ...]


SOC2 = Framework(
    id="soc2",
    name="SOC 2 (Trust Services Criteria)",
    controls=(
        Control("CC6.1", "Logical access — credential protection",
                "The entity protects credentials and secrets against unauthorized "
                "exposure. Evidenced by automated secret scanning gating every change.",
                ("secrets",)),
        Control("CC6.8", "Prevention of unauthorized or malicious software/changes",
                "Controls prevent unauthorized or unsafe code from reaching production. "
                "Evidenced by automated code review and secret scanning on each change.",
                ("review", "secrets")),
        Control("CC7.1", "Vulnerability management",
                "The entity identifies vulnerabilities in components it depends on and "
                "acts on them. Evidenced by dependency CVE scanning gating changes.",
                ("depcheck",)),
        Control("CC8.1", "Change management",
                "Changes are authorized, tested, and reviewed before deployment. "
                "Evidenced by code review, migration-safety, API-compatibility, "
                "test-coverage, and change-impact gates on each deployment.",
                ("review", "migrate", "apidiff", "testgap", "impact")),
        Control("CC7.2", "System monitoring for quality & anomalies",
                "The entity monitors changes for quality and anomalies prior to release. "
                "Evidenced by test-gap and change-impact analysis on each change.",
                ("testgap", "impact")),
    ),
)

ISO27001 = Framework(
    id="iso27001",
    name="ISO/IEC 27001:2022 (Annex A)",
    controls=(
        Control("A.8.28", "Secure coding",
                "Secure coding principles are applied to software development. "
                "Evidenced by automated code review gating each change.",
                ("review",)),
        Control("A.8.8", "Management of technical vulnerabilities",
                "Information about technical vulnerabilities is obtained and exposure "
                "evaluated. Evidenced by dependency CVE scanning on each change.",
                ("depcheck",)),
        Control("A.8.12", "Data leakage prevention",
                "Measures prevent the disclosure of sensitive information such as "
                "credentials. Evidenced by secret & .env scanning on each change.",
                ("secrets",)),
        Control("A.8.32", "Change management",
                "Changes to information-processing facilities are subject to change "
                "management. Evidenced by migration-safety, API-compatibility, review, "
                "and change-impact gates on each deployment.",
                ("migrate", "apidiff", "review", "impact")),
        Control("A.8.29", "Security testing in development and acceptance",
                "Security testing is performed during the development lifecycle. "
                "Evidenced by test-coverage and code-review gates on each change.",
                ("testgap", "review")),
    ),
)

_FRAMEWORKS = {f.id: f for f in (SOC2, ISO27001)}
_ALIASES = {"soc2": "soc2", "soc-2": "soc2", "soc": "soc2",
            "iso27001": "iso27001", "iso": "iso27001", "iso-27001": "iso27001",
            "27001": "iso27001"}


def get_framework(fid: str) -> Framework:
    key = _ALIASES.get(fid.strip().lower(), fid.strip().lower())
    fw = _FRAMEWORKS.get(key)
    if fw is None:
        raise UnknownFramework(
            f"Unknown framework '{fid}'. Available: " + ", ".join(sorted(_FRAMEWORKS)))
    return fw


def list_frameworks() -> list[Framework]:
    return list(_FRAMEWORKS.values())
