"""
oneport-impact — the blast-radius engine.

Answers "what breaks if I touch this?" from the repo's own structure and history:
a reverse call graph (who calls it), git co-change mining (what moves with it), and
ownership (who to ask) — judged by an LLM, run entirely on your machine.

Part of the Oneport developer OS. Detection is deterministic; the model only judges.
"""

__version__ = "0.2.0"
