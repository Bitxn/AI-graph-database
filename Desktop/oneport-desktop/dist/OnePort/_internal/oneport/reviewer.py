"""
Core review orchestrator.

Reviewer.review() is the single entry point for all review operations.
It decides how to get the diff (local file, git, platform API), builds the
prompt, calls Claude, parses the result, and writes to/reads from the cache.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# Optional legacy path: reviews run through the managed Gemini proxy by
# default; the Anthropic SDK is only needed if someone configures a Claude
# model with their own key. Keeping it optional keeps the wheel light and a
# clean `pip install oneport-review` free of an unused ML SDK.
try:
    import anthropic
except ImportError:  # pragma: no cover — exercised in clean installs
    anthropic = None  # type: ignore[assignment]

from oneport.cache import ReviewCache
from oneport.chunker import chunk_diff
from oneport.config import Config, load_config
from oneport.exceptions import AuthError, IntegrationError, ParseError, RateLimitError
from oneport.markers import extract_reviewed_sha
from oneport.integrations.github import GitHubIntegration, parse_pr_url
from oneport.integrations.gitlab import GitLabIntegration
from oneport.integrations.bitbucket import BitbucketIntegration
from oneport.integrations.local_diff import LocalDiffIntegration
from oneport.prompt_builder import build_prompt, build_pr_prompt
from oneport.result import Issue, Location, ReviewResult, Severity, compute_blocking
from oneport.rules.loader import load_rule_set
from oneport.waivers import apply_waivers, load_waivers


def _parse_issues_json(raw: str) -> dict:
    """Strip optional markdown fences and parse the model's JSON envelope.

    Callers expect an ``{"issues": [...]}`` object, but models sometimes return a
    bare ``[...]`` array (or wrap the list under a different key). Normalize all of
    those into the envelope shape so a valid-but-differently-shaped response never
    crashes the gate with AttributeError.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError("Claude returned non-JSON output", raw_response=raw) from exc

    if isinstance(data, list):
        return {"issues": data}
    if isinstance(data, dict):
        if "issues" in data:
            return data
        # Model used a synonym key (findings/results/comments) for the list.
        for key in ("findings", "results", "comments", "reviews"):
            if isinstance(data.get(key), list):
                return {"issues": data[key]}
        return data  # no recognizable list → callers' .get("issues", []) yields []
    # Any other JSON scalar (str/int/None): no issues.
    return {"issues": []}


_ASSERT_LINE_RE = re.compile(r"^\s*assert\b")
_HUNK_RE = re.compile(r"^@@[^+]*\+(\d+)")


def _new_line_lookup(text: str) -> dict[tuple[str | None, int], str]:
    """Map (file, new-line-number) → source line, from plain file content OR a
    unified diff. Plain content is keyed (None, n). Diff entries are keyed by
    the +++ path (a/ b/ prefixes stripped); when the diff touches exactly one
    file, (None, n) fallback keys are added so a model-echoed path variant
    can't break the lookup."""
    if not text:
        return {}
    lines = text.splitlines()
    is_diff = text.startswith(("diff ", "--- ", "+++ ", "@@")) or any(
        l.startswith("@@") for l in lines[:400])
    out: dict[tuple[str | None, int], str] = {}
    if not is_diff:
        for i, l in enumerate(lines, start=1):
            out[(None, i)] = l
        return out

    current: str | None = None
    files_seen: set[str] = set()
    new_ln = 0
    in_hunk = False
    for l in lines:
        if l.startswith("+++ "):
            current = l[4:].strip()
            if current.startswith(("a/", "b/")):
                current = current[2:]
            files_seen.add(current)
            in_hunk = False
            continue
        m = _HUNK_RE.match(l)
        if m:
            new_ln = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if l.startswith("+"):
            out[(current, new_ln)] = l[1:]
            new_ln += 1
        elif l.startswith(" ") or l == "":
            out[(current, new_ln)] = l[1:] if l.startswith(" ") else ""
            new_ln += 1
        elif not l.startswith("-"):
            in_hunk = False  # next file's meta lines
    if len(files_seen) == 1:
        for (_, n), v in list(out.items()):
            out[(None, n)] = v
    return out


