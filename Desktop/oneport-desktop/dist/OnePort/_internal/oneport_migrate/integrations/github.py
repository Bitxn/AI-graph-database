"""
GitHub integration — fetches PR migration files and posts inline reviews.

Supports:
  - Public repos (no token required, rate-limited to 60 req/hr)
  - Private repos (requires GITHUB_TOKEN with repo scope)
  - GitHub Enterprise (set GITHUB_API_URL env var)
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field

import httpx

from oneport_migrate.exceptions import IntegrationError


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
class PullRequestFiles:
    """The subset of a PR that matters to the migration gate."""

    number: int
    head_sha: str
    diff: str
    # [(path, content), ...] for changed files that look like migrations.
    migration_files: list[tuple[str, str]] = field(default_factory=list)


def looks_like_migration_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if p.endswith(".sql"):
        return True
    if not p.endswith(".py"):
        return False
    return "/migrations/" in p or p.startswith("migrations/") or "/versions/" in p


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-migrate/0.1",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    # ── Fetching ───────────────────────────────────────────────────────────────

    def get_pr_migrations(self, url: str) -> PullRequestFiles:
        """Fetch a PR's diff plus the current content of every changed file
        that looks like a migration (at the PR head SHA)."""
        owner, repo, number = parse_pr_url(url)

        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta = self._get_json(client, f"/repos/{owner}/{repo}/pulls/{number}",
                                  context=f"PR #{number}")
            head_sha = meta.get("head", {}).get("sha", "")

            resp = client.get(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}",
                headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
            )
            self._raise_for_status(resp, context=f"PR #{number} diff")
            diff = resp.text

            files = self._get_json(client, f"/repos/{owner}/{repo}/pulls/{number}/files",
                                   context=f"PR #{number} files",
                                   params={"per_page": 100})

            migrations: list[tuple[str, str]] = []
            for f in files:
                path = f.get("filename", "")
                if f.get("status") == "removed":
                    continue  # deleted migrations can't be applied
                if not looks_like_migration_path(path):
                    continue
                content = self._get_file_content(client, owner, repo, path, head_sha)
                if content is not None:
                    migrations.append((path, content))

        return PullRequestFiles(
            number=number, head_sha=head_sha, diff=diff, migration_files=migrations,
        )

    def _get_file_content(
        self, client: httpx.Client, owner: str, repo: str, path: str, ref: str
    ) -> str | None:
        resp = client.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        if resp.status_code == 404:
            return None
        self._raise_for_status(resp, context=f"contents of {path}@{ref[:7]}")
        data = resp.json()
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def list_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """All submitted reviews on a PR, chronological (used to find the last
        oneport-migrate reviewed-SHA marker)."""
        with httpx.Client(headers=self._headers, timeout=30) as client:
            return self._get_json(client, f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                                  context=f"reviews on #{pr_number}",
                                  params={"per_page": 100})

    # ── Posting ────────────────────────────────────────────────────────────────

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """Submit a full PR review with inline, per-line comments in one call.

        Each comment's `line` MUST be part of the diff (new-file numbering) or
        GitHub returns 422 — use diff_utils.commentable_lines to filter first.
        Event defaults to COMMENT: REQUEST_CHANGES on your own PR is rejected
        by GitHub, and the CLI exit code is what gates CI anyway.
        """
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR reviews.")

        payload: dict = {"body": body, "event": event}
        if comments:
            payload["comments"] = comments

        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.post(
                f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                json=payload,
            )
            if resp.status_code not in (200, 201):
                raise IntegrationError(
                    f"Failed to post PR review: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            return resp.json()

    # ── Private ────────────────────────────────────────────────────────────────

    def _get_json(self, client: httpx.Client, path: str, context: str,
                  params: dict | None = None):
        resp = client.get(f"{GITHUB_API_URL}{path}", params=params)
        self._raise_for_status(resp, context=context)
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
                "GitHub API rate limit exceeded. Provide a GITHUB_TOKEN to increase the limit.",
                status_code=429,
            )
        if not resp.is_success:
            raise IntegrationError(
                f"GitHub API error for {context}: {resp.status_code} {resp.text[:200]}",
                status_code=resp.status_code,
            )
