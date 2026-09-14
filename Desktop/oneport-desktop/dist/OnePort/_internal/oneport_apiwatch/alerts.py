"""Alerting to the customer's own channels — never through an Oneport server.

Two channels, both driven by the customer's own credentials from the environment:

  * slack        → POST to $SLACK_WEBHOOK_URL (an incoming-webhook the customer owns)
  * github-issue → open/update a deduped issue in $GITHUB_REPOSITORY using $GITHUB_TOKEN
                   (both set automatically inside GitHub Actions)

Alerts fire only on failure. A delivery failure raises AlertError, which the CLI
reports without masking the underlying check exit code.
"""

from __future__ import annotations

import json
import os

import httpx

from oneport_apiwatch.exceptions import AlertError
from oneport_apiwatch.result import CheckResult, Report

# Hidden marker so the GitHub-issue channel updates one issue instead of opening
# a new one every run.
_ISSUE_MARKER = "<!-- oneport-apiwatch:alert -->"
_ISSUE_TITLE = "🔴 oneport-apiwatch: API check(s) failing"


# ── Message building ──────────────────────────────────────────────────────────

def _check_line(check: CheckResult) -> str:
    status = check.status_code if check.status_code is not None else "no response"
    line = (
        f"*{check.name}* — {check.method} {check.url}\n"
        f"  status {status}, {check.latency_ms:.0f}ms"
    )
    for reason in check.failures:
        line += f"\n  • {reason}"
    if check.diagnosis and check.diagnosis.summary:
        line += f"\n  🧠 {check.diagnosis.summary}"
        if check.diagnosis.suggested_action:
            line += f"\n  → first action: {check.diagnosis.suggested_action}"
    return line


def build_slack_payload(report: Report) -> dict:
    summary = report.summary
    header = (
        f":red_circle: oneport-apiwatch — "
        f"{summary['failed']}/{summary['total']} check(s) failing"
    )
    body = "\n\n".join(_check_line(c) for c in report.failed)
    return {"text": f"{header}\n\n{body}"}


def build_issue_body(report: Report) -> str:
    summary = report.summary
    lines = [
        _ISSUE_MARKER,
        f"**{summary['failed']}/{summary['total']} check(s) failing** "
        f"as of {report.generated_at}.",
        "",
    ]
    for check in report.failed:
        status = check.status_code if check.status_code is not None else "no response"
        lines.append(f"### {check.name}")
        lines.append(f"`{check.method} {check.url}` — status {status}, {check.latency_ms:.0f}ms")
        for reason in check.failures:
            lines.append(f"- {reason}")
        if check.diagnosis and check.diagnosis.summary:
            lines.append(f"\n**AI diagnosis:** {check.diagnosis.summary}")
            for i, cause in enumerate(check.diagnosis.likely_causes, 1):
                lines.append(f"{i}. {cause}")
            if check.diagnosis.suggested_action:
                lines.append(f"\n_First action: {check.diagnosis.suggested_action}_")
        lines.append("")
    return "\n".join(lines)


# ── Channels ──────────────────────────────────────────────────────────────────

