"""
Core check orchestrator.

Checker.check() is the single entry point:
  1. Resolve target → migration files (path, dir, --staged, --head, PR URL)
  2. Parse each file into normalized operations
  3. Run the deterministic rule engine (this alone decides WHAT is dangerous)
  4. If enabled and there are findings, run the LLM blast-radius layer
  5. Recompute blocking from the FULL finding set → CheckResult
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from oneport_migrate.advisor import Advisor
from oneport_migrate.config import Config, load_config
from oneport_migrate.exceptions import OneportMigrateError, ParseError
from oneport_migrate.guidelines import load_guidelines
from oneport_migrate.integrations.github import GitHubIntegration, looks_like_migration_path
from oneport_migrate.integrations import local_git
from oneport_migrate.operations import ParsedMigration
from oneport_migrate.parsers import detect_framework, parse_migration
from oneport_migrate.result import CheckResult, compute_blocking
from oneport_migrate.rules.engine import run_rules
from oneport_migrate.rules.loader import load_rule_set

# Directories never scanned for migrations when a directory target is given.
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "site-packages"}


class Checker:
    def __init__(self, config: Config | None = None, config_path: str | None = None) -> None:
        self.config = config or load_config(config_path=config_path)
        self._rule_set = load_rule_set(
            ignored_ids=self.config.rules.ignore,
            severity_overrides=self.config.rules.severity,
        )
        self._guidelines = load_guidelines(self.config.guidelines_path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self, target: str, use_llm: bool = True) -> CheckResult:
        """
        Check a target and return a CheckResult.

        target may be:
          - a migration file path                ("app/migrations/0002_x.py")
          - a directory to scan                  ("app/migrations/")
          - "--staged"                           (staged migration files)
          - "--head"                             (last commit's migration files)
          - a GitHub PR URL                      ("https://github.com/o/r/pull/1")
        """
        start = time.monotonic()

        files, meta = self._resolve_target(target)
        migrations, notes = self._parse_files(files)

        findings = run_rules(migrations, self._rule_set, db=self.config.db)
        for m in migrations:
            notes.extend(m.notes)

        # Approved waivers keep findings in the report but exclude them from the gate.
        from oneport_migrate.waivers import apply_waivers, load_waivers
        waived = apply_waivers(findings, load_waivers("."))
        if waived:
            notes.append(f"{waived} finding(s) waived via .oneport/migrate-waivers.yml "
                         "— shown but not blocking.")

        result = CheckResult(
            findings=findings,
            target=target,
            files_checked=[m.file for m in migrations],
            db=self.config.db,
            notes=notes,
            diff=meta.get("diff", ""),
            pr_ref=meta.get("pr_ref"),
        )

        from oneport_account import is_logged_in

        llm_wanted = use_llm and self.config.llm.enabled
        if llm_wanted and not is_logged_in():
            result.llm_note = (
                "LLM layer skipped: not logged in to Oneport. Run "
                "`oneport-account login <token>` (free token at "
                "https://oneport.dev) for blast-radius judgment."
            )
        elif llm_wanted and findings and migrations:
            advisor = Advisor(self.config, guidelines=self._guidelines)
            assessment = advisor.assess(findings, migrations)
            result.model = self.config.model
            result.verdict = assessment.verdict
            result.rewrite_plan = assessment.rewrite_plan
            result.llm_ok = assessment.ok
            result.llm_note = assessment.note
            result.total_tokens = assessment.total_tokens
        elif llm_wanted and not findings:
            # Clean migrations don't need judgment — no API call, no tokens.
            result.llm_note = ""

        # Blocking is computed on the FULL finding set (waived findings excluded),
        # at the configured threshold, before any display filter.
        result.blocking = compute_blocking(findings, self.config.fail_on)
        result.elapsed_ms = int((time.monotonic() - start) * 1000)
        return result

    def post_github_review(self, result: CheckResult) -> dict | None:
        """
        Post `result` as an inline PR review. Returns the created review, or
        None when this head SHA was already reviewed (marker match — re-run
        with nothing new to say).
        """
        if not result.pr_ref:
            raise ValueError("post_github_review requires a result from a PR check.")

        from oneport_migrate.formatters.github_fmt import build_pr_review
        from oneport_migrate.markers import extract_reviewed_sha

        integration = GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))
        owner = result.pr_ref["owner"]
        repo = result.pr_ref["repo"]
        number = result.pr_ref["number"]
        head_sha = result.pr_ref.get("head_sha", "")

        if head_sha:
            try:
                for review in reversed(integration.list_reviews(owner, repo, number)):
                    sha = extract_reviewed_sha(review.get("body") or "")
                    if sha:
                        if sha == head_sha:
                            return None  # already reviewed this revision
                        break
            except OneportMigrateError:
                pass  # can't read reviews — post anyway

        payload = build_pr_review(result, result.diff)
        return integration.post_review(
            owner=owner, repo=repo, pr_number=number,
            body=payload["body"], comments=payload["comments"], event=payload["event"],
        )

    # ── Target resolution ──────────────────────────────────────────────────────

    def _resolve_target(self, target: str) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        """Returns ([(path, content), ...], meta)."""
        if target == "--staged":
            return self._from_local_paths(local_git.staged_files()), {}

        if target == "--head":
            return self._from_local_paths(local_git.head_files()), {}

        if "github.com" in target and "/pull/" in target:
            from oneport_migrate.integrations.github import parse_pr_url
            owner, repo, number = parse_pr_url(target)
            integration = GitHubIntegration(token=os.getenv("GITHUB_TOKEN", ""))
            pr = integration.get_pr_migrations(target)
            meta = {
                "diff": pr.diff,
                "pr_ref": {"owner": owner, "repo": repo, "number": number,
                           "head_sha": pr.head_sha},
            }
            return pr.migration_files, meta

        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f"Path not found: {target}")

        if path.is_dir():
            candidates: list[Path] = []
            for pattern in ("*.py", "*.sql"):
                for p in sorted(path.rglob(pattern)):
                    if not any(part in _SKIP_DIRS for part in p.parts):
                        candidates.append(p)
            return self._read_files([str(p) for p in candidates]), {}

        content = path.read_text(encoding="utf-8", errors="replace")
        if detect_framework(str(path), content) is None:
            raise ParseError(
                f"{target} is not recognisable as a Django, Alembic, or SQL migration.",
                path=target,
            )
        return [(str(path), content)], {}

    def _from_local_paths(self, paths: list[str]) -> list[tuple[str, str]]:
        migration_like = [p for p in paths if looks_like_migration_path(p) or p.endswith(".sql")]
        # Also consider any changed .py whose content says it's a migration —
        # not every project keeps them under migrations/.
        others = [p for p in paths if p.endswith(".py") and p not in migration_like]
        return self._read_files(migration_like + others)

    def _read_files(self, paths: list[str]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for p in paths:
            path = Path(p)
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if detect_framework(p, content) is not None:
                out.append((p, content))
        return out

    # ── Parsing ────────────────────────────────────────────────────────────────

    def _parse_files(
        self, files: list[tuple[str, str]]
    ) -> tuple[list[ParsedMigration], list[str]]:
        migrations: list[ParsedMigration] = []
        notes: list[str] = []
        for path, content in files:
            # Django's initial 0001 files and Alembic env.py aren't worth
            # gating; but we keep it simple: parse everything detected.
            parsed = parse_migration(path, content)
            if parsed is not None:
                migrations.append(parsed)
        return migrations, notes
