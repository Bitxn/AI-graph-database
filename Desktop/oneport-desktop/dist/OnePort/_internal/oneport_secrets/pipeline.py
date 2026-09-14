"""
Orchestration: deterministic scan → LLM triage → remediation, plus optional
GitHub posting. Keeps the CLI thin and makes the whole flow unit-testable with a
mocked model.
"""

from __future__ import annotations

import os
import re

from oneport_secrets.config import Config
from oneport_secrets.exceptions import IntegrationError, OneportError
from oneport_secrets.guidelines import load_guidelines
from oneport_secrets.remediation import annotate
from oneport_secrets.result import ScanResult, Verdict
from oneport_secrets.scanner import scan
from oneport_secrets.triage import Triager

# Paths that hold test fixtures. A generic/entropy/JWT match here is example
# data, not a leak — real provider keys (AWS/GitHub/Stripe) still block anywhere.
_TEST_PATH_RE = re.compile(
    # `testdata` is Go's built-in fixtures dir — the `go` tool ignores any
    # directory named exactly that, so it's THE standard home for sample logs,
    # golden files, and trust bundles. cli/cli's testdata/log-*.txt flooded the
    # report until it was recognised here alongside tests/fixtures/etc.
    r"(^|/)(tests?|testdata|__tests__|__mocks__|mocks?|fixtures?|specs?|e2e|examples?)(/|$)"
    # any *.test.<ext> / *.spec.<ext> — includes keys.test.json (JWK test keys),
    # not just .test.ts/.js
    r"|[._-](test|spec)\.[a-z0-9]+$"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go|rb)$",
    re.IGNORECASE,
)
# Detectors that fire on shape, not on a specific provider's key format. Only
# these are downgraded in test paths; a real AKIA.../ghp_... still blocks.
_GENERIC_DETECTORS = {"generic-secret-assignment", "high-entropy-string", "jwt"}


def _line_is_doc_comment(line_text: str) -> bool:
    """A JSDoc/JavaDoc continuation or block-opener line — documentation, where
    `password: 'ahotproject'` is an illustration, not a live credential. `*`-led
    lines never occur in real code, so this is a high-precision signal."""
    s = (line_text or "").lstrip()
    return s.startswith("*") or s.startswith("/**")


# A real leaked credential is SPARSE — one or two per file, in source or config.
# When a single file yields MANY entropy-only hits it is a data blob, not a leak:
# an embedded key/cert bundle (TUF root.json, sigstore trust bundles), a minified
# asset, a sample log, a golden fixture. Flagging every token there floods the
# report and trains users to ignore a secrets gate entirely. Only the lowest-
# confidence entropy fallback is suppressed here — provider-specific detectors
# (AWS/GitHub/Stripe/PEM/…) still fire on every line, so a real key hidden among
# the noise is still caught. Deterministic, so it holds while triage is down.
_BULK_ENTROPY_THRESHOLD = 8


def _classify_bulk_entropy(result: ScanResult) -> None:
    """Suppress entropy-only hits in files that are clearly bulk high-entropy
    DATA (many hits in one file), not sparse leaked credentials."""
    from collections import Counter

    counts = Counter(
        f.path for f in result.findings
        if f.verdict == Verdict.UNREVIEWED and f.detector_id == "high-entropy-string"
    )
    bulk = {p for p, n in counts.items() if n >= _BULK_ENTROPY_THRESHOLD}
    for f in result.findings:
        if (f.verdict == Verdict.UNREVIEWED
                and f.detector_id == "high-entropy-string"
                and f.path in bulk):
            f.verdict = Verdict.FALSE_POSITIVE
            f.reason = ("bulk high-entropy data file (embedded keys/certs/logs/"
                        "fixtures — real leaks are sparse); provider detectors still active")


def _classify_test_fixtures(result: ScanResult) -> None:
    """Mark generic matches that are clearly examples as FALSE_POSITIVE.

    Two example sources dominate real repos, and both block while triage is
    rate-limited: test files (hono's *.test.ts produced 227 'secrets') and doc
    comments (JSDoc `@example` blocks). Provider-specific keys (AWS/GitHub/PEM)
    are deliberately excluded — those never legitimately appear as examples.
    """
    for f in result.findings:
        if f.verdict != Verdict.UNREVIEWED or f.detector_id not in _GENERIC_DETECTORS:
            continue
        if _TEST_PATH_RE.search(f.path.replace("\\", "/")):
            f.verdict = Verdict.FALSE_POSITIVE
            f.reason = "test fixture — generic match in a test file"
        elif _line_is_doc_comment(f.line_text):
            f.verdict = Verdict.FALSE_POSITIVE
            f.reason = "documentation example — generic match in a doc comment"


