"""
GitHub integration — fetches PR diffs via the GitHub REST API v3.

Supports:
  - Public repos (no token required, rate-limited to 60 req/hr)
  - Private repos (requires GITHUB_TOKEN with repo scope)
  - GitHub Enterprise (set GITHUB_API_URL env var)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from oneport.exceptions import IntegrationError


GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")

_PR_URL_RE = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, number). Raises IntegrationError if malformed."""
    match = _PR_URL_RE.match(url)
    if not match:
        raise IntegrationError(f"Unrecognised GitHub PR URL: {url}")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    diff: str
    base_branch: str
    head_branch: str
    head_sha: str = ""


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-review/1.0",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    def get_pr(self, url: str) -> PullRequest:
        """
        Fetch a pull request and its unified diff from a GitHub PR URL.

        Args:
            url: Full GitHub PR URL, e.g. https://github.com/org/repo/pull/42

        Returns:
            PullRequest with title, body, and diff populated.

        Raises:
            IntegrationError: If the PR is not found or the token is invalid.
        """
        owner, repo, number = parse_pr_url(url)

        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta = self._fetch_pr_meta(client, owner, repo, number)
            diff = self._fetch_diff(client, owner, repo, number)

        return PullRequest(
            number=number,
            title=meta.get("title", ""),
            body=meta.get("body", "") or "",
            diff=diff,
            base_branch=meta.get("base", {}).get("ref", ""),
            head_branch=meta.get("head", {}).get("ref", ""),
            head_sha=meta.get("head", {}).get("sha", ""),
        )

    def list_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """All submitted reviews on a PR, chronological (used to find the last
        Oneport review's reviewed-SHA marker for incremental reviews)."""
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url, params={"per_page": 100})
            self._raise_for_status(resp, context=f"reviews on #{pr_number}")
            return resp.json()

    def get_compare_diff(self, owner: str, repo: str, base: str, head: str) -> str:
        """Unified diff of base...head (the commits pushed since `base`)."""
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/compare/{base}...{head}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(
                url, headers={**self._headers, "Accept": "application/vnd.github.v3.diff"}
            )
            self._raise_for_status(resp, context=f"compare {base[:7]}...{head[:7]}")
            return resp.text

    def post_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> dict:
        """Post a single top-level issue comment on a PR (not anchored to a line).

        Prefer `post_review` for review findings — it anchors each issue to its
        file/line as an inline comment, which is what reviewers actually expect.
        """
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.post(url, json={"body": body})
            if resp.status_code not in (200, 201):
                raise IntegrationError(
                    f"Failed to post PR comment: {resp.status_code} {resp.text}",
                    status_code=resp.status_code,
                )
            return resp.json()

    def list_review_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """All inline review comments on a PR (used to reconstruct a thread)."""
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url, params={"per_page": 100})
            self._raise_for_status(resp, context=f"review comments on #{pr_number}")
            return resp.json()

    def reply_to_review_comment(
        self, owner: str, repo: str, pr_number: int, comment_id: int, body: str
    ) -> dict:
        """Post a threaded reply under an existing inline review comment."""
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")
        url = (
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
            f"/comments/{comment_id}/replies"
        )
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.post(url, json={"body": body})
            if resp.status_code not in (200, 201):
                raise IntegrationError(
                    f"Failed to post threaded reply: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            return resp.json()

    def get_file(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[str, str] | None:
        """Fetch a file's decoded content and blob sha at `ref`, or None if absent."""
        import base64
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url, params={"ref": ref})
            if resp.status_code == 404:
                return None
            self._raise_for_status(resp, context=f"contents of {path}@{ref}")
            data = resp.json()
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            return content, data["sha"]

    def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        sha: str | None = None,
    ) -> dict:
        """Create or update a file on `branch` via the contents API.

        `sha` must be the current blob sha when updating an existing file;
        omit it when creating a new one.
        """
        import base64
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to commit files.")
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}"
        payload: dict = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.put(url, json=payload)
            if resp.status_code not in (200, 201):
                raise IntegrationError(
                    f"Failed to commit {path}: {resp.status_code} {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            return resp.json()

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """
        Submit a full PR review with inline, per-line comments in a single call.

        POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews

        Args:
            body: Overall review summary shown at the top of the review (markdown).
            comments: [{"path": "src/app.py", "line": 42, "body": "..."}, ...].
                Each `line` MUST be a line that's part of the diff (new-file
                numbering) or GitHub returns 422 "line must be part of the diff".
                Use `oneport.diff_utils.commentable_lines` to filter beforehand.
            event: "COMMENT" | "REQUEST_CHANGES" | "APPROVE". Defaults to COMMENT
                since REQUEST_CHANGES/APPROVE on your own PR (e.g. a bot opening
                and reviewing the same PR) is rejected by GitHub with a 422; the
                CLI's own exit code (not the review event) is what should gate CI.

        Returns:
            The created review object (includes its id and html_url).
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
        """
        Create-or-update a "sticky" comment identified by a hidden HTML marker.

        Searches the issue's comments for one containing `marker`; if found,
        edits that comment in place (PATCH), otherwise creates a new one (POST).
        This keeps one always-current comment instead of a new comment per push.

        Returns the created/updated comment object.
        """
        if not self.token:
            raise IntegrationError("GITHUB_TOKEN is required to post PR comments.")

        base = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            existing_id: int | None = None
            # One page of 100 is plenty — the sticky comment is nearly always
            # among the earliest, and worst case we post a fresh one.
            resp = client.get(
                f"{base}/issues/{issue_number}/comments",
                params={"per_page": 100},
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

    # ── Private ────────────────────────────────────────────────────────────────

    def _fetch_pr_meta(
        self, client: httpx.Client, owner: str, repo: str, number: int
    ) -> dict:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        resp = client.get(url)
        self._raise_for_status(resp, context=f"PR #{number}")
        return resp.json()

    def _fetch_diff(
        self, client: httpx.Client, owner: str, repo: str, number: int
    ) -> str:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        resp = client.get(url, headers={**self._headers, "Accept": "application/vnd.github.v3.diff"})
        self._raise_for_status(resp, context=f"PR #{number} diff")
        return resp.text

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
