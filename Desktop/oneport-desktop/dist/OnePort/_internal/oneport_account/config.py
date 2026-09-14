"""
Where the client points and where it stores the user's token.

The API base URL resolves in this order:
  1. $ONEPORT_API_URL                      (override — CI, self-host, staging)
  2. the api_url saved in the credentials  (pinned at login)
  3. DEFAULT_API_URL                        (the hosted Oneport backend)

Only the last needs editing once, after the Supabase project is created:
replace <PROJECT-REF> with your Supabase project ref.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── The hosted Oneport backend (FastAPI) — the single source of truth ────────
# Serves /llm, /account, /redeem, /ship-runs, /issue-token and reads/writes the
# ONE Supabase project the web Console also reads. Pointing the CLI here is what
# makes a user's CLI usage + ship runs show up in their Console.
# Override per-run with $ONEPORT_API_URL (staging / self-host).
DEFAULT_API_URL = "https://api.numbleai.com"

CREDENTIALS_PATH = Path(os.path.expanduser("~")) / ".oneport" / "credentials.json"
DEFAULT_BUY_URL = "https://version-4-production.d2tx07mbxfs880.amplifyapp.com/pricing"


def api_base(pinned: str | None = None) -> str:
    return (os.getenv("ONEPORT_API_URL") or pinned or DEFAULT_API_URL).rstrip("/")
