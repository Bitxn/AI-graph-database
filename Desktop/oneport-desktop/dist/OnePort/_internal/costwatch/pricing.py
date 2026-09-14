"""
Bundled, approximate on-demand price table.

These are ROUGH monthly USD estimates for common AWS and GCP compute/storage,
computed from published on-demand hourly rates in a representative US region
(us-east-1 / us-central1) at 730 hours per month. They are intentionally
approximate — no cloud credentials, no live pricing API — and are meant to
size findings ("~4x oversized, ~$180/mo"), not to reconcile a bill.

Rates drift; treat every figure as a ballpark. Update the tables below as
needed. Unknown instance types simply return None (no estimate attached).
"""

from __future__ import annotations

HOURS_PER_MONTH = 730

# AWS EC2 on-demand, Linux, us-east-1 ($/hr).
_EC2_HOURLY: dict[str, float] = {
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416,
    "t3.large": 0.0832, "t3.xlarge": 0.1664, "t3.2xlarge": 0.3328,
    "t3a.micro": 0.0094, "t3a.small": 0.0188, "t3a.medium": 0.0376, "t3a.large": 0.0752,
    "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464, "t2.large": 0.0928,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384, "m5.4xlarge": 0.768,
    "m5.8xlarge": 1.536, "m5.12xlarge": 2.304, "m5.24xlarge": 4.608,
    "m6i.large": 0.096, "m6i.xlarge": 0.192, "m6i.2xlarge": 0.384, "m6i.4xlarge": 0.768,
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34, "c5.4xlarge": 0.68,
    "c5.9xlarge": 1.53, "c6i.large": 0.085, "c6i.xlarge": 0.17, "c6i.2xlarge": 0.34,
    "r5.large": 0.126, "r5.xlarge": 0.252, "r5.2xlarge": 0.504, "r5.4xlarge": 1.008,
    "r6i.large": 0.126, "r6i.xlarge": 0.252, "r6i.2xlarge": 0.504,
    "p3.2xlarge": 3.06, "p3.8xlarge": 12.24, "g4dn.xlarge": 0.526, "g4dn.2xlarge": 0.752,
}

# AWS RDS on-demand, single-AZ, us-east-1 ($/hr).
_RDS_HOURLY: dict[str, float] = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068,
    "db.t3.large": 0.136, "db.t3.xlarge": 0.272, "db.t3.2xlarge": 0.544,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.m5.2xlarge": 0.684,
    "db.m5.4xlarge": 1.368, "db.m6i.large": 0.171, "db.m6i.xlarge": 0.342,
    "db.r5.large": 0.24, "db.r5.xlarge": 0.48, "db.r5.2xlarge": 0.96, "db.r5.4xlarge": 1.92,
    "db.r6g.large": 0.216, "db.r6g.xlarge": 0.432,
}

# AWS ElastiCache on-demand, us-east-1 ($/hr).
_CACHE_HOURLY: dict[str, float] = {
    "cache.t3.micro": 0.017, "cache.t3.small": 0.034, "cache.t3.medium": 0.068,
    "cache.m5.large": 0.156, "cache.m5.xlarge": 0.311, "cache.m5.2xlarge": 0.623,
    "cache.r5.large": 0.216, "cache.r5.xlarge": 0.433, "cache.r5.2xlarge": 0.866,
}

# GCP Compute Engine on-demand, us-central1 ($/hr).
_GCP_HOURLY: dict[str, float] = {
    "e2-micro": 0.008376, "e2-small": 0.016751, "e2-medium": 0.033503,
    "e2-standard-2": 0.067007, "e2-standard-4": 0.134014, "e2-standard-8": 0.268028,
    "e2-standard-16": 0.536056,
    "n1-standard-1": 0.0475, "n1-standard-2": 0.095, "n1-standard-4": 0.19,
    "n1-standard-8": 0.38, "n1-standard-16": 0.76,
    "n2-standard-2": 0.0971, "n2-standard-4": 0.1942, "n2-standard-8": 0.3885,
    "n2-standard-16": 0.7769, "n2-standard-32": 1.5539,
    "c2-standard-4": 0.2088, "c2-standard-8": 0.4176, "c2-standard-16": 0.8352,
}

# Block-storage $/GB-month.
_STORAGE_PER_GB: dict[str, float] = {
    # AWS EBS
    "gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015,
    "standard": 0.05,
    # GCP persistent disk
    "pd-standard": 0.04, "pd-balanced": 0.10, "pd-ssd": 0.17, "pd-extreme": 0.125,
}

DEFAULT_STORAGE_PER_GB = 0.10  # unknown volume type → assume gp2-ish


def instance_monthly(size: str) -> float | None:
    """Approximate monthly USD for a compute/db/cache instance, or None if unknown.

    Accepts EC2 (`t3.large`), RDS (`db.m5.large`), ElastiCache
    (`cache.r5.large`), and GCP (`n2-standard-4`) sizes.
    """
    if not size:
        return None
    key = size.strip()
    for table in (_EC2_HOURLY, _RDS_HOURLY, _CACHE_HOURLY, _GCP_HOURLY):
        if key in table:
            return round(table[key] * HOURS_PER_MONTH, 2)
    return None


def storage_monthly(size_gb: float, volume_type: str = "") -> float | None:
    """Approximate monthly USD for a block volume of `size_gb`.

    Unknown/blank volume types fall back to a gp2-ish rate rather than None,
    since a size is enough to estimate the dominant cost.
    """
    if size_gb is None or size_gb <= 0:
        return None
    rate = _STORAGE_PER_GB.get((volume_type or "").strip().lower(), DEFAULT_STORAGE_PER_GB)
    return round(size_gb * rate, 2)


def is_known_instance(size: str) -> bool:
    return instance_monthly(size) is not None
