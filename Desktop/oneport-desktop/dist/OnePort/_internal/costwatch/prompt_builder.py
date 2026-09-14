"""
Builds the system prompt and user message for a waste-detection call.

The deterministic layer has already parsed the IaC and attached approximate
monthly costs; this module renders that inventory for the model and asks it for
the judgment calls a price table can't make — what's over-provisioned, what
could be serverless/spot, what's an oversized volume, what's missing
autoscaling, what's a dev box with no shutdown schedule.
"""

from __future__ import annotations

from dataclasses import dataclass

from costwatch.result import Resource

SYSTEM_PROMPT = ""
# NOTE: the tuned system prompt for this call lives SERVER-SIDE in the
# Oneport prompt vault, keyed by tool:action — the proxy injects it at
# call time and ignores this placeholder. The prompt is intentionally
# not shipped in this package.



@dataclass
class PromptParts:
    system: str
    user: str


def _render_resource(r: Resource) -> str:
    cost = (
        f"~${r.total_monthly_cost:.0f}/mo"
        if r.monthly_cost is not None
        else "cost: unknown (not in price table)"
    )
    size = f" size={r.size}" if r.size else ""
    count = f" count={r.count}" if r.count != 1 else ""
    # Only the attributes that matter for sizing — keep the prompt focused.
    keep = (
        "environment", "env", "name", "tags", "root_block_device.volume_size",
        "allocated_storage", "size", "multi_az", "desired_capacity", "min_size",
        "max_size", "replicas", "cpus", "memory_gb", "autoscaling", "spot_price",
        "instance_market_options.market_type", "num_cache_nodes",
    )
    extras = {k: v for k, v in r.attributes.items() if k in keep and v not in ("", "false")}
    extra_str = ("  " + ", ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
    loc = f"{r.file}:{r.line}" if r.line else r.file
    return f"- {r.kind}.{r.name} [{r.provider}]{size}{count} — {cost}  ({loc}){extra_str}"


def build_prompt(
    resources: list[Resource],
    guidelines: str = "",
    context: str = "",
) -> PromptParts:
    """Assemble the system + user messages for a waste-detection call."""
    system = SYSTEM_PROMPT
    if guidelines:
        system += (
            "\n\nTeam guidelines — project-specific rules this team set. Honour them "
            "exactly (do not raise a finding a guideline forbids; tag guideline-driven "
            'findings with category "team-guideline"):\n' + guidelines
        )

    total = sum(r.total_monthly_cost for r in resources)
    lines = [
        "Provisioned resource inventory (approximate monthly costs from a bundled "
        "price table — treat as ballpark):",
        "",
        *[_render_resource(r) for r in resources],
        "",
        f"Approximate total provisioned cost: ~${total:.0f}/mo across "
        f"{len(resources)} resource(s).",
    ]
    if context:
        lines += ["", context]
    lines += ["", "Report the waste as JSON per the schema."]

    return PromptParts(system=system, user="\n".join(lines))
