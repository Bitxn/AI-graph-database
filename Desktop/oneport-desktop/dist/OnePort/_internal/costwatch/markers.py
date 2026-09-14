"""
Hidden HTML marker embedded in posted GitHub comments.

The marker lets `analyze --post` find and update its own previous comment
instead of stacking a new one on every push — the PR itself is the state store,
no server or database required.
"""

from __future__ import annotations

COST_COMMENT_MARKER = "<!-- oneport-costwatch -->"