def send_slack(report: Report, webhook_url: str | None = None) -> None:
    """POST a failure summary to a Slack incoming webhook."""
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        raise AlertError(
            "Slack alert requested but SLACK_WEBHOOK_URL is not set. Create an "
            "incoming webhook in your own Slack workspace and export it."
        )
    payload = build_slack_payload(report)
    try:
        resp = httpx.post(url, json=payload, timeout=15)
    except httpx.HTTPError as exc:
        raise AlertError(f"Slack webhook request failed: {exc}") from exc
    if not resp.is_success:
        raise AlertError(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")


def send_github_issue(
    report: Report,
    repo: str | None = None,
    token: str | None = None,
) -> str:
    """Open (or update) a single deduped GitHub issue describing the failures.

    Returns the issue's html_url. Uses $GITHUB_REPOSITORY and $GITHUB_TOKEN,
    which GitHub Actions sets automatically.
    """
    repo = repo or os.getenv("GITHUB_REPOSITORY", "")
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not repo or not token:
        raise AlertError(
            "GitHub issue alert requires GITHUB_REPOSITORY and GITHUB_TOKEN "
            "(both set automatically inside GitHub Actions)."
        )

    api = f"https://api.github.com/repos/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = build_issue_body(report)

    try:
        existing = _find_open_alert_issue(api, headers)
        if existing is not None:
            number = existing["number"]
            # Refresh the body and add a comment so subscribers get notified.
            resp = httpx.patch(
                f"{api}/issues/{number}", headers=headers, json={"body": body}, timeout=20
            )
            _raise_for_github(resp, "update issue")
            comment = httpx.post(
                f"{api}/issues/{number}/comments",
                headers=headers,
                json={"body": f"Still failing as of {report.generated_at}."},
                timeout=20,
            )
            _raise_for_github(comment, "comment on issue")
            return existing.get("html_url", "")

        resp = httpx.post(
            f"{api}/issues",
            headers=headers,
            json={"title": _ISSUE_TITLE, "body": body},
            timeout=20,
        )
        _raise_for_github(resp, "create issue")
        return resp.json().get("html_url", "")
    except httpx.HTTPError as exc:
        raise AlertError(f"GitHub API request failed: {exc}") from exc


def _find_open_alert_issue(api: str, headers: dict) -> dict | None:
    resp = httpx.get(
        f"{api}/issues",
        headers=headers,
        params={"state": "open", "per_page": 100},
        timeout=20,
    )
    _raise_for_github(resp, "list issues")
    for issue in resp.json():
        # Skip PRs (they show up in the issues list) and match our marker.
        if "pull_request" in issue:
            continue
        if _ISSUE_MARKER in (issue.get("body") or ""):
            return issue
    return None


def _raise_for_github(resp: httpx.Response, action: str) -> None:
    if not resp.is_success:
        detail = ""
        try:
            detail = json.loads(resp.text).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            detail = resp.text[:200]
        raise AlertError(f"GitHub {action} failed ({resp.status_code}): {detail}")


def send_slack_recovery(report: Report, webhook_url: str | None = None) -> None:
    """Post a recovery notice when previously-down checks are healthy again."""
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        raise AlertError("Slack recovery alert requested but SLACK_WEBHOOK_URL is not set.")
    names = ", ".join(c.name for c in report.recovered) or "all checks"
    text = f":large_green_circle: oneport-apiwatch — recovered: {names}"
    try:
        resp = httpx.post(url, json={"text": text}, timeout=15)
    except httpx.HTTPError as exc:
        raise AlertError(f"Slack webhook request failed: {exc}") from exc
    if not resp.is_success:
        raise AlertError(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")


def close_github_issue(report: Report, repo: str | None = None, token: str | None = None) -> str:
    """On recovery, comment on and close the open alert issue (if any)."""
    repo = repo or os.getenv("GITHUB_REPOSITORY", "")
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not repo or not token:
        raise AlertError("GitHub recovery requires GITHUB_REPOSITORY and GITHUB_TOKEN.")
    api = f"https://api.github.com/repos/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        issue = _find_open_alert_issue(api, headers)
        if issue is None:
            return ""
        number = issue["number"]
        names = ", ".join(c.name for c in report.recovered) or "all checks"
        httpx.post(f"{api}/issues/{number}/comments", headers=headers,
                   json={"body": f"✅ Recovered: {names} (as of {report.generated_at})."}, timeout=20)
        resp = httpx.patch(f"{api}/issues/{number}", headers=headers,
                           json={"state": "closed"}, timeout=20)
        _raise_for_github(resp, "close issue")
        return issue.get("html_url", "")
    except httpx.HTTPError as exc:
        raise AlertError(f"GitHub API request failed: {exc}") from exc


def dispatch(report: Report, channel: str) -> str:
    """Send `report` to the named channel. Returns a short status string.

    Chooses the failure path when anything is down (tripped or failed this run),
    otherwise the recovery path — so a run that only saw endpoints come back up
    posts a recovery, not a phantom "0 failing" alert.
    """
    down = report.tripped or report.failed
    if channel == "slack":
        if down:
            send_slack(report)
            return "Slack webhook notified"
        send_slack_recovery(report)
        return "Slack recovery notified"
    if channel == "github-issue":
        if down:
            url = send_github_issue(report)
            return f"GitHub issue: {url}" if url else "GitHub issue updated"
        url = close_github_issue(report)
        return f"GitHub issue closed: {url}" if url else "no open issue to close"
    raise AlertError(f"unknown alert channel: {channel}")