def _calibrate_assert_findings(issues: list, source_text: str = "") -> None:
    """Deterministic severity calibration for `assert`-statement findings.

    On scrapy the model flagged six `assert x` lines as ERROR under "Missing
    null/None check" — two of which literally WERE None-checks (`assert x is
    not None`). The real concern with asserts (stripped under `python -O`) is a
    robustness/style issue, not a ship-blocking defect — and ERROR blocks
    `op ship`. A CI gate that fails a routine diff over asserts is a gate teams
    turn off, so this guard runs regardless of what the model said:

      * assert-line findings are capped at WARNING (never blocking), and
      * the mislabel is corrected to name the actual concern.

    The check reads the ACTUAL source line (from the reviewed content), not the
    model's snippet echo — live, the model returned empty snippets and a
    snippet-only guard silently missed all six. Deterministic on purpose:
    prompt guidance reduces this at the source, but a guard the model can't
    override is what protects trust in the gate.
    """
    lookup = _new_line_lookup(source_text)
    for issue in issues:
        line_text = (lookup.get((issue.file, issue.line), "")
                     or lookup.get((None, issue.line), "")
                     or issue.snippet or "")
        if issue.severity.rank >= 2 and _ASSERT_LINE_RE.match(line_text):
            issue.severity = Severity.WARNING
            low = issue.message.lower()
            if "null" in low or "none check" in low:
                issue.message = (
                    "assert-based guard is stripped under `python -O` — use an "
                    "explicit check that raises if this must hold in production"
                )


