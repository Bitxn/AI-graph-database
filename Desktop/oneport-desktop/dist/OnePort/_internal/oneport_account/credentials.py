"""
Local credential storage: ~/.oneport/credentials.json holds the user's
`op_live_...` token, their email, and the API URL pinned at login. The file is
written 0600 (owner-only) where the OS supports it.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from oneport_account.config import CREDENTIALS_PATH


@dataclass
class Credentials:
    token: str
    email: str = ""
    api_url: str = ""


def load() -> Credentials | None:
    p = CREDENTIALS_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = data.get("token")
    if not token:
        return None
    return Credentials(token=token, email=data.get("email", ""), api_url=data.get("api_url", ""))


def save(creds: Credentials) -> None:
    p = CREDENTIALS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "token": creds.token, "email": creds.email, "api_url": creds.api_url,
    }, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600; best-effort on Windows
    except OSError:
        pass


def clear() -> bool:
    p = CREDENTIALS_PATH
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False
