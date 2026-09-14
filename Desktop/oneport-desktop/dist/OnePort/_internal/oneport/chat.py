"""
Interactive PR chat — lets developers talk to Oneport in PR comment threads.

Designed to run inside a GitHub Action on `issue_comment` and
`pull_request_review_comment` events:

    on:
      issue_comment: {types: [created]}
      pull_request_review_comment: {types: [created]}

    ... run: oneport respond   # reads $GITHUB_EVENT_PATH

Behaviour:
  - Comments that don't mention the trigger (default "@oneport") are skipped.
  - Bot comments are always skipped — otherwise Oneport would reply to itself
    in an infinite loop.
  - "@oneport remember: <rule>" appends the rule to the repo's guidelines file
    with a commit on the PR's head branch, then confirms in a reply. From that
    commit on, every review enforces the rule.
  - Anything else is answered conversationally: for inline review-comment
    threads the reply is threaded and the model sees the file path, the diff
    hunk, and the whole thread; for plain PR comments the model sees the PR
    title/description/diff and replies in the conversation tab.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

try:  # optional legacy BYOK path — managed Gemini proxy is the default
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

from oneport.config import Config, load_config
from oneport.exceptions import AuthError, IntegrationError, RateLimitError
from oneport.guidelines import DEFAULT_GUIDELINES_PATH, FILE_HEADER
from oneport.integrations.github import GitHubIntegration

DEFAULT_TRIGGER = "@oneport"

_REMEMBER_RE = re.compile(
    r"remember\s*:?\s*(?P<rule>.+)", re.IGNORECASE | re.DOTALL
)

CHAT_SYSTEM_PROMPT = """\
You are Oneport, an AI code reviewer, replying inside a GitHub pull request
conversation. A developer has mentioned you and you must answer their last
message.

How to answer:
- Be direct and technically precise. Answer the question actually asked.
- Keep replies short — a few sentences, or a short code block when code says it
  better. This is a PR thread, not a blog post.
- If asked to explain one of your review findings, explain the underlying
  problem and its consequences concretely (what input breaks it, what an
  attacker could do, what the failure looks like in production).
- If asked for alternatives, give at most two, each with a one-line trade-off.
- If the developer pushes back and they are right, concede plainly and say so.
  If they are wrong, hold your position and show the failing case.
- Use GitHub-flavoured markdown. Never wrap your whole reply in a code fence.
- You are replying as a comment — do NOT prefix your reply with a greeting or
  sign it.
