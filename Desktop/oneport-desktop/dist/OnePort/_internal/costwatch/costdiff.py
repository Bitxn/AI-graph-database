"""
PR cost-diff — the gate hook behind `analyze --post`.

Given a GitHub PR, fetch each changed IaC file at both the base and head
revisions, price both deterministically, and compute the extra provisioned
cost the PR introduces. When that increase clears the configured threshold, run
the LLM waste pass on the head revision and post a sticky comment:

  "this PR adds ~$340/mo — here's why + a cheaper config".

Everything is read-only except the single comment upsert.
"""

from __future__ import annotations

from dataclasses import dataclass

from costwatch.analyzer import Analyzer
from costwatch.integrations.github import GitHubIntegration, PullRequest
from costwatch.parsers import is_iac_filename, parse_source_text
from costwatch.result import CostReport, Resource


@dataclass
class CostDiff:
    base_cost: float
    head_cost: float
    report: CostReport   # LLM findings on the head revision (cost_delta set)

    @property
    def delta(self) -> float:
        return self.head_cost - self.base_cost

    @property
    def increased(self) -> bool:
        return self.delta > 0


def _price_only(resources: list[Resource]) -> float:
    return sum(r.total_monthly_cost for r in resources)


def compute_pr_cost_diff(
    analyzer: Analyzer,
    integration: GitHubIntegration,
    pr: PullRequest,
) -> CostDiff:
    """Build the base-vs-head cost diff and (LLM) findings for a PR's IaC changes."""
    iac_files = [f for f in pr.changed_files if is_iac_filename(f)]

    base_resources: list[Resource] = []
    head_resources: list[Resource] = []
    for path in iac_files:
        base_text = integration.get_file(pr.owner, pr.repo, path, pr.base_sha)
        head_text = integration.get_file(pr.owner, pr.repo, path, pr.head_sha)
        if base_text is not None:
            base_resources.extend(parse_source_text(path, base_text))
        if head_text is not None:
            head_resources.extend(parse_source_text(path, head_text))

    base_cost = _price_only(base_resources)
    head_cost = _price_only(head_resources)

    context = (
        f"This is a pull request. Base provisioned cost ~${base_cost:.0f}/mo, "
        f"head ~${head_cost:.0f}/mo (delta ~${head_cost - base_cost:+.0f}/mo). "
        f"Focus findings on the resources this PR adds or enlarges."
    )
    report = analyzer.analyze_resources(head_resources, context=context)
    report.path = f"PR #{pr.number}"
    report.cost_delta = round(head_cost - base_cost, 2)
    return CostDiff(base_cost=base_cost, head_cost=head_cost, report=report)
