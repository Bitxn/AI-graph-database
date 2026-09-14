"""
GitHub integration — read a PR's changed files at its base and head revisions,
and upsert the sticky cost comment. Read-only except for the one comment post.

Public repos work without a token (rate-limited); private repos and posting a
comment need GITHUB_TOKEN. GitHub Enterprise: set GITHUB_API_URL.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field

import httpx

from costwatch.exceptions import IntegrationError

GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

_PR_URL_RE = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, number)."""
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
    base_sha: str
    head_sha: str
    changed_files: list[str] = field(default_factory=list)


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-costwatch/0.1",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    def get_pr(self, url: str) -> PullRequest:
        owner, repo, number = parse_pr_url(url)
        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta = self._get(client, f"/repos/{owner}/{repo}/pulls/{number}", f"PR #{number}")
            files = self._get_paginated(
                client, f"/repos/{owner}/{repo}/pulls/{number}/files", f"files on #{number}"
            )
        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            title=meta.get("title", ""),
            base_sha=meta.get("base", {}).get("sha", ""),
            head_sha=meta.get("head", {}).get("sha", ""),
            changed_files=[f.get("filename", "") for f in files if f.get("filename")],
        )

    def get_file(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        """Fetch a file's decoded content at `ref`, or None if absent at that ref."""
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref},
            )
            if resp.status_code == 404:
                return None
            self._raise_for_status(resp, f"contents of {path}@{ref[:7]}")
            data = resp.json()
            if data.get("encoding") != "base64":
                return data.get("content", "")
            return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")

    def upsert_issue_comment(
        self, owner: str, repo: str, number: int, body: str, marker: str
    ) -> dict:
        """Create-or-update a sticky comment identified by a hidden marker."""
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")
        base = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            existing_id: int | None = None
            resp = client.get(f"{base}/issues/{number}/comments", params={"per_page": 100})
            self._raise_for_status(resp, f"comments on #{number}")
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
                    f"{base}/issues/{number}/comments", json={"body": body}
                )
            self._raise_for_status(resp, f"upsert comment on #{number}")
            return resp.json()

    # ── Private ────────────────────────────────────────────────────────────────

    def _get(self, client: httpx.Client, path: str, context: str) -> dict:
        resp = client.get(f"{GITHUB_API_URL}{path}")
        self._raise_for_status(resp, context)
        return resp.json()

    def _get_paginated(self, client: httpx.Client, path: str, context: str) -> list[dict]:
        resp = client.get(f"{GITHUB_API_URL}{path}", params={"per_page": 100})
        self._raise_for_status(resp, context)
        return resp.json()

    def _raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
        if resp.status_code == 401:
            raise IntegrationError(
                f"GitHub authentication failed for {context}. Check GITHUB_TOKEN.", 401
            )
        if resp.status_code == 404:
            raise IntegrationError(
                f"GitHub resource not found: {context}. Check the URL and token access.", 404
            )
        if resp.status_code == 429:
            raise IntegrationError(
                "GitHub API rate limit exceeded. Provide a GITHUB_TOKEN.", 429
            )
        if not resp.is_success:
            raise IntegrationError(
                f"GitHub API error for {context}: {resp.status_code} {resp.text[:200]}",
                resp.status_code,
            )
