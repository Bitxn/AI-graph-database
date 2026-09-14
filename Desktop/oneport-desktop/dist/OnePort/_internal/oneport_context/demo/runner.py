# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Runner for `oneport-context demo` — narrate a bundled sample repo, offline."""
from __future__ import annotations

import asyncio
from pathlib import Path

from oneport_context.indexer import build_index
from oneport_context.storyboard import build_storyboard
from oneport_context.show.server import serve_walkthrough

_SAMPLE = Path(__file__).parent / "sample_repo"


def run_demo(port: int = 7000, open_browser: bool = True) -> None:
    """Index the bundled sample repo (heuristic, no key) and serve its walkthrough."""
    idx = asyncio.run(build_index(_SAMPLE, llm=None, model_name="bundled demo (offline)"))
    idx.name = "TaskFlow"          # nicer than the on-disk folder name
    story = asyncio.run(build_storyboard(idx))
    serve_walkthrough(idx, story, port=port, open_browser=open_browser)
