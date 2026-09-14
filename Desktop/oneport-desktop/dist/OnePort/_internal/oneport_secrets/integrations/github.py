"""
Minimal GitHub REST integration for `--post`: fetch a PR's diff and upsert a
sticky scan-report comment (found and edited in place via a hidden marker), plus
optional inline review comments anchored to changed lines.

Redaction happens in the formatter — this layer only moves bytes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from oneport_secrets.exceptions import IntegrationError

GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

_PR_URL_RE = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = _PR_URL_RE.match(url)
    if not m:
        raise IntegrationError(f"Unrecognised GitHub PR URL: {url}")
    return m.group("owner"), m.group("repo"), int(m.group("number"))


@dataclass
class PullRequest:
    owner: str
    repo: str
    number: int
    diff: str = ""
    head_sha: str = ""


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-secrets/1.0",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    def get_pr(self, owner: str, repo: str, number: int) -> PullRequest:
        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta = client.get(f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}")
            self._raise_for_status(meta, f"PR #{number}")
            diff = client.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}",
                headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
            )
            self._raise_for_status(diff, f"PR #{number} diff")
        return PullRequest(
            owner=owner, repo=repo, number=number,
            diff=diff.text, head_sha=meta.json().get("head", {}).get("sha", ""),
        )

    def upsert_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str, marker: str
    ) -> dict:
        """Create-or-update a sticky comment identified by a hidden HTML marker."""
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")
        base = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            existing_id: int | None = None
            resp = client.get(f"{base}/issues/{issue_number}/comments", params={"per_page": 100})
            self._raise_for_status(resp, f"comments on #{issue_number}")
            for comment in resp.json():
                if marker in (comment.get("body") or ""):
                    existing_id = comment["id"]
                    break
            if existing_id is not None:
                resp = client.patch(f"{base}/issues/comments/{existing_id}", json={"body": body})
            else:
                resp = client.post(f"{base}/issues/{issue_number}/comments", json={"body": body})
            self._raise_for_status(resp, f"upsert comment on #{issue_number}")
            return resp.json()

    def post_review(
        self, owner: str, repo: str, pr_number: int, body: str, comments: list[dict]
    ) -> dict:
        """Submit a PR review with inline comments (event=COMMENT)."""
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR reviews.")
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        payload: dict = {"body": body, "event": "COMMENT"}
        if comments:
            payload["comments"] = comments
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.post(url, json=payload)
            self._raise_for_status(resp, f"post review on #{pr_number}")
            return resp.json()

    def _raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
        if resp.status_code == 401:
            raise IntegrationError(f"GitHub auth failed for {context}. Check GITHUB_TOKEN.", 401)
        if resp.status_code == 404:
            raise IntegrationError(f"GitHub resource not found: {context}.", 404)
        if resp.status_code == 429:
            raise IntegrationError("GitHub API rate limit exceeded.", 429)
        if not resp.is_success:
            raise IntegrationError(
                f"GitHub API error for {context}: {resp.status_code} {resp.text[:200]}",
                resp.status_code,
            )
