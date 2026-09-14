"""
Orchestration: scan (detect) → optional plan (LLM) and apply (codemod) → optional
verify (pytest before/after). Detection and transforms are deterministic; only the
plan uses a model.
"""

from __future__ import annotations

import time
from pathlib import Path

from oneport_upgrade import codemod, planner
from oneport_upgrade.config import Config
from oneport_upgrade.guidelines import load_guidelines
from oneport_upgrade.result import UpgradeReport
from oneport_upgrade.rules import get_migration
from oneport_upgrade.scanner import scan_repo
from oneport_upgrade.verifier import verify as run_verify


class Upgrader:
    def __init__(self, config: Config, root: str | Path = ".") -> None:
        self.config = config
        self.root = Path(root)

    def _waive(self, findings) -> None:
        """Apply team waivers (.oneport/upgrade-waivers.yml) in place, from the repo root."""
        from oneport_upgrade.waivers import apply_waivers, load_waivers
        apply_waivers(findings, load_waivers(self.root))

    def scan(self, target: str, use_llm: bool = True, plan: bool = False) -> UpgradeReport:
        start = time.monotonic()
        migration = get_migration(target)
        findings, scanned = scan_repo(self.root, migration)
        self._waive(findings)
        report = UpgradeReport(migration=migration.id, findings=findings,
                              files_scanned=scanned, model=self.config.model)
        if plan and use_llm and self.config.has_key and findings:
            steps, tokens = planner.make_plan(
                migration.id, findings, self.config, load_guidelines(self.config.guidelines_path))
            report.plan = steps
            report.total_tokens += tokens
        elif plan and not self.config.has_key:
            report.notes.append(
                "Not logged in to Oneport — skipped the written plan (the scan above is "
                "complete). Run `oneport-account login <token>`; free token at "
                "https://oneport.dev."
            )
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report

    def apply(self, target: str, dry_run: bool = False, do_verify: bool = False) -> UpgradeReport:
        start = time.monotonic()
        migration = get_migration(target)
        findings, scanned = scan_repo(self.root, migration)
        self._waive(findings)
        report = UpgradeReport(migration=migration.id, findings=findings,
                              files_scanned=scanned, model=self.config.model)

        # A waived finding is left as-is: it must not be auto-rewritten.
        fixable = [f for f in findings if not f.waived]
        auto = [f for f in fixable if f.auto]
        if not auto:
            waived_note = (f" ({len(report.waived)} waived)" if report.waived else "")
            report.notes.append(
                f"No auto-fixable findings — the rest need manual changes.{waived_note}")
            report.elapsed_ms = int((time.monotonic() - start) * 1000)
            return report

        def _do_apply() -> None:
            codemod.apply_fixes(self.root, fixable, migration, dry_run=dry_run)

        if do_verify and not dry_run:
            report.verify = run_verify(self.root, _do_apply, timeout=self.config.verify_timeout)
        else:
            _do_apply()

        if dry_run:
            report.notes.append(f"Dry run — {len(report.applied)} fix(es) would be applied, "
                               "nothing written.")
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        return report
