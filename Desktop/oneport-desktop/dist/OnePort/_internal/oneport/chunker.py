"""
Large-diff chunking.

A single model call has a finite budget; a 5,000-line PR crammed into one
prompt either errors out or gets a shallow skim. Instead, big diffs are split
on file boundaries and greedily packed into chunks under a character budget,
one review call per chunk, results merged. Files are never split across
chunks, so every finding still has full within-file context.
"""

from __future__ import annotations

from oneport.diff_utils import split_diff_by_file

# ~60k chars ≈ 15k tokens of diff per call — comfortable within model limits
# alongside the system prompt, rule catalog, and guidelines.
DEFAULT_CHUNK_CHARS = 60_000


def chunk_diff(diff_text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """
    Split a unified diff into chunks of at most `max_chars`, on file boundaries.

    - A diff already under budget is returned as a single chunk, byte-identical
      (so the non-chunked path — and its cache keys — is unaffected).
    - A single file larger than the budget becomes its own oversized chunk:
      one big call is still a better outcome than truncating the file or
      silently skipping it.
    """
    if len(diff_text) <= max_chars:
        return [diff_text]

    segments = split_diff_by_file(diff_text)
    if not segments:
        return [diff_text]  # unparseable — let the single-call path try

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for segment in segments:
        seg_len = len(segment.text) + 1  # +1 for the joining newline
        if current and current_size + seg_len > max_chars:
            chunks.append("\n".join(current))
            current, current_size = [], 0
        current.append(segment.text)
        current_size += seg_len

    if current:
        chunks.append("\n".join(current))
    return chunks
