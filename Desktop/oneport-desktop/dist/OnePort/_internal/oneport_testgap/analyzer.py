"""
The analyze pipeline — deterministic core first, LLM judgment second.

    diff  ──► changed lines ─┐
                             ├──► gaps (changed ∩ uncovered) ──► LLM rank ──► report
    pytest --cov ► coverage ─┘                                   LLM generate+verify

Coverage facts NEVER come from the model. The model only ranks proven gaps
and writes candidate tests, which are then executed before anyone sees them.
"""

from __future__ import annotations

from pathlib import Path

from oneport_testgap.config import Config
from oneport_testgap.coverage_utils import parse_coverage_xml, run_coverage
from oneport_testgap.diff_utils import added_lines
from oneport_testgap.exceptions import NothingToAnalyze
from oneport_testgap.gaps import GapReport, build_gaps
from oneport_testgap.generator import generate_tests
from oneport_testgap.guidelines import load_guidelines
from oneport_testgap.ranker import rank_gaps
from oneport_testgap.targets import ResolvedTarget, resolve_target


class Analyzer:
    def __init__(self, config: Config, repo_root: str | Path | None = None) -> None:
        self.config = config
        self.repo_root = Path(repo_root or Path.cwd()).resolve()

    def analyze(
        self,
        target: str,
        coverage_file: str | None = None,
        generate: int = 0,
    ) -> tuple[GapReport, ResolvedTarget]:
        """
        Run the full pipeline. Raises DiffError when there's nothing to analyze
        (the CLI turns "no Python changes" into a friendly no-op instead).
        """
        resolved = resolve_target(target, repo_path=self.repo_root)

        changed = added_lines(resolved.diff)
        py_changed = {
            path: lines for path, lines in changed.items() if path.endswith(".py")
        }
        if not py_changed:
            raise NothingToAnalyze(
                "No changed Python lines in this diff — nothing to analyze."
            )

        # Deterministic core: real coverage from pytest-cov (or a provided XML).
        xml_path = Path(coverage_file) if coverage_file else run_coverage(
            self.repo_root,
            pytest_args=self.config.coverage.pytest_args,
            timeout=self.config.coverage.timeout,
        )
        coverage = parse_coverage_xml(xml_path, self.repo_root)

        gaps, stats = build_gaps(
            py_changed, coverage, self.repo_root, self.config.ignore_paths
        )

        report = GapReport(
            target=resolved.label,
            model=self.config.model,
            gaps=gaps,
            pr_ref=resolved.pr_ref,
            **stats,
        )
        if not gaps:
            return report, resolved

        # LLM judgment on top: risk ranking, then (optionally) verified tests.
        guidelines = load_guidelines(self.repo_root / self.config.guidelines_path)
        report.gaps, rank_tokens = rank_gaps(gaps, self.config, guidelines)
        report.total_tokens += rank_tokens

        if generate > 0:
            report.generated, gen_tokens = generate_tests(
                report.gaps, generate, self.config, self.repo_root, guidelines
            )
            report.total_tokens += gen_tokens

        return report, resolved
