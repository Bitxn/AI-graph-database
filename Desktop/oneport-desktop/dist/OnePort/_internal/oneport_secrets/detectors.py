"""
The deterministic detection catalog: provider-specific credential regexes plus
the entropy fallback. This is the *only* thing that ever decides "there is a
candidate secret here". The LLM never runs here.

Each Detector carries a stable id used both for reporting and to look up
provider-specific revocation steps in oneport_secrets/remediation.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from oneport_secrets.entropy import entropy_candidates


@dataclass(frozen=True)
class Detector:
    id: str
    name: str
    severity: str  # critical | high | medium | low
    pattern: re.Pattern[str]
    description: str = ""


@dataclass(frozen=True)
class RawHit:
    detector_id: str
    detector_name: str
    severity: str
    match: str
    start: int
    end: int
    entropy: float | None = None


def _d(id: str, name: str, severity: str, pattern: str, flags: int = 0, desc: str = "") -> Detector:
    return Detector(id, name, severity, re.compile(pattern, flags), desc)


# ── Provider catalog ────────────────────────────────────────────────────────────
# Ordered high→low confidence. Anchored/high-specificity patterns first so their
# spans win when the entropy fallback would otherwise re-flag the same chars.

CATALOG: list[Detector] = [
    # AWS
    _d("aws-access-key-id", "AWS Access Key ID", "critical",
       r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[0-9A-Z]{16}\b",
       desc="AWS access key identifier"),
    _d("aws-secret-access-key", "AWS Secret Access Key", "critical",
       r"(?i)aws.{0,20}?(?:secret|access).{0,20}?['\"]([A-Za-z0-9/+=]{40})['\"]",
       desc="AWS secret access key (contextual)"),
    # Google / GCP
    _d("gcp-api-key", "Google API Key", "high",
       r"\bAIza[0-9A-Za-z\-_]{35}\b", desc="Google/Firebase API key"),
    _d("gcp-oauth-client", "Google OAuth Client ID", "medium",
       r"\b[0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com\b"),
    _d("gcp-service-account", "GCP Service Account Key", "critical",
       r'"type":\s*"service_account"', desc="GCP service-account JSON key material"),
    # Stripe
    _d("stripe-secret-key", "Stripe Secret Key", "critical",
       r"\b(?:sk|rk)_live_[0-9a-zA-Z]{24,}\b", desc="Stripe live secret/restricted key"),
    _d("stripe-test-key", "Stripe Test Key", "low",
       r"\b(?:sk|rk)_test_[0-9a-zA-Z]{24,}\b", desc="Stripe test-mode key"),
    # GitHub
    _d("github-pat", "GitHub Personal Access Token", "critical",
       r"\bghp_[0-9A-Za-z]{36}\b"),
    _d("github-oauth", "GitHub OAuth/Server Token", "critical",
       r"\b(?:gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b"),
    _d("github-fine-grained", "GitHub Fine-grained PAT", "critical",
       r"\bgithub_pat_[0-9A-Za-z_]{82}\b"),
    # GitLab
    _d("gitlab-pat", "GitLab Personal Access Token", "high",
       r"\bglpat-[0-9A-Za-z\-_]{20}\b"),
    # Slack
    _d("slack-token", "Slack Token", "high",
       r"\bxox[baprs]-[0-9A-Za-z-]{10,48}\b"),
    _d("slack-webhook", "Slack Incoming Webhook", "medium",
       r"https://hooks\.slack\.com/services/T[0-9A-Za-z_]+/B[0-9A-Za-z_]+/[0-9A-Za-z_]{20,}"),
    # Payment / comms SaaS
    _d("twilio-api-key", "Twilio API Key", "high", r"\bSK[0-9a-fA-F]{32}\b"),
    _d("sendgrid-key", "SendGrid API Key", "high",
       r"\bSG\.[0-9A-Za-z_\-]{22}\.[0-9A-Za-z_\-]{43}\b"),
    _d("mailgun-key", "Mailgun API Key", "high", r"\bkey-[0-9a-zA-Z]{32}\b"),
    # LLM / package registries
    _d("openai-key", "OpenAI API Key", "critical",
       r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}T3BlbkFJ[A-Za-z0-9_\-]{20,}\b"),
    _d("anthropic-key", "Anthropic API Key", "critical",
       r"\bsk-ant-[0-9A-Za-z\-_]{90,}\b"),
    _d("npm-token", "npm Access Token", "high", r"\bnpm_[0-9A-Za-z]{36}\b"),
    _d("pypi-token", "PyPI Upload Token", "critical", r"\bpypi-AgEIcHlwaS[0-9A-Za-z\-_]{50,}\b"),
    # JWT & private keys
    _d("jwt", "JSON Web Token", "medium",
       r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    _d("private-key", "Private Key (PEM)", "critical",
       r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
       desc="PEM-encoded private key block"),
    # Connection strings with an embedded password
    _d("db-connection-uri", "Database URI with password", "high",
       r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:@/\s]+:([^@/\s]{3,})@[^\s'\"]+"),
    # Generic labelled assignment (lowest confidence — leans on triage)
    _d("generic-secret-assignment", "Generic secret assignment", "medium",
       r"""(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth)\s*[:=]\s*['"]([^'"\s]{8,})['"]""",
       desc="password/secret/token assigned a quoted literal"),
]

CATALOG_BY_ID: dict[str, Detector] = {d.id: d for d in CATALOG}

# Obvious non-secret literals the generic/db detectors would otherwise flag.
# These are skipped deterministically (not sent to the model) to keep noise low;
# genuine ambiguity is still left to triage.
_OBVIOUS_PLACEHOLDERS = {
    "password", "changeme", "change_me", "your_password", "yourpassword",
    "example", "placeholder", "redacted", "xxxxxxxx", "supersecret",
    "notarealsecret", "dummy", "test", "password123", "hunter2",
}

# Words that mark a value as a placeholder even inside a longer readable phrase.
# hono's JWT tests assign `a-secret`, `it-is-a-secret`, `cookie-secret` — all
# contain "secret" and are plain words, but exact-match membership missed them.
# `change` covers the "change this / change me / changethis" instruction family
# (the FastAPI template ships POSTGRES_PASSWORD="changethis" in its docs) — the
# single commonest placeholder, which must not fail a gate when triage is down.
# The readability guard in _generic_value_is_placeholder keeps a real hex/base64
# credential that happens to contain one of these words from being suppressed.
_PLACEHOLDER_SUBSTRINGS = (
    "secret", "example", "changeme", "changethis", "change-", "change_",
    "placeholder", "dummy", "sample", "yoursecret", "your-", "your_", "fake",
    "notreal", "not-a-real", "replace", "todo", "insertyour", "yourapikey",
)


def _generic_value_is_placeholder(hit_match: str, group_value: str | None) -> bool:
    if not group_value:
        return False
    low = group_value.lower()
    if low in _OBVIOUS_PLACEHOLDERS:
        return True
    # A value that contains a placeholder word AND reads like words (letters and
    # separators, not high-entropy gibberish) is a placeholder: "a-secret",
    # "it-is-a-secret". A real credential that happens to contain "secret" is
    # high-entropy, so the readability guard keeps it flagged.
    if any(sub in low for sub in _PLACEHOLDER_SUBSTRINGS):
        readable = sum(c.isalpha() or c in "-_ ." for c in group_value)
        if readable / len(group_value) > 0.7:
            return True
    return False


def detect(
    text: str,
    extra_detectors: list[Detector] | None = None,
    entropy_enabled: bool = True,
) -> list[RawHit]:
    """Run the catalog (+ any custom detectors) and the entropy fallback on one
    string. Returns hits sorted by position, with overlapping spans deduped so a
    provider match always beats a redundant entropy match on the same characters.
    """
    detectors = list(CATALOG) + (extra_detectors or [])
    hits: list[RawHit] = []
    claimed: list[tuple[int, int]] = []

    for det in detectors:
        for m in det.pattern.finditer(text):
            # For detectors with a capture group, the group is the actual secret;
            # fall back to the whole match otherwise.
            group_value = m.group(1) if m.groups() else None
            secret = group_value or m.group(0)
            if det.id == "generic-secret-assignment" and _generic_value_is_placeholder(
                m.group(0), group_value
            ):
                continue
            hits.append(RawHit(det.id, det.name, det.severity, secret, m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    if entropy_enabled:
        for start, end, token, ent in entropy_candidates(text):
            if any(s <= start and end <= e for s, e in claimed):
                continue  # already covered by a provider detector
            hits.append(
                RawHit("high-entropy-string", "High-entropy string", "medium",
                       token, start, end, entropy=ent)
            )

    hits.sort(key=lambda h: h.start)
    return hits
