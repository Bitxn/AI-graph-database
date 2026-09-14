"""
Bitbucket integration — fetches PR diffs via the Bitbucket Cloud REST API 2.0.

Authentication: Bitbucket app passwords (username + app password).
Set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD environment variables.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from oneport.exceptions import IntegrationError


BITBUCKET_API_URL = "https://api.bitbucket.org/2.0"

_PR_URL_RE = re.compile(
    r"https?://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/pull-requests/(?P<number>\d+)"
)


@dataclass
class BitbucketPR:
    number: int
    title: str
    description: str
    diff: str
    source_branch: str
    destination_branch: str


class BitbucketIntegration:
    def __init__(self, username: str = "", app_password: str = "") -> None:
        self.username = username or os.getenv("BITBUCKET_USERNAME", "")
        self.app_password = app_password or os.getenv("BITBUCKET_APP_PASSWORD", "")
        self._auth = (self.username, self.app_password) if self.username else None

    def get_pr(self, url: str) -> BitbucketPR:
        """
        Fetch a pull request and its diff from a Bitbucket PR URL.
        """
        match = _PR_URL_RE.match(url)
        if not match:
            raise IntegrationError(f"Unrecognised Bitbucket PR URL: {url}")

        workspace = match.group("workspace")
        repo = match.group("repo")
        number = int(match.group("number"))

        with httpx.Client(auth=self._auth, timeout=30) as client:
            meta = self._fetch_pr_meta(client, workspace, repo, number)
            diff = self._fetch_diff(client, workspace, repo, number)

        return BitbucketPR(
            number=number,
            title=meta.get("title", ""),
            description=meta.get("description", "") or "",
            diff=diff,
            source_branch=meta.get("source", {}).get("branch", {}).get("name", ""),
            destination_branch=meta.get("destination", {}).get("branch", {}).get("name", ""),
        )

    # ── Private ────────────────────────────────────────────────────────────────

    def _fetch_pr_meta(
        self, client: httpx.Client, workspace: str, repo: str, number: int
    ) -> dict:
        url = f"{BITBUCKET_API_URL}/repositories/{workspace}/{repo}/pullrequests/{number}"
        resp = client.get(url)
        self._raise_for_status(resp, f"PR #{number}")
        return resp.json()

    def _fetch_diff(
        self, client: httpx.Client, workspace: str, repo: str, number: int
    ) -> str:
        url = f"{BITBUCKET_API_URL}/repositories/{workspace}/{repo}/pullrequests/{number}/diff"
        resp = client.get(url)
        self._raise_for_status(resp, f"PR #{number} diff")
        return resp.text

    def _raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
        if resp.status_code == 401:
            raise IntegrationError(
                f"Bitbucket authentication failed for {context}. "
                "Check BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD.",
                status_code=401,
            )
        if resp.status_code == 404:
            raise IntegrationError(
                f"Bitbucket resource not found: {context}.",
                status_code=404,
            )
        if not resp.is_success:
            raise IntegrationError(
                f"Bitbucket API error for {context}: {resp.status_code}",
                status_code=resp.status_code,
            )
