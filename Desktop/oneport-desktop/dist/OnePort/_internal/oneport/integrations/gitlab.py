"""
GitLab integration — fetches MR diffs via the GitLab REST API v4.

Supports GitLab.com and self-hosted GitLab instances.
Set GITLAB_API_URL for self-hosted (default: https://gitlab.com/api/v4).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from oneport.exceptions import IntegrationError


GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4")

_MR_URL_RE = re.compile(
    r"https?://[^/]+/(?P<namespace>.+)/-/merge_requests/(?P<number>\d+)"
)


@dataclass
class MergeRequest:
    number: int
    title: str
    description: str
    diff: str
    source_branch: str
    target_branch: str


class GitLabIntegration:
    def __init__(self, token: str = "") -> None:
        self.token = token or os.getenv("GITLAB_TOKEN", "")
        self._headers = {"User-Agent": "oneport-review/1.0"}
        if self.token:
            self._headers["PRIVATE-TOKEN"] = self.token

    def get_mr(self, url: str) -> MergeRequest:
        """
        Fetch a merge request and its diff from a GitLab MR URL.

        Args:
            url: Full GitLab MR URL.

        Returns:
            MergeRequest with title, description, and diff populated.
        """
        match = _MR_URL_RE.match(url)
        if not match:
            raise IntegrationError(f"Unrecognised GitLab MR URL: {url}")

        namespace = match.group("namespace").replace("/", "%2F")
        number = int(match.group("number"))

        with httpx.Client(headers=self._headers, timeout=30) as client:
            meta = self._fetch_mr_meta(client, namespace, number)
            diff = self._fetch_diff(client, namespace, number)

        return MergeRequest(
            number=number,
            title=meta.get("title", ""),
            description=meta.get("description", "") or "",
            diff=diff,
            source_branch=meta.get("source_branch", ""),
            target_branch=meta.get("target_branch", ""),
        )

    # ── Private ────────────────────────────────────────────────────────────────

    def _fetch_mr_meta(self, client: httpx.Client, namespace: str, number: int) -> dict:
        url = f"{GITLAB_API_URL}/projects/{namespace}/merge_requests/{number}"
        resp = client.get(url)
        self._raise_for_status(resp, f"MR #{number}")
        return resp.json()

    def _fetch_diff(self, client: httpx.Client, namespace: str, number: int) -> str:
        url = f"{GITLAB_API_URL}/projects/{namespace}/merge_requests/{number}/diffs"
        resp = client.get(url)
        self._raise_for_status(resp, f"MR #{number} diff")
        diffs = resp.json()
        return "\n".join(
            f"--- {d.get('old_path', '')}\n+++ {d.get('new_path', '')}\n{d.get('diff', '')}"
            for d in diffs
        )

    def _raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
        if resp.status_code == 401:
            raise IntegrationError(
                f"GitLab authentication failed for {context}. Check GITLAB_TOKEN.",
                status_code=401,
            )
        if resp.status_code == 404:
            raise IntegrationError(
                f"GitLab resource not found: {context}.",
                status_code=404,
            )
        if not resp.is_success:
            raise IntegrationError(
                f"GitLab API error for {context}: {resp.status_code}",
                status_code=resp.status_code,
            )
