"""
Shannon-entropy scoring for catching high-entropy strings that no provider
regex knows about (rotated custom tokens, opaque API keys, base64 blobs).

This is deliberately noisy on its own — that is the whole point of the LLM
triage layer downstream. Detection stays deterministic; judgment is the model's.
"""

from __future__ import annotations

import math
import re

# Candidate substrings that *could* be a secret: long runs of the character sets
# real credentials live in. We score each and keep the high-entropy ones.
_BASE64_RE = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")

# Empirically-tuned thresholds (bits/char). Base64 alphabet tops out near 6.0;
# English prose sits ~3.5–4.0, so 4.5 separates credentials from words well.
BASE64_THRESHOLD = 4.5
HEX_THRESHOLD = 3.0


def shannon_entropy(data: str) -> float:
    """Bits of Shannon entropy per character. Empty string scores 0."""
    if not data:
        return 0.0
    counts: dict[str, int] = {}
    for ch in data:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


# Content-address digest lengths: md5(32), sha1/git-SHA(40), sha256(64),
# sha512(128). A pure-hex string of exactly these lengths is overwhelmingly a
# hash — CI action pins, lockfile digests, GitHub blob SHAs — not a credential.
# Real hex API keys are rarely these exact "digest" lengths, and provider-format
# keys are caught by regex rules anyway. Skipping them kills the dominant
# entropy false positive on real repos without weakening secret coverage.
_DIGEST_LENGTHS = frozenset({32, 40, 64, 128})


def _is_hash_digest(token: str) -> bool:
    return len(token) in _DIGEST_LENGTHS and all(c in "0123456789abcdefABCDEF" for c in token)


def _looks_wordy(token: str) -> bool:
    """Cheap guard against flagging identifiers like get_user_by_id_and_name.

    A token dominated by a few repeated separators or with very few distinct
    characters is almost never a credential.
    """
    distinct = len(set(token))
    if distinct <= 8:
        return True
    # Mostly underscores/dashes (snake/kebab identifiers) → not a secret.
    seps = sum(token.count(c) for c in "_-")
    return seps > len(token) * 0.25


def entropy_candidates(text: str) -> list[tuple[int, int, str, float]]:
    """Yield (start, end, token, entropy) for every high-entropy run in `text`.

    Spans are byte offsets within `text`, so callers can dedupe against regex
    matches that already cover the same characters.
    """
    hits: list[tuple[int, int, str, float]] = []

    for m in _BASE64_RE.finditer(text):
        token = m.group(0)
        if _looks_wordy(token):
            continue
        ent = shannon_entropy(token)
        if ent >= BASE64_THRESHOLD:
            hits.append((m.start(), m.end(), token, ent))

    for m in _HEX_RE.finditer(text):
        token = m.group(0)
        if _is_hash_digest(token):
            continue  # md5/sha1/sha256/sha512 digest — a hash, not a secret
        ent = shannon_entropy(token)
        # Hex is a 16-symbol alphabet (~4 bits max); a genuine random hex secret
        # still clears 3.0, while "aaaaaaaa..." padding does not.
        if ent >= HEX_THRESHOLD and not _looks_wordy(token):
            # avoid double-reporting a span already flagged as base64
            if not any(s <= m.start() and m.end() <= e for s, e, _, _ in hits):
                hits.append((m.start(), m.end(), token, ent))

    return hits
