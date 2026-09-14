"""
Data models for review results.

ReviewResult is the single object returned by Reviewer.review() and the
programmatic API. It serialises cleanly to JSON and is the source of truth
that all formatters render from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]

    def __ge__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.rank >= other.rank

    def __gt__(self, other: "Severity") -> bool:  # type: ignore[override]
        return self.rank > other.rank


def compute_blocking(issues: list["Issue"], fail_on: "Severity | str" = Severity.ERROR) -> bool:
    """True if any NON-WAIVED issue is at or above the ``fail_on`` threshold.

    This is the single source of truth for the CI-fail decision. Waived issues
    (approved in .oneport/review-waivers.yml) never count, at any threshold."""
    if isinstance(fail_on, str):
        fail_on = Severity(fail_on)
    return any(i.severity >= fail_on for i in issues if not getattr(i, "waived", False))


@dataclass
class Location:
    """File location of an issue."""

    file: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None


@dataclass
class Issue:
    """A single code review finding."""

    rule_id: str
    severity: Severity
    message: str
    suggestion: str
    location: Location
    snippet: str = ""
    category: str = ""           # security | performance | logic | style | pattern
    docs_url: str = ""
    fix: str = ""                # exact replacement code for location.line..end_line;
                                 # empty when the model isn't confident enough to
                                 # propose a committable fix. Rendered as a GitHub
                                 # ```suggestion block (one-click "Commit suggestion").
    waived: bool = False         # approved via .oneport/review-waivers.yml — shown
                                 # but excluded from the blocking decision.
    waiver_reason: str = ""      # why it was accepted (from the waiver entry).

    @property
    def file(self) -> str:
        return self.location.file

    @property
    def line(self) -> int:
        return self.location.line

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class ReviewResult:
    """
    The complete result of a review run.

    Attributes:
        issues:        Detected issues, sorted by severity desc then file/line asc.
                       May be a display-filtered subset after `.filter()` — see `blocking`.
        target:        The file path or PR URL that was reviewed.
        model:         The Claude model used.
        cached:        True if this result was served from the local cache.
        total_tokens:  Total tokens consumed (input + output).
        elapsed_ms:    Wall-clock time for the review in milliseconds.
        blocking:      True if the FULL (unfiltered) issue set contained an error/critical
                       finding. Fixed at construction time and carried through `.filter()`
                       unchanged, so a display filter (e.g. --min-severity critical) can
                       never hide a real error/critical from the CI exit-code decision.
        diff:          The raw diff text that was reviewed, when the target was a PR.
                       Carried along so a caller can post an inline PR review afterwards
                       without re-fetching it. Empty for plain file reviews.
        pr_ref:        {"owner", "repo", "number", "head_sha"} when the target was a
                       GitHub PR, else None. Used to post an inline review back to
                       that PR (and to stamp the reviewed-SHA marker for incremental
                       reviews).
        incremental_from: When this was an incremental review (only the commits
                       pushed since the last Oneport review), the SHA the diff
                       started from. Empty for full reviews.
    """

    issues: list[Issue] = field(default_factory=list)
    target: str = ""
    model: str = ""
    cached: bool = False
    total_tokens: int = 0
    elapsed_ms: int = 0
    blocking: bool = False
    diff: str = field(default="", repr=False)
    pr_ref: dict[str, Any] | None = None
    incremental_from: str = ""
    # Files dropped before the model saw them (lockfiles, generated code,
    # ship-report.json, .oneportrc ignore_paths). Surfaced because "0 issues"
    # and "nothing was reviewed" look identical otherwise — and when a commit
    # touches ONLY ignored files, an unreported skip renders as a clean pass.
    skipped_paths: list[str] = field(default_factory=list)

    @property
    def reviewed_nothing(self) -> bool:
        """True when every file in the diff was skipped — a vacuous 'clean'."""
        return bool(self.skipped_paths) and not self.issues and not self.diff.strip()

    def filter(self, min_severity: Severity | str) -> "ReviewResult":
        """Return a new ReviewResult containing only issues at or above min_severity.

        `blocking`, `diff`, and `pr_ref` are carried over from the source result
        rather than recomputed/dropped — `blocking` must reflect the full issue
        set regardless of the display filter, and `diff`/`pr_ref` are needed to
        post a review afterwards even if the displayed issue list was filtered.
        """
        if isinstance(min_severity, str):
            min_severity = Severity(min_severity)
        filtered = [i for i in self.issues if i.severity >= min_severity]
        return ReviewResult(
            issues=filtered,
            target=self.target,
            model=self.model,
            cached=self.cached,
            total_tokens=self.total_tokens,
            elapsed_ms=self.elapsed_ms,
            blocking=self.blocking,
            diff=self.diff,
            pr_ref=self.pr_ref,
            incremental_from=self.incremental_from,
            skipped_paths=self.skipped_paths,
        )

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def has_blocking_issues(self) -> bool:
        """True if the full (unfiltered) issue set contained an error/critical finding.

        Always reflects `blocking`, not `self.issues` — see the class docstring for why
        this must not be recomputed from a possibly display-filtered issue list.
        """
        return self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "model": self.model,
            "cached": self.cached,
            "total_tokens": self.total_tokens,
            "elapsed_ms": self.elapsed_ms,
            "blocking": self.blocking,
            "summary": {
                "total": len(self.issues),
                "critical": len(self.critical),
                "error": len(self.errors),
                "warning": len(self.warnings),
                "info": len([i for i in self.issues if i.severity == Severity.INFO]),
                "waived": len([i for i in self.issues if i.waived]),
            },
            "issues": [i.to_dict() for i in self.issues],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def __len__(self) -> int:
        return len(self.issues)

    def __bool__(self) -> bool:
        return not self.has_blocking_issues
