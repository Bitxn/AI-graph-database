"""
GitHub integration — trimmed to what depcheck needs, same wire behaviour as
oneport-review/integrations/github.py.

  - parse a PR URL
  - fetch the PR diff (to know which manifest lines accept inline comments)
  - post a review with inline, per-line comments
  - upsert a marker-identified sticky summary comment
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from oneport_depcheck.exceptions import IntegrationError

GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

_PR_URL_RE = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = _PR_URL_RE.match(url)
    if not match:
        raise IntegrationError(f"Unrecognised GitHub PR URL: {url}")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


@dataclass
class PullRequest:
    owner: str
    repo: str
    number: int
    title: str
    diff: str
    head_sha: str = ""


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-depcheck/1.0",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    def get_pr(self, url: str) -> PullRequest:
        owner, repo, number = parse_pr_url(url)
        base = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta_resp = client.get(base)
            self._raise_for_status(meta_resp, context=f"PR #{number}")
            meta = meta_resp.json()

            diff_resp = client.get(
                base, headers={**self._headers, "Accept": "application/vnd.github.v3.diff"}
            )
            self._raise_for_status(diff_resp, context=f"PR #{number} diff")

        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            title=meta.get("title", ""),
            diff=diff_resp.text,
            head_sha=(meta.get("head") or {}).get("sha", ""),
        )

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """Submit a PR review with inline per-line comments in a single call.

        Each comment's `line` must be part of the diff (new-file numbering) or
        GitHub returns 422 — callers filter with diff_utils.commentable_lines.
        """
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR reviews.")
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        payload: dict = {"body": body, "event": event}
        if comments:
            payload["comments"] = comments
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.post(url, json=payload)
            if resp.status_code not in (200, 201):
                raise IntegrationError(
                    f"Failed to post PR review: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            return resp.json()

    def upsert_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        marker: str,
    ) -> dict:
        """Create-or-update the sticky comment identified by `marker`."""
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")
        base = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            existing_id: int | None = None
            resp = client.get(
                f"{base}/issues/{issue_number}/comments", params={"per_page": 100}
            )
            self._raise_for_status(resp, context=f"comments on #{issue_number}")
            for comment in resp.json():
                if marker in (comment.get("body") or ""):
                    existing_id = comment["id"]
                    break

            if existing_id is not None:
                resp = client.patch(
                    f"{base}/issues/comments/{existing_id}", json={"body": body}
                )
            else:
                resp = client.post(
                    f"{base}/issues/{issue_number}/comments", json={"body": body}
                )
            self._raise_for_status(resp, context=f"upsert comment on #{issue_number}")
            return resp.json()

    def _raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
        if resp.status_code == 401:
            raise IntegrationError(
                f"GitHub authentication failed for {context}. Check GITHUB_TOKEN.",
                status_code=401,
            )
        if resp.status_code == 404:
            raise IntegrationError(
                f"GitHub resource not found: {context}. "
                "Check the URL and that the token has repo access.",
                status_code=404,
            )
        if resp.status_code == 429:
            raise IntegrationError(
                "GitHub API rate limit exceeded. Provide a GITHUB_TOKEN.",
                status_code=429,
            )
        if not resp.is_success:
            raise IntegrationError(
                f"GitHub API error for {context}: {resp.status_code} {resp.text[:200]}",
                status_code=resp.status_code,
            )