def run_scan(path: str, mode: str, config: Config, triage: bool = True,
             on_commit=None) -> ScanResult:
    """Full scan pipeline. Detection always runs; triage runs when a key exists.

    Remediation is attached only to findings that end up blocking (real or
    untriaged), so a report isn't cluttered with rotation steps for placeholders.
    """
    guidelines = load_guidelines(config.guidelines_path)
    ignore = list(config.ignore_paths) + guidelines.ignore_paths

    result = scan(
        path=path,
        mode=mode,
        detectors=guidelines.custom_detectors or None,
        ignore=ignore,
        entropy_enabled=config.entropy,
        on_commit=on_commit,
    )

    # Deterministic pre-triage: generic matches inside test files are fixtures,
    # not leaks. This runs BEFORE triage so it holds even when the model is
    # rate-limited — the exact case where hono's 227 test-fixture 'secrets' would
    # otherwise all stay UNREVIEWED and block a clean repo.
    _classify_test_fixtures(result)
    # Then collapse bulk-data entropy floods (embedded key/cert bundles, sample
    # logs) that survived the test-path pass — e.g. a TUF root.json full of
    # public-key material sitting outside any test dir.
    _classify_bulk_entropy(result)

    if triage and config.has_key and result.findings:
        try:
            Triager(config).triage(result)
        except OneportError as exc:
            # Triage is an OPTIONAL refinement layer: it downgrades placeholders to
            # false-positive. Detection already found these deterministically, so a
            # transport failure (rate limit / no tokens / auth) must not discard
            # them — that turns a rate-limited scan into "PASS - No secrets
            # detected", the most dangerous output a secrets gate can produce.
            # _parse_verdicts is already fail-safe; this closes the same gap one
            # layer down. Findings stay UNREVIEWED, and UNREVIEWED blocks.
            result.triage_error = str(exc)
            result.triaged = False

    annotate(result.blocking)
    return result


def post_to_github(result: ScanResult, config: Config, pr_url: str | None) -> dict:
    """Upsert the sticky report comment and post inline comments on changed lines.

    PR context comes from `pr_url` if given, else from GitHub Actions env
    (GITHUB_REPOSITORY + PR number derived from the event/ref).
    """
    from oneport_secrets.formatters.github_fmt import (
        build_comment,
        build_inline_comments,
        commentable_lines,
    )
    from oneport_secrets.integrations.github import GitHubIntegration, parse_pr_url
    from oneport_secrets.markers import SECRETS_MARKER

    if pr_url:
        owner, repo, number = parse_pr_url(pr_url)
    else:
        owner, repo, number = _pr_from_env()

    gh = GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))

    # Inline comments require the PR diff to know which lines GitHub will accept.
    inline: list[dict] = []
    try:
        pr = gh.get_pr(owner, repo, number)
        inline = build_inline_comments(result, commentable_lines(pr.diff))
    except IntegrationError:
        inline = []  # fall back to the sticky comment only

    body = build_comment(result)
    if inline:
        try:
            return gh.post_review(owner, repo, number, body, inline)
        except IntegrationError:
            pass  # fall through to sticky comment
    return gh.upsert_issue_comment(owner, repo, number, body, SECRETS_MARKER)


def _pr_from_env() -> tuple[str, str, int]:
    repo_full = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo_full:
        raise OneportError(
            "--post outside a PR URL needs GITHUB_REPOSITORY and a PR number "
            "(pass --pr <url>, or run in GitHub Actions on a pull_request event)."
        )
    owner, repo = repo_full.split("/", 1)

    # PR number from the ref (refs/pull/123/merge) or the event payload.
    ref = os.getenv("GITHUB_REF", "")
    if "/pull/" in ref:
        try:
            return owner, repo, int(ref.split("/pull/")[1].split("/")[0])
        except (IndexError, ValueError):
            pass
    number = os.getenv("PR_NUMBER") or os.getenv("GITHUB_PR_NUMBER")
    if number and number.isdigit():
        return owner, repo, int(number)
    raise OneportError("Could not determine the PR number for --post. Pass --pr <url>.")
