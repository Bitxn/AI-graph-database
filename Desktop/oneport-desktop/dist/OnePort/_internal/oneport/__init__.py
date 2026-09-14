"""
Oneport Review — AI-powered code review for teams without a senior engineer.

Public API:
    review(target, **kwargs) -> ReviewResult
"""

from oneport.reviewer import Reviewer
from oneport.result import ReviewResult, Issue, Severity

__version__ = "1.2.0"
__all__ = ["review", "ReviewResult", "Issue", "Severity"]


def review(
    target: str,
    *,
    format: str = "inline",
    min_severity: str = "warning",
    config_path: str | None = None,
) -> ReviewResult:
    """
    Review a file, PR URL, or diff and return a ReviewResult.

    Args:
        target: File path, GitHub/GitLab/Bitbucket PR URL, or "--staged" / "--head".
        format: Output format — "inline" | "json" | "github" | "sarif".
        min_severity: Minimum severity to include — "info" | "warning" | "error" | "critical".
        config_path: Path to .oneportrc file. Auto-discovered if None.

    Returns:
        ReviewResult containing all detected issues.

    Example:
        >>> from oneport import review
        >>> result = review("src/auth.py")
        >>> for issue in result.issues:
        ...     print(f"[{issue.severity}] Line {issue.line}: {issue.message}")
    """
    reviewer = Reviewer(config_path=config_path)
    return reviewer.review(target, min_severity=min_severity)
