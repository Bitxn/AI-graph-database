"""Oneport Secrets — AI pre-ship secret & .env gate.

Deterministic detection (provider regexes + Shannon entropy) over the working
tree, the staged diff, and the *full git history*; an LLM (Gemini) layer that
does triage only — REAL vs FALSE-POSITIVE — so we cut trufflehog's noise
without ever letting the model be the detector.
"""

__version__ = "1.0.1"
