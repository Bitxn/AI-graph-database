"""
Hidden HTML markers embedded in posted GitHub content, so a later run can find
and update the comment it left before — the PR is the state store, no server.
"""

from __future__ import annotations

import re

# Sticky-comment marker: the scan report upserts one comment instead of spamming.
SECRETS_MARKER = "<!-- oneport-secrets -->"
SECRETS_MARKER_RE = re.compile(re.escape(SECRETS_MARKER))


def has_marker(body: str) -> bool:
    return bool(SECRETS_MARKER_RE.search(body or ""))
