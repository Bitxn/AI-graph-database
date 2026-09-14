"""
GitHub integration — PR file contents in, verdicts out.

Reading (public repos need no token; private need GITHUB_TOKEN with repo scope):
  - PR metadata (base/head SHAs) and the unified diff (for comment anchoring)
  - the changed-file list, and full file contents at the base and head SHAs

Writing (--post; requires GITHUB_TOKEN):
  - a sticky verdict-table comment, upserted in place via a hidden marker
  - an inline PR review with comments on the exact changed signatures,
    skipped when the head SHA was already checked (no duplicates on re-run)

GitHub Enterprise: set GITHUB_API_URL. Ported from oneport-review's
oneport/integrations/github.py.
"""

from __future__ import annotations

import base64
import os
import re

import httpx

from oneport_apidiff.exceptions import IntegrationError
from oneport_apidiff.sources import FilePair

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


class GitHubIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oneport-apidiff/0.1",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    # ── Reads ──────────────────────────────────────────────────────────────────

    def get_pr_meta(self, owner: str, repo: str, number: int) -> dict:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url)
            self._raise_for_status(resp, context=f"PR #{number}")
            return resp.json()

    def get_pr_diff(self, owner: str, repo: str, number: int) -> str:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(
                url, headers={**self._headers, "Accept": "application/vnd.github.v3.diff"}
            )
            self._raise_for_status(resp, context=f"PR #{number} diff")
            return resp.text

    def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict]:
        """All changed files on a PR (filename, status, previous_filename)."""
        files: list[dict] = []
        with httpx.Client(headers=self._headers, timeout=30) as client:
            for page in range(1, 4):  # 300 files is plenty for an API-surface check
                url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}/files"
                resp = client.get(url, params={"per_page": 100, "page": page})
                self._raise_for_status(resp, context=f"files of PR #{number}")
                batch = resp.json()
                files.extend(batch)
                if len(batch) < 100:
                    break
        return files

    def get_file_at(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        """Fetch a file's decoded content at `ref`, or None if absent."""
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url, params={"ref": ref})
            if resp.status_code == 404:
                return None
            self._raise_for_status(resp, context=f"contents of {path}@{ref}")
            data = resp.json()
            return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")

    def list_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues/{number}/comments"
        with httpx.Client(headers=self._headers, timeout=30) as client:
            resp = client.get(url, params={"per_page": 100})
            self._raise_for_status(resp, context=f"comments on #{number}")
            return resp.json()

    # ── Writes ─────────────────────────────────────────────────────────────────

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
        Submit a PR review with inline, per-line comments in a single call.

        Each comment's `line` MUST be part of the diff (new-file numbering) or
        GitHub returns 422 — filter with diff_utils.commentable_lines first.
        Event stays COMMENT: the CLI's exit code, not the review event, gates CI.
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
        """
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

    # ── Private ────────────────────────────────────────────────────────────────

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


def collect_pr_file_pairs(
    gh: GitHubIntegration, owner: str, repo: str, number: int
) -> tuple[list[FilePair], dict]:
    """
    Fetch (base, head) content pairs for every file changed in a PR, plus a
    pr_ref dict ({"owner", "repo", "number", "head_sha", "base_sha", "diff"})
    the engine carries through for --post.

    Only files ApiDiff can analyse (.py and OpenAPI documents) have their
    contents fetched — no point downloading images and lockfiles.
    """
    from oneport_apidiff.openapi_diff import is_openapi_file

    meta = gh.get_pr_meta(owner, repo, number)
    base_sha = meta.get("base", {}).get("sha", "")
    head_sha = meta.get("head", {}).get("sha", "")

    pairs: list[FilePair] = []
    for f in gh.list_pr_files(owner, repo, number):
        path = f.get("filename", "")
        if not (path.endswith(".py") or is_openapi_file(path)):
            continue
        status = f.get("status", "modified")
        if status == "renamed":
            old_path = f.get("previous_filename", path)
            pairs.append(
                FilePair(path=old_path, base=gh.get_file_at(owner, repo, old_path, base_sha), head=None)
            )
            pairs.append(
                FilePair(path=path, base=None, head=gh.get_file_at(owner, repo, path, head_sha))
            )
            continue
        base = None if status == "added" else gh.get_file_at(owner, repo, path, base_sha)
        head = None if status == "removed" else gh.get_file_at(owner, repo, path, head_sha)
        pairs.append(FilePair(path=path, base=base, head=head))

    pr_ref = {
        "owner": owner,
        "repo": repo,
        "number": number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff": gh.get_pr_diff(owner, repo, number),
    }
    return pairs, pr_ref