"""


@dataclass
class ChatEvent:
    """The subset of a GitHub webhook event the responder needs."""

    kind: str                    # "issue_comment" | "review_comment"
    owner: str = ""
    repo: str = ""
    pr_number: int = 0
    comment_id: int = 0
    comment_body: str = ""
    comment_author: str = ""
    # Review-comment (inline thread) extras:
    path: str = ""
    line: int = 0
    diff_hunk: str = ""
    in_reply_to_id: int | None = None


def parse_event(event: dict, trigger: str = DEFAULT_TRIGGER) -> ChatEvent | None:
    """
    Turn a raw GitHub event payload into a ChatEvent, or None if it isn't
    something Oneport should answer (no trigger mention, a bot author, or a
    comment on a plain issue rather than a PR).
    """
    comment = event.get("comment") or {}
    body = comment.get("body") or ""
    author = ((comment.get("user") or {}).get("login")) or ""

    if trigger.lower() not in body.lower():
        return None
    if author.endswith("[bot]"):
        return None  # never answer bots — that way lies infinite recursion

    repo_full = ((event.get("repository") or {}).get("full_name")) or ""
    if "/" not in repo_full:
        return None
    owner, repo = repo_full.split("/", 1)

    if "pull_request" in event:  # pull_request_review_comment event
        pr_number = event["pull_request"].get("number", 0)
        return ChatEvent(
            kind="review_comment",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            comment_id=comment.get("id", 0),
            comment_body=body,
            comment_author=author,
            path=comment.get("path", ""),
            line=comment.get("line") or comment.get("original_line") or 0,
            diff_hunk=comment.get("diff_hunk", ""),
            in_reply_to_id=comment.get("in_reply_to_id"),
        )

    issue = event.get("issue") or {}
    if "pull_request" not in issue:
        return None  # a plain issue comment, not a PR conversation
    return ChatEvent(
        kind="issue_comment",
        owner=owner,
        repo=repo,
        pr_number=issue.get("number", 0),
        comment_id=comment.get("id", 0),
        comment_body=body,
        comment_author=author,
    )


class Responder:
    """Answers @oneport mentions in PR threads."""

    def __init__(
        self,
        config: Config | None = None,
        config_path: str | None = None,
        github: GitHubIntegration | None = None,
    ) -> None:
        self.config = config or load_config(config_path=config_path)
        self._client = (anthropic.Anthropic(api_key=self.config.api_key or "unused-managed-path")
                        if anthropic else None)
        import os
        self._github = github or GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))

    # ── Public API ─────────────────────────────────────────────────────────────

    def respond(self, event: ChatEvent, trigger: str = DEFAULT_TRIGGER) -> dict:
        """Handle one actionable ChatEvent. Returns the posted comment object."""
        instruction = _strip_trigger(event.comment_body, trigger)

        rule = _extract_remember(instruction)
        if rule is not None:
            return self._handle_remember(event, rule)

        reply = self._generate_reply(event, instruction)
        return self._post_reply(event, reply)

    # ── remember: commit a guideline to the PR branch ──────────────────────────

    def _handle_remember(self, event: ChatEvent, rule: str) -> dict:
        from datetime import date

        pr_url = f"https://github.com/{event.owner}/{event.repo}/pull/{event.pr_number}"
        pr = self._github.get_pr(pr_url)
        path = self.config.guidelines_path or DEFAULT_GUIDELINES_PATH

        rule_line = " ".join(rule.split())
        entry = (
            f"- {rule_line}  "
            f"<!-- added {date.today().isoformat()} via PR #{event.pr_number} "
            f"by @{event.comment_author} -->\n"
        )

        existing = self._github.get_file(event.owner, event.repo, path, ref=pr.head_branch)
        if existing is None:
            content, sha = FILE_HEADER + "\n" + entry, None
        else:
            old_content, sha = existing
            joiner = "" if old_content.endswith("\n") else "\n"
            content = old_content + joiner + entry

        self._github.put_file(
            owner=event.owner,
            repo=event.repo,
            path=path,
            branch=pr.head_branch,
            content=content,
            message=f"chore: add Oneport review guideline (requested by @{event.comment_author})",
            sha=sha,
        )

        confirmation = (
            f"Remembered — committed to `{path}` on `{pr.head_branch}`:\n\n"
            f"> {rule_line}\n\n"
            f"Every review from now on will enforce this."
        )
        return self._post_reply(event, confirmation)

    # ── Conversational replies ─────────────────────────────────────────────────

    def _generate_reply(self, event: ChatEvent, instruction: str) -> str:
        context_parts: list[str] = []

        pr_url = f"https://github.com/{event.owner}/{event.repo}/pull/{event.pr_number}"
        pr = self._github.get_pr(pr_url)
        context_parts.append(f"PR Title: {pr.title}")
        if pr.body:
            context_parts.append(f"PR Description:\n{pr.body}")

        if event.kind == "review_comment":
            context_parts.append(f"The thread is anchored to `{event.path}` line {event.line}.")
            if event.diff_hunk:
                context_parts.append(f"Diff hunk under discussion:\n```\n{event.diff_hunk}\n```")
            thread = self._fetch_thread(event)
            if thread:
                rendered = "\n\n".join(f"**{c['author']}** wrote:\n{c['body']}" for c in thread)
                context_parts.append(f"Thread so far (oldest first):\n{rendered}")
        else:
            # Conversation-tab mention: give the model the full diff, since the
            # question can be about anything in the PR.
            context_parts.append(f"PR Diff:\n```\n{pr.diff.strip()}\n```")

        context_parts.append(
            f"**{event.comment_author}**'s message to you (answer this):\n{instruction}"
        )

        return self._call_claude(CHAT_SYSTEM_PROMPT, "\n\n".join(context_parts))

    def _fetch_thread(self, event: ChatEvent) -> list[dict]:
        """Reconstruct the inline comment thread this mention belongs to."""
        root_id = event.in_reply_to_id or event.comment_id
        try:
            all_comments = self._github.list_review_comments(
                event.owner, event.repo, event.pr_number
            )
        except IntegrationError:
            return []  # degrade gracefully — reply without thread history

        thread = [
            c for c in all_comments
            if c.get("id") == root_id or c.get("in_reply_to_id") == root_id
        ]
        thread.sort(key=lambda c: c.get("created_at", ""))
        return [
            {"author": (c.get("user") or {}).get("login", "unknown"), "body": c.get("body", "")}
            for c in thread
        ]

    def _post_reply(self, event: ChatEvent, body: str) -> dict:
        if event.kind == "review_comment":
            return self._github.reply_to_review_comment(
                event.owner, event.repo, event.pr_number,
                comment_id=event.in_reply_to_id or event.comment_id,
                body=body,
            )
        return self._github.post_review_comment(
            event.owner, event.repo, event.pr_number, body=body
        )

    def _call_claude(self, system: str, user: str) -> str:
        from oneport.llm import call_gemini, is_gemini_model

        if is_gemini_model(self.config.model):
            text, _ = call_gemini(
                model=self.config.model,
                api_key=self.config.api_key,
                system=system,
                user=user,
                max_tokens=self.config.max_tokens,
            )
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
            return message.content[0].text
        except anthropic.AuthenticationError as exc:
            raise AuthError("Invalid Anthropic API key.") from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError("Anthropic API rate limit hit. Try again shortly.") from exc


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _strip_trigger(body: str, trigger: str) -> str:
    """Remove the trigger mention so the model sees a clean instruction."""
    return re.sub(re.escape(trigger), "", body, flags=re.IGNORECASE).strip()


def _extract_remember(instruction: str) -> str | None:
    """Return the guideline text if the instruction is a remember command."""
    match = _REMEMBER_RE.match(instruction.strip())
    if not match:
        return None
    rule = match.group("rule").strip()
    return rule or None


def load_event_file(path: str) -> dict:
    """Read the GitHub Actions event payload (usually $GITHUB_EVENT_PATH)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