class Reviewer:
    """
    Orchestrates a full review lifecycle:
      1. Resolve target → diff text
      2. Check cache
      3. Build prompt
      4. Call Claude
      5. Parse response → ReviewResult
      6. Write cache
    """

    def __init__(self, config: Config | None = None, config_path: str | None = None) -> None:
        self.config = config or load_config(config_path=config_path)
        self._client = self._make_client()
        self._cache = ReviewCache(enabled=self.config.cache.enabled, ttl=self.config.cache.ttl)
        self._rule_set = load_rule_set(
            ignored_ids=self.config.rules.ignore,
            severity_overrides=self.config.rules.severity,
        )
        # rule_id → Rule, so a finding that cites a catalog rule but omits the
        # message (the model does this) can be backfilled from the rule's own
        # description instead of surfacing as "(no message)".
        self._rules_by_id = {r.id: r for r in self._rule_set.all_rules}
        from oneport.guidelines import load_guidelines
        self._guidelines = load_guidelines(self.config.guidelines_path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def review(self, target: str, min_severity: str = "warning", full: bool = False) -> ReviewResult:
        """
        Review a target and return a ReviewResult.

        target may be:
          - A file path            ("src/auth.py")
          - A GitHub PR URL        ("https://github.com/org/repo/pull/42")
          - A GitLab MR URL        ("https://gitlab.com/org/repo/-/merge_requests/7")
          - A Bitbucket PR URL     ("https://bitbucket.org/org/repo/pull-requests/3")
          - "--staged"             (git diff --cached)
          - "--head"               (git diff HEAD~1)

        For GitHub PRs that Oneport has reviewed before (via --post), only the
        commits pushed since the last review are reviewed — pass full=True to
        force a from-scratch review of the whole PR.
        """
        start = time.monotonic()

        diff, meta = self._resolve_target(target, full=full)

        # Apply built-in + .oneportrc ignore_paths to multi-file diffs (PRs,
        # --staged, --head). An explicitly named single file is reviewed regardless
        # — the user asked for that file by name.
        #
        # The built-ins apply even with no ignore_paths configured: the default
        # config is exactly where an unconfigured user gets ship-report.json (or a
        # lockfile) reviewed as if it were code.
        if meta.get("is_pr") or meta.get("is_diff"):
            from oneport.diff_utils import DEFAULT_IGNORE_PATTERNS, filter_diff
            patterns = DEFAULT_IGNORE_PATTERNS + list(self.config.ignore_paths or [])
            diff, skipped = filter_diff(diff, patterns)
            meta["skipped_paths"] = skipped
            if not diff.strip():
                # Every file was ignored — no API call. `skipped_paths` MUST ride
                # along: this result is "nothing was reviewed", not "reviewed and
                # clean", and the caller can only tell the difference from it.
                result = ReviewResult(
                    issues=[], target=meta.get("file_name", ""), model=self.config.model,
                    blocking=False, diff="", pr_ref=meta.get("pr_ref"),
                    skipped_paths=skipped,
                )
                result.elapsed_ms = int((time.monotonic() - start) * 1000)
                return result

        # Static-analyzer hints for local reviews (files exist on disk here).
        if self.config.analyzers and not meta.get("is_pr"):
            meta["analyzer_hints"] = self._analyzer_hints(target, diff, meta)

        cache_key = self._cache.make_key(diff, self.config.model, prompt_context=self._guidelines)

        # Cache hit
        if cached := self._cache.get(cache_key):
            result = self._parse_claude_response(cached, meta, from_cache=True, diff=diff)
            result.elapsed_ms = int((time.monotonic() - start) * 1000)
            return result.filter(min_severity)

        # Big diffs are split on file boundaries into per-chunk model calls and
        # merged — a 5,000-line PR gets N thorough reviews, not one shallow one.
        # (Non-diff content and small diffs come back as a single chunk.)
        chunks = chunk_diff(diff, self.config.max_chunk_chars)

        # Full-file context: only when the diff fits one call — for chunked
        # (huge) diffs the budget is better spent on the diff itself.
        if (
            len(chunks) == 1
            and self.config.full_file_context
            and (meta.get("is_pr") or meta.get("is_diff"))
        ):
            meta["file_context"] = self._file_context(diff, meta)

        raws: list[str] = []
        total_tokens = 0
        for chunk in chunks:
            prompt = self._build_prompt_for(chunk, meta)
            raws.append(self._call_claude(prompt.system, prompt.user))
            total_tokens += getattr(self, "_last_tokens", 0)
        self._last_tokens = total_tokens

        if len(raws) == 1:
            raw = raws[0]
        else:
            merged: list = []
            for raw_chunk in raws:
                merged.extend(_parse_issues_json(raw_chunk).get("issues", []))
            raw = json.dumps({"issues": merged})

        self._cache.set(cache_key, raw)

        result = self._parse_claude_response(raw, meta, from_cache=False, diff=diff)
        result.elapsed_ms = int((time.monotonic() - start) * 1000)
        return result.filter(min_severity)

    def post_github_review(self, result: ReviewResult) -> dict:
        """
        Post `result` as an inline PR review (per-line comments where possible).

        Requires `result.pr_ref` to be set — i.e. the reviewed target was a
        GitHub PR URL. Uses `result.diff` (carried on the result since it was
        first fetched) rather than re-fetching, so this never makes an extra
        GitHub API call beyond the review post itself.
        """
        if not result.pr_ref:
            raise ValueError("post_github_review requires a result from a GitHub PR review.")

        from oneport.formatters.github_fmt import build_pr_review  # lazy: avoids a circular
        # import at module load time (formatters/__init__ -> sarif.py -> oneport.__version__
        # -> oneport/__init__.py, which itself imports Reviewer from this module).

        payload = build_pr_review(result, result.diff)
        integration = GitHubIntegration(token=self._get_github_token())
        return integration.post_review(
            owner=result.pr_ref["owner"],
            repo=result.pr_ref["repo"],
            pr_number=result.pr_ref["number"],
            body=payload["body"],
            comments=payload["comments"],
            event=payload["event"],
        )

    def _build_prompt_for(self, diff: str, meta: dict[str, Any]):
        extra = "\n\n".join(
            section for section in
            (meta.get("analyzer_hints", ""), meta.get("file_context", ""))
            if section
        )
        if meta.get("is_pr"):
            return build_pr_prompt(
                diff=diff,
                title=meta.get("title", ""),
                description=meta.get("description", ""),
                rule_set=self._rule_set,
                guidelines=self._guidelines,
                extra_context=extra,
            )
        return build_prompt(
            diff=diff,
            file_name=meta.get("file_name", ""),
            rule_set=self._rule_set,
            guidelines=self._guidelines,
            extra_context=extra,
        )

    def _file_context(self, diff: str, meta: dict[str, Any]) -> str:
        """Line-numbered full contents of changed files, under a char budget.

        Local diffs read from disk; PR diffs fetch each file at the PR's head
        SHA via the contents API (capped at 10 files). Numbered lines double as
        an anchor that keeps the model's reported line numbers honest.
        """
        from oneport.diff_utils import split_diff_by_file

        paths = [s.path for s in split_diff_by_file(diff)][:10]
        if not paths:
            return ""

        pr_ref = meta.get("pr_ref") or {}
        integration = None
        if meta.get("is_pr"):
            if not pr_ref.get("head_sha"):
                return ""
            integration = GitHubIntegration(token=self._get_github_token())

        budget = self.config.max_context_chars
        sections: list[str] = []
        for path in paths:
            content = self._read_context_file(path, integration, pr_ref)
            if content is None:
                continue
            numbered = "\n".join(
                f"{i}: {line}" for i, line in enumerate(content.splitlines(), 1)
            )
            block = f"=== {path} ===\n{numbered}"
            if len(block) > budget:
                continue  # skip oversized file, keep budget for the rest
            budget -= len(block)
            sections.append(block)

        if not sections:
            return ""
        return (
            "Full current content of changed files, line-numbered, for context. "
            "Review ONLY the changes shown in the diff, but use this to understand "
            "surrounding code and to report exact line numbers:\n\n"
            + "\n\n".join(sections)
        )

    def _read_context_file(
        self, path: str, integration: GitHubIntegration | None, pr_ref: dict
    ) -> str | None:
        if integration is not None:
            try:
                found = integration.get_file(
                    pr_ref["owner"], pr_ref["repo"], path, ref=pr_ref["head_sha"]
                )
                return found[0] if found else None
            except IntegrationError:
                return None
        p = Path(path)
        if not p.is_file():
            return None  # deleted in this diff, or path outside the working tree
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _analyzer_hints(self, target: str, diff: str, meta: dict[str, Any]) -> str:
        from oneport.analyzers import format_findings_for_prompt, run_analyzers

        if meta.get("is_diff"):
            from oneport.diff_utils import split_diff_by_file
            paths = [s.path for s in split_diff_by_file(diff)]
        elif meta.get("file_name"):
            paths = [meta["file_name"]]
        else:
            return ""
        return format_findings_for_prompt(run_analyzers(paths))

    # ── Target resolution ──────────────────────────────────────────────────────

    def _resolve_target(self, target: str, full: bool = False) -> tuple[str, dict[str, Any]]:
        """
        Returns (diff_text, metadata_dict).
        """
        if target == "--staged":
            diff = LocalDiffIntegration().get_staged_diff()
            return diff, {"is_diff": True}

        if target == "--head":
            diff = LocalDiffIntegration().get_head_diff()
            return diff, {"is_diff": True}

        if "github.com" in target and "/pull/" in target:
            owner, repo, number = parse_pr_url(target)
            integration = GitHubIntegration(token=self._get_github_token())
            pr = integration.get_pr(target)
            meta: dict[str, Any] = {
                "is_pr": True,
                "title": pr.title,
                "description": pr.body,
                "file_name": "",
                "pr_ref": {
                    "owner": owner, "repo": repo, "number": number,
                    "head_sha": pr.head_sha,
                },
            }

            diff = pr.diff
            if not full and pr.head_sha:
                last_sha = self._find_last_reviewed_sha(integration, owner, repo, number)
                if last_sha and last_sha != pr.head_sha:
                    try:
                        incremental = integration.get_compare_diff(
                            owner, repo, last_sha, pr.head_sha
                        )
                        if incremental.strip():
                            diff = incremental
                            meta["incremental_from"] = last_sha
                    except IntegrationError:
                        # Force-push or unreachable base — the compare 404s.
                        # Reviewing the full PR again is always a safe fallback.
                        pass
            return diff, meta

        if "gitlab.com" in target and "/merge_requests/" in target:
            integration = GitLabIntegration(token=self._get_gitlab_token())
            mr = integration.get_mr(target)
            return mr.diff, {"is_pr": True, "title": mr.title, "description": mr.description}

        if "bitbucket.org" in target and "/pull-requests/" in target:
            integration = BitbucketIntegration(
                username=self._get_bitbucket_username(),
                app_password=self._get_bitbucket_token(),
            )
            pr = integration.get_pr(target)
            return pr.diff, {"is_pr": True, "title": pr.title, "description": pr.description}

        # Treat as local file path
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {target}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return content, {"file_name": str(path)}

    def _find_last_reviewed_sha(
        self, integration: GitHubIntegration, owner: str, repo: str, number: int
    ) -> str | None:
        """Latest reviewed-SHA marker left by a previous Oneport review, or None."""
        try:
            reviews = integration.list_reviews(owner, repo, number)
        except IntegrationError:
            return None  # can't read reviews (rate limit, perms) → full review
        for review in reversed(reviews):  # API returns chronological ascending
            sha = extract_reviewed_sha(review.get("body") or "")
            if sha:
                return sha
        return None

    # ── Claude call ────────────────────────────────────────────────────────────

    def _call_claude(self, system: str, user: str) -> str:
        from oneport.llm import call_gemini, is_gemini_model

        if is_gemini_model(self.config.model):
            text, tokens = call_gemini(
                model=self.config.model,
                api_key=self.config.api_key,
                system=system,
                user=user,
                max_tokens=self.config.max_tokens,
                response_json=True,   # findings are a JSON envelope
            )
            self._last_tokens = tokens
            return text

        if self._client is None:
            raise AuthError(
                f"Model '{self.config.model}' needs the optional Anthropic SDK "
                "(pip install anthropic) plus an API key — or use the default "
                "managed model (gemini-flash-latest) with `oneport-account login`."
            )
        try:
            message = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            # Total token tracking
            self._last_tokens = message.usage.input_tokens + message.usage.output_tokens
            return message.content[0].text
        except anthropic.AuthenticationError as exc:
            raise AuthError("Invalid Anthropic API key.") from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError("Anthropic API rate limit hit. Try again shortly.") from exc

    # ── Response parsing ───────────────────────────────────────────────────────

    def _parse_claude_response(
        self,
        raw: str,
        meta: dict[str, Any],
        from_cache: bool,
        diff: str = "",
    ) -> ReviewResult:
        data = _parse_issues_json(raw)

        issues: list[Issue] = []
        for item in data.get("issues", []):
            # Apply severity overrides from config
            severity_str = item.get("severity", "warning")
            rule_id = item.get("rule_id", "OPR000")
            if rule_id in self._rule_set.severity_overrides:
                severity_str = self._rule_set.severity_overrides[rule_id]

            try:
                severity = Severity(severity_str)
            except ValueError:
                severity = Severity.WARNING

            location = Location(
                file=item.get("file", meta.get("file_name", "unknown")),
                line=int(item.get("line", 0)),
                column=int(item.get("column", 0)),
                end_line=item.get("end_line"),
                end_column=item.get("end_column"),
            )

            # Backfill from the rule catalog when the model gives a valid rule_id
            # but an empty message/category (it did exactly this on hono:
            # OPR024 with everything blank → the gate printed "(no message)").
            rule = self._rules_by_id.get(rule_id)
            message = (item.get("message") or "").strip()
            if not message and rule is not None:
                message = rule.description
            category = item.get("category", "") or (rule.category if rule else "")

            # A finding with no message after backfill is unactionable noise —
            # the model hallucinated a location with nothing to say. Drop it
            # rather than surface a blank warning that fails the eye test.
            if not message:
                continue

            issues.append(Issue(
                rule_id=rule_id,
                severity=severity,
                message=message,
                suggestion=item.get("suggestion", ""),
                location=location,
                snippet=item.get("snippet", ""),
                category=category,
                fix=item.get("fix", "") or "",
            ))

        _calibrate_assert_findings(issues, source_text=diff)

        # Sort: critical first, then by file + line
        issues.sort(key=lambda i: (-i.severity.rank, i.file, i.line))

        # Apply team waivers (.oneport/review-waivers.yml): a waived finding is
        # still shown and still travels into SARIF as a suppression, but is
        # excluded from the blocking decision below. Loaded from cwd — the repo
        # root the reviewer is invoked in.
        apply_waivers(issues, load_waivers("."))

        # Computed on the FULL issue set, before any --min-severity display filter is
        # applied in review() — a display filter must never hide a blocking issue from
        # CI. Threshold is config.fail_on (default error); waived issues never block.
        blocking = compute_blocking(issues, self.config.fail_on)

        return ReviewResult(
            issues=issues,
            target=meta.get("file_name", ""),
            model=self.config.model,
            cached=from_cache,
            total_tokens=getattr(self, "_last_tokens", 0),
            blocking=blocking,
            diff=diff,
            pr_ref=meta.get("pr_ref"),
            incremental_from=meta.get("incremental_from", ""),
            skipped_paths=meta.get("skipped_paths", []) or [],
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_client(self):
        # The default model is a Gemini model, so reviews go through the Oneport
        # managed proxy (see llm.call_gemini) and this Anthropic client is never
        # called. None when the optional SDK isn't installed — the Claude path
        # raises a clear AuthError instead. A placeholder key keeps construction
        # from raising on the empty api_key that no-BYOK leaves behind.
        if anthropic is None:
            return None
        return anthropic.Anthropic(api_key=self.config.api_key or "unused-managed-path")

    def _get_github_token(self) -> str:
        import os
        return os.getenv("GITHUB_TOKEN", "")

    def _get_gitlab_token(self) -> str:
        import os
        return os.getenv("GITLAB_TOKEN", "")

    def _get_bitbucket_username(self) -> str:
        import os
        return os.getenv("BITBUCKET_USERNAME", "")

    def _get_bitbucket_token(self) -> str:
        import os
        return os.getenv("BITBUCKET_APP_PASSWORD", "")
