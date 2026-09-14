"""
docker-compose parsing.

Each service becomes a Resource. Compose has no instance types, but it does
carry the two things that drive container cost: replica count
(`deploy.replicas`) and CPU/memory reservations/limits
(`deploy.resources.limits`). We surface those so the LLM can flag, e.g., a
service pinned to 8 replicas with a 4-CPU / 8G limit and no autoscaling.

Where CPU/memory limits are present we attach a rough monthly estimate based
on reserved vCPU-hours (documented as approximate), so findings can carry a
dollar figure even without an instance type.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from costwatch.exceptions import ParseError
from costwatch.pricing import HOURS_PER_MONTH
from costwatch.result import Resource

# Very rough blended $/vCPU-hour for always-on container compute (Fargate-ish).
_PER_VCPU_HOUR = 0.04
_PER_GB_HOUR = 0.004


def parse_compose_file(path: str | Path) -> list[Resource]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    return parse_compose_text(text, str(path.as_posix()))


def parse_compose_text(text: str, filename: str = "") -> list[Resource]:
    """Parse docker-compose YAML from a string (used for in-memory PR content)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseError(f"cannot parse compose file {filename}: {exc}") from exc

    if not isinstance(data, dict):
        return []

    services = data.get("services") or {}
    if not isinstance(services, dict):
        return []

    resources: list[Resource] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        resources.append(_service_resource(name, svc, filename))
    return resources


def _service_resource(name: str, svc: dict, file: str) -> Resource:
    deploy = svc.get("deploy") or {}
    replicas = _int(deploy.get("replicas"), 1)

    limits = ((deploy.get("resources") or {}).get("limits")) or {}
    cpus = _float(limits.get("cpus"))
    mem_gb = _parse_mem(limits.get("memory"))

    attrs: dict[str, str] = {
        "image": str(svc.get("image", "")),
        "replicas": str(replicas),
    }
    has_autoscale = "replicas" in deploy or bool(deploy.get("mode") == "replicated")
    attrs["restart"] = str(svc.get("restart", ""))
    if cpus:
        attrs["cpus"] = str(cpus)
    if mem_gb:
        attrs["memory_gb"] = str(mem_gb)
    attrs["autoscaling"] = "false"  # compose has no native autoscaling
    attrs["_has_deploy"] = str(has_autoscale)

    monthly = None
    if cpus or mem_gb:
        per_unit = (cpus or 0) * _PER_VCPU_HOUR + (mem_gb or 0) * _PER_GB_HOUR
        monthly = round(per_unit * HOURS_PER_MONTH, 2) if per_unit else None

    return Resource(
        kind="service",
        name=name,
        provider="docker",
        file=file,
        line=0,
        size=(f"{cpus}vCPU/{mem_gb}GB" if (cpus or mem_gb) else ""),
        count=max(replicas, 1),
        attributes=attrs,
        monthly_cost=monthly,
    )


def _int(val: object, default: int = 1) -> int:
    try:
        return int(float(val))  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _float(val: object) -> float:
    try:
        return float(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0


def _parse_mem(val: object) -> float:
    """Parse a compose memory string ('512M', '2G', '1073741824') to GB."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val) / (1024**3), 3)
    s = str(val).strip().upper()
    mult = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}
    if s and s[-1] in mult:
        return round(_float(s[:-1]) * mult[s[-1]], 3)
    # plain bytes
    return round(_float(s) / (1024**3), 3)
