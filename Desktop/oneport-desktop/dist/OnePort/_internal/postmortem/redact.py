"""
Secret / PII redaction — runs BEFORE any incident data leaves the machine.

Incident logs and Slack threads routinely carry credentials, tokens, customer
emails and internal IPs. OnePort's privacy rule is that only what's needed to
do the job should travel; a post-mortem needs the *shape* of an error, not the
bearer token that happened to be on the request line. So we scrub known secret
and PII patterns first and hand the model a redacted copy.

Redaction is deterministic and local. Each match is replaced with a typed
placeholder (e.g. `[REDACTED:aws_key]`) so the model still sees that "a key was
here" without seeing the key. Timestamps, log levels, stack frames, hostnames
and error text are deliberately preserved — the analysis depends on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: the most specific / highest-entropy patterns run first so a
# generic "long token" rule can't swallow a value a precise rule would label
# better. Each entry is (kind, compiled regex). Group 0 is redacted whole
# unless the pattern uses a capturing group named `secret`, in which case only
# that group is replaced (keeps surrounding context like `password=` readable).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # PEM private key blocks (multi-line) — collapse the whole block.
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[posur]_[A-Za-z0-9]{20,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+(?P<secret>[A-Za-z0-9._\-]{12,})")),
    # key=value / key: value secrets — redact only the value.
    ("credential", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|apikey|token|auth"
        r"|access[_-]?key|client[_-]?secret)\b\s*[=:]\s*"
        r"(?P<secret>[^\s'\"`,;]{4,})")),
    # Credentials embedded in a URL: scheme://user:pass@host
    ("url_credential", re.compile(r"(?<=://)(?P<secret>[^:/\s@]+:[^/\s@]+)(?=@)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("ip", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
]

# Emails inside a redaction placeholder must not re-trigger the email rule, and
# private/loopback IPs are noise, not PII — keep them (they aid analysis).
_IP_KEEP = re.compile(r"^(?:127\.|0\.0\.0\.0$|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.)")


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        """Human line like `3 redacted (aws_key×1, email×2)`, or '' if nothing."""
        if not self.total:
            return ""
        parts = ", ".join(f"{k}×{v}" for k, v in sorted(self.counts.items()))
        return f"{self.total} redacted ({parts})"


def redact_text(text: str | None) -> RedactionResult:
    """Return a redacted copy of ``text`` plus per-kind match counts.

    Idempotent in practice: placeholders like ``[REDACTED:email]`` don't match
    any secret pattern, so redacting already-redacted text is a no-op."""
    if not text:
        return RedactionResult(text=text or "", counts={})

    counts: dict[str, int] = {}

    def _sub(kind: str, pattern: re.Pattern, s: str) -> str:
        def repl(m: re.Match) -> str:
            # Private/loopback IPs are kept — they're not sensitive and the
            # timeline often needs them.
            if kind == "ip" and _IP_KEEP.match(m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            if "secret" in m.groupdict() and m.group("secret") is not None:
                start, end = m.span("secret")
                return m.group(0)[: start - m.start()] + f"[REDACTED:{kind}]" + m.group(0)[end - m.start():]
            return f"[REDACTED:{kind}]"

        return pattern.sub(repl, s)

    out = text
    for kind, pattern in _PATTERNS:
        out = _sub(kind, pattern, out)
    return RedactionResult(text=out, counts=counts)


def redact_all(*texts: str | None) -> tuple[list[str], RedactionResult]:
    """Redact several inputs, returning the redacted list and a merged summary."""
    merged: dict[str, int] = {}
    redacted: list[str] = []
    for t in texts:
        r = redact_text(t)
        redacted.append(r.text)
        for k, v in r.counts.items():
            merged[k] = merged.get(k, 0) + v
    return redacted, RedactionResult(text="", counts=merged)
