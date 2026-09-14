"""
Slack delivery — post a rendered standup to an incoming webhook.

A standup's natural home is a channel, not a terminal. `weekly --post $SLACK_URL`
in a cron job or CI drops the summary straight into Slack as Block Kit blocks
(headline + themed bullets + a stats context line). Dependency-free: the POST
goes through the stdlib, so nothing new is pulled into the install.

The message is built from the SAME grounded StandupReport the terminal renders,
so what lands in Slack has already had any fabricated references stripped.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from oneport_standup.exceptions import PostError
from oneport_standup.result import StandupReport

_MAX_BULLETS_PER_GROUP = 12


def _mrkdwn(text: str) -> str:
    """Escape the three characters Slack mrkdwn treats specially."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_blocks(report: StandupReport) -> list[dict]:
    """Render a StandupReport as Slack Block Kit blocks."""
    n = report.narrative
    title = n.headline.strip() if n and n.headline.strip() else (
        f"{report.mode.title()} — {report.range_label}")

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": title[:150], "emoji": True}},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                       "text": f"*{report.author_label}* · {report.range_label}"}]},
    ]

    if n and n.groups:
        for g in n.groups:
            bullets = "\n".join(f"• {_mrkdwn(b)}" for b in g.bullets[:_MAX_BULLETS_PER_GROUP])
            heading = f"*{_mrkdwn(g.title)}*\n" if g.title else ""
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": (heading + bullets)[:3000]}})
    else:
        # No narrative (offline / not logged in): fall back to commits by day, so
        # the Slack post is never empty and never claims a summary it doesn't have.
        for day, commits in report.by_day().items():
            lines = "\n".join(f"• {_mrkdwn(c.subject)}" for c in commits[:_MAX_BULLETS_PER_GROUP])
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": f"*{day}*\n{lines}"[:3000]}})

    ins, dele = report.net_lines
    stats = (f"{len(report.commits)} commit(s) · {report.files_touched} file(s) · "
             f"+{ins}/-{dele}")
    if report.tickets:
        stats += " · " + ", ".join(report.tickets[:10])
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": stats}]})
    return blocks


def post_to_slack(report: StandupReport, webhook_url: str, timeout: float = 10.0) -> None:
    """POST the report to a Slack incoming webhook. Raises PostError on failure."""
    if not webhook_url.startswith(("http://", "https://")):
        raise PostError(f"Not a valid Slack webhook URL: {webhook_url!r}")

    blocks = build_blocks(report)
    fallback = report.narrative.headline or f"{report.mode.title()} — {report.range_label}"
    payload = json.dumps({"text": fallback, "blocks": blocks}).encode("utf-8")

    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200 or body.strip() != "ok":
                raise PostError(f"Slack rejected the message (HTTP {resp.status}): {body[:200]}")
    except urllib.error.URLError as exc:
        raise PostError(f"Could not reach Slack webhook: {exc}") from exc
