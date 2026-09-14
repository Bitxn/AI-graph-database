"""
PR summarizer — generates a high-level summary, per-file walkthrough table,
and a Mermaid diagram for a pull request, for posting as a sticky PR comment.

This is the "brief the human reviewer before they read a single line" feature:
the summary comment is upserted (found by a hidden HTML marker and edited in
place), so force-pushes and new commits update one comment instead of spamming
the PR timeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

try:  # optional legacy BYOK path — managed Gemini proxy is the default
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

from oneport.config import Config, load_config
from oneport.exceptions import AuthError, ParseError, RateLimitError
from oneport.integrations.github import GitHubIntegration, parse_pr_url

# Hidden marker used to find and update the existing summary comment on re-runs.
SUMMARY_MARKER = "<!-- oneport-summary -->"

SUMMARY_SYSTEM_PROMPT = """\
You are a senior engineer writing a pre-review briefing for a pull request, so
a human reviewer understands what it does before reading a single line of the
diff.

Produce:
1. "summary": 1-3 short paragraphs. What the PR does, why (infer from the
   title/description/code), and anything a reviewer should pay extra attention
   to (risky areas, behaviour changes, migrations). Plain prose, no headings.
2. "walkthrough": one entry per changed file, each with a one-sentence
   description of what changed in that file and why it matters. Group
   trivially-related files (e.g. a source file and its test) is NOT allowed —
   one entry per file path exactly as it appears in the diff.
3. "diagram": a Mermaid diagram showing how the changed pieces interact.
   - Use `sequenceDiagram` when the PR changes runtime behaviour/flow.
   - Use `flowchart TD` when the PR is structural (new modules, refactors).
   - Maximum ~15 nodes/participants. Only include elements relevant to this PR.
   - MUST be valid Mermaid syntax. No markdown fences, just the raw diagram
     source starting with `sequenceDiagram` or `flowchart`.
   - Node labels must not contain double quotes or parentheses — Mermaid
     breaks on them. Use plain words.
   If the change is too trivial to diagram meaningfully (typo fixes, doc-only
   changes), return "" for the diagram — do not force one.

Output ONLY valid JSON — no preamble, no markdown fences:
{
  "summary": "...",
  "walkthrough": [
    {"file": "src/auth.py", "change": "Adds OAuth2 token refresh with a 60s clock-skew buffer."}
  ],
  "diagram": "sequenceDiagram\\n  participant C as Client\\n  ..."
}
"""


@dataclass
class WalkthroughEntry:
    file: str
    change: str


@dataclass
class PrSummary:
    summary: str = ""
    walkthrough: list[WalkthroughEntry] = field(default_factory=list)
    diagram: str = ""
    pr_ref: dict | None = None
    model: str = ""
    total_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "walkthrough": [{"file": w.file, "change": w.change} for w in self.walkthrough],
            "diagram": self.diagram,
            "model": self.model,
            "total_tokens": self.total_tokens,
        }


class Summarizer:
    """Generates and posts PR summary briefings."""

    def __init__(self, config: Config | None = None, config_path: str | None = None) -> None:
        self.config = config or load_config(config_path=config_path)
        self._client = (anthropic.Anthropic(api_key=self.config.api_key or "unused-managed-path")
                        if anthropic else None)

    # ── Public API ─────────────────────────────────────────────────────────────

    def summarize(self, pr_url: str) -> PrSummary:
        """Fetch a GitHub PR and generate its summary briefing."""
        import os
        owner, repo, number = parse_pr_url(pr_url)
        integration = GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))
        pr = integration.get_pr(pr_url)

        user_parts = [f"PR Title: {pr.title}"]
        if pr.body:
            user_parts.append(f"PR Description:\n{pr.body}")
        user_parts.append(f"Diff:\n```\n{pr.diff.strip()}\n```")

        raw = self._call_claude(SUMMARY_SYSTEM_PROMPT, "\n\n".join(user_parts))
        summary = self._parse_response(raw)
        summary.pr_ref = {"owner": owner, "repo": repo, "number": number}
        summary.model = self.config.model
        return summary

    def post(self, summary: PrSummary) -> dict:
        """Upsert the summary as a sticky comment on the PR."""
        import os
        if not summary.pr_ref:
            raise ValueError("post() requires a PrSummary produced by summarize().")
        integration = GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))
        return integration.upsert_issue_comment(
            owner=summary.pr_ref["owner"],
            repo=summary.pr_ref["repo"],
            issue_number=summary.pr_ref["number"],
            body=render_markdown(summary),
            marker=SUMMARY_MARKER,
        )

    # ── Internals ──────────────────────────────────────────────────────────────

    def _call_claude(self, system: str, user: str) -> str:
        from oneport.llm import call_gemini, is_gemini_model

        if is_gemini_model(self.config.model):
            text, tokens = call_gemini(
                model=self.config.model,
                api_key=self.config.api_key,
                system=system,
                user=user,
                max_tokens=self.config.max_tokens,
            )
            self._last_tokens = tokens
            return text

        if self._client is None:
            raise AuthError(
                f"Model '{self.config.model}' needs the optional Anthropic SDK "
                "(pip install anthropic) — or use the default managed model."
            )
        try:
            message = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            self._last_tokens = message.usage.input_tokens + message.usage.output_tokens
            return message.content[0].text
        except anthropic.AuthenticationError as exc:
            raise AuthError("Invalid Anthropic API key.") from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError("Anthropic API rate limit hit. Try again shortly.") from exc

    def _parse_response(self, raw: str) -> PrSummary:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ParseError("Claude returned non-JSON summary output", raw_response=raw) from exc

        return PrSummary(
            summary=data.get("summary", "").strip(),
            walkthrough=[
                WalkthroughEntry(file=w.get("file", ""), change=w.get("change", ""))
                for w in data.get("walkthrough", [])
                if w.get("file")
            ],
            diagram=(data.get("diagram") or "").strip(),
            total_tokens=getattr(self, "_last_tokens", 0),
        )


# ── Markdown rendering ──────────────────────────────────────────────────────────

def render_markdown(summary: PrSummary) -> str:
    """Render the sticky-comment markdown. Starts with the hidden upsert marker."""
    lines = [SUMMARY_MARKER, "## 📝 Oneport Summary", "", summary.summary]

    if summary.walkthrough:
        lines += ["", "### Walkthrough", "", "| File | Change |", "|------|--------|"]
        for entry in summary.walkthrough:
            # A literal | in the change text would break the table row.
            change = entry.change.replace("|", "\\|")
            lines.append(f"| `{entry.file}` | {change} |")

    if summary.diagram:
        lines += ["", "### How the pieces interact", "", "```mermaid", summary.diagram, "```"]

    lines += ["", "---", f"*Summarized by [Oneport](https://oneport.dev) · model: `{summary.model}`*"]
    return "\n".join(lines)
