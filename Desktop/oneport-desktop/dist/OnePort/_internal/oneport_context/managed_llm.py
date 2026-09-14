# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
Managed LLM backend for oneport-context.

Context holds no model key. Every model call (file summaries, repo summary,
chat answers, PDF narrative, video script) goes through the Oneport managed
proxy, which runs the model with a server-side key and meters the token usage
against the logged-in account. Only the already-built prompt is sent — the repo
itself is never uploaded (retrieval reads files locally and inlines the slices).

This adapter matches oneport_debug_core's `BaseLLMProvider` shape (an awaitable
`complete(prompt, options)` plus an `active_provider` label) so it drops into the
CLI exactly where an `LLMRouter` used to go.
"""

from __future__ import annotations

import asyncio

from oneport_account import (
    AccountError,
    NotLoggedIn,
    OutOfTokens,
    RateLimited,
    call_managed_llm,
)

DEFAULT_MODEL = "gemini-flash-latest"


class ManagedLLM:
    """Routes `complete()` through the Oneport metered proxy under tool='context'."""

    active_provider = "oneport-managed (gemini)"
    name = "oneport-managed"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    async def complete(self, prompt: str, options=None) -> str:
        temperature = getattr(options, "temperature", 0.2) if options else 0.2
        max_tokens = getattr(options, "max_tokens", None) if options else None

        def _run() -> str:
            try:
                result = call_managed_llm(
                    system="",
                    user=prompt,
                    tool="context",
                    action="generate",
                    model=self.model,
                    max_tokens=max_tokens or 2048,
                    temperature=temperature,
                )
            except NotLoggedIn as exc:
                raise RuntimeError(
                    "Not logged in to Oneport. Run `oneport-account login <token>` "
                    "(free token at https://oneport.dev)."
                ) from exc
            except OutOfTokens as exc:
                raise RuntimeError(f"Out of Oneport tokens. Top up at {exc.buy_url}") from exc
            except RateLimited as exc:
                raise RuntimeError("Model rate-limited right now — try again shortly.") from exc
            except AccountError as exc:
                raise RuntimeError(f"Oneport service error: {exc}") from exc
            return result.text

        # call_managed_llm is synchronous httpx; keep the event loop free.
        return await asyncio.to_thread(_run)
