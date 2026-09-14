"""Result dataclasses shared by the scanner, triage layer, formatters, and CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}[self.value]


class Reachability(str, Enum):
    # LLM-triaged: the vulnerable code path is plausibly hit by this codebase.
    REACHABLE = "REACHABLE"
    # LLM-triaged or deterministic: package unused / vulnerable path not exercised.
    LIKELY_UNREACHABLE = "LIKELY-UNREACHABLE"
    # Deterministic: the dependency only appears in a dev/test manifest section.
    DEV_ONLY = "DEV-ONLY"
    # Triage skipped (--no-llm) or the model reply couldn't be parsed.
    UNTRIAGED = "UNTRIAGED"


# Sort keys: what to fix first. REACHABLE always above everything else.
_REACH_ORDER = {
    Reachability.REACHABLE: 0,
    Reachability.UNTRIAGED: 1,
    Reachability.LIKELY_UNREACHABLE: 2,
    Reachability.DEV_ONLY: 3,
}


@dataclass
class Package:
    """One dependency as declared in a manifest or lockfile."""
    name: str
    version: str | None          # None = unpinned (can't be queried against OSV)
    ecosystem: str               # "PyPI" | "npm" (OSV ecosystem names)
    manifest: str                # repo-relative path of the manifest file
    line: int                    # 1-based line number of the declaration
    is_dev: bool = False
    version_exact: bool = True   # False when derived from a range like ^1.2.3


@dataclass
class UsageEvidence:
    file: str
    line: int
    snippet: str


@dataclass
class Finding:
    """One (package, vulnerability) pair confirmed by OSV.dev."""
    package: str
    version: str
    ecosystem: str
    manifest: str
    manifest_line: int
    is_dev: bool
    vuln_id: str                     # OSV id (GHSA-... or CVE-...)
    # False when `version` is a range floor (e.g. "cryptography>=37.0.0"), not a
    # pin. Rendering those as "pkg==37.0.0" misrepresents the manifest — the
    # project permits anything above the floor, and the installed version is
    # probably newer. Only a lockfile/`==` gives an exact version.
    version_exact: bool = True
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    details: str = ""
    severity: Severity = Severity.UNKNOWN
    cvss_score: float | None = None
    fixed_version: str | None = None
    references: list[str] = field(default_factory=list)

    # Triage (LLM layer or deterministic short-circuits)
    reachability: Reachability = Reachability.UNTRIAGED
    reachability_reason: str = ""
    evidence: list[UsageEvidence] = field(default_factory=list)

    # Upgrade guidance (deterministic)
    upgrade_target: str | None = None
    break_risk: str = ""
    fix_suggestion: str = ""         # exact replacement manifest line, if committable

    # Waiver (approved via .oneport/depcheck-waivers.yml) — shown, but excluded
    # from the --fail-on gate.
    waived: bool = False
    waiver_reason: str = ""

    @property
    def spec(self) -> str:
        """`pkg==1.2.3` when pinned, `pkg>=1.2.3` when the manifest gave a range.

        Single source of truth for every formatter — hardcoding "==" told users
        scrapy pinned `cryptography==37.0.0` when it actually declares
        `cryptography>=37.0.0` and almost certainly runs something newer.
        """
        return f"{self.package}{'==' if self.version_exact else '>='}{self.version}"

    @property
    def cve(self) -> str:
        """Prefer the CVE alias for display; fall back to the OSV id."""
        for alias in self.aliases:
            if alias.startswith("CVE-"):
                return alias
        return self.vuln_id

    def sort_key(self) -> tuple:
        return (
            _REACH_ORDER[self.reachability],
            -self.severity.rank,
            -(self.cvss_score or 0.0),
            self.package,
            self.vuln_id,
        )


@dataclass
class LicenseIssue:
    package: str
    version: str | None
    ecosystem: str
    manifest: str
    manifest_line: int
    license: str
    risk: str        # BLOCKED | REVIEW | UNKNOWN
    detail: str


def _finding_dict(f: Finding) -> dict:
    """Serialize a Finding, adding a self-identifying `message`.

    `op ship` renders one line per finding and picks the first of several message
    keys — falling through to `summary`, which is just the CVE title. A lockfile
    can legitimately carry two vulnerable copies of the same dependency (saleor
    ships undici 6.21.3 AND 7.14.0), and both map to the same manifest line, so
    the verdict printed the identical blocker twice with nothing to tell them
    apart. Leading with the spec makes every line identify itself.
    """
    d = asdict(f)
    d["message"] = f"{f.spec} — {f.summary}" if f.summary else f.spec
    return d


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    license_issues: list[LicenseIssue] = field(default_factory=list)
    packages_scanned: int = 0
    manifests: list[str] = field(default_factory=list)
    skipped_unpinned: list[str] = field(default_factory=list)
    target: str = "."
    model: str = ""
    triaged: bool = False
    total_tokens: int = 0
    elapsed_ms: int = 0

    @property
    def reachable(self) -> list[Finding]:
        return [f for f in self.findings if f.reachability == Reachability.REACHABLE]

    def counts_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        return counts

    def counts_by_reachability(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.reachability.value] = counts.get(f.reachability.value, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "packages_scanned": self.packages_scanned,
            "manifests": self.manifests,
            "skipped_unpinned": self.skipped_unpinned,
            "model": self.model if self.triaged else "",
            "triaged": self.triaged,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "waived": sum(1 for f in self.findings if f.waived),
            "findings": [_finding_dict(f) for f in self.findings],
            "license_issues": [asdict(li) for li in self.license_issues],
        }
