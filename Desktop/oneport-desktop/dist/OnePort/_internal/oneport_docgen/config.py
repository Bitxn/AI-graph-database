# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Branding config — `docgen.toml`.

Lets a company give every generated document a consistent house style (name, logo,
colours, footer) without touching code. Looked up in the target repo first, then the
current directory. Absent config just falls back to a clean default theme.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "docgen.toml"


@dataclass
class Brand:
    company: str = ""
    product: str = ""
    logo: str = ""                 # path to a PNG/JPG, relative to the config file
    primary: str = "#0F172A"       # deep slate — cover + control bars
    accent: str = "#4F46E5"        # indigo — section rules + headings
    accent2: str = "#0EA5E9"       # sky — project name accent
    font: str = "Helvetica"        # PDF base font family
    footer: str = ""               # extra footer line, e.g. "© 2026 Acme, Inc."

    _base: Path = Path(".")

    def logo_path(self) -> Path | None:
        if not self.logo:
            return None
        p = Path(self.logo)
        if not p.is_absolute():
            p = self._base / p
        return p if p.exists() else None


_FIELDS = {"company", "product", "logo", "primary", "accent", "accent2", "font", "footer"}


def find_config(repo: Path) -> Path | None:
    for base in (repo, Path.cwd()):
        p = base / CONFIG_NAME
        if p.exists():
            return p
    return None


def load_brand(repo: Path) -> Brand:
    """Load docgen.toml from the repo (or cwd); return defaults if none is found."""
    cfg = find_config(repo)
    if cfg is None:
        b = Brand()
        b._base = repo
        return b
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        b = Brand()
        b._base = cfg.parent
        return b
    section = data.get("brand", data)          # accept [brand] table or top-level keys
    kwargs = {k: v for k, v in section.items() if k in _FIELDS}
    b = Brand(**kwargs)
    b._base = cfg.parent
    return b


SAMPLE_TOML = """\
# oneport-docgen branding — applies to every generated document.
# Delete any line to fall back to the default.
[brand]
company  = "Acme Corporation"
product  = ""                       # optional product/programme name
logo     = ""                       # path to a PNG/JPG cover logo, e.g. "assets/logo.png"
primary  = "#0F172A"                # cover background + control bars (hex)
accent   = "#4F46E5"                # section rules + headings (hex)
accent2  = "#0EA5E9"                # project-name accent (hex)
footer   = "Confidential — internal distribution only"
"""


def write_sample(dest: Path) -> Path:
    dest.write_text(SAMPLE_TOML, encoding="utf-8")
    return dest
