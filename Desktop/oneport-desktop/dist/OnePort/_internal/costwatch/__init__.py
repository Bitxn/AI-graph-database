"""
Oneport Costwatch — an AI infrastructure cost gate.

Reads the Infrastructure-as-Code the customer already has in the repo
(Terraform, docker-compose) and flags waste — over-provisioned instances,
always-on resources, oversized volumes, missing autoscaling — with an
estimated monthly saving and a concrete, cheaper config for each finding.

No cloud credentials required: everything is derived statically from the IaC,
so the trust ask is read-only and local.
"""

from __future__ import annotations

__version__ = "0.2.0"
