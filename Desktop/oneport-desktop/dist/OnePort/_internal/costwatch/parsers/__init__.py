"""
IaC parsers.

Each parser turns a source file into a list of `Resource` objects with the
provisioned size/count and, where the bundled price table covers it, an
approximate monthly cost. Everything is static — the files on disk are the
only input; no cloud APIs, no credentials.
"""

from __future__ import annotations

from pathlib import Path

from costwatch.exceptions import ParseError
from costwatch.parsers.docker import parse_compose_file, parse_compose_text
from costwatch.parsers.terraform import parse_tf_file, parse_tf_plan, parse_tf_text
from costwatch.result import Resource

__all__ = [
    "discover_and_parse",
    "is_iac_filename",
    "parse_compose_file",
    "parse_compose_text",
    "parse_source_text",
    "parse_tf_file",
    "parse_tf_plan",
    "parse_tf_text",
]

# Filenames we recognise as docker-compose.
_COMPOSE_NAMES = {
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
}


def _is_compose(path: Path) -> bool:
    name = path.name.lower()
    return name in _COMPOSE_NAMES or name.startswith("docker-compose.")


def is_iac_filename(filename: str) -> bool:
    """True if `filename` is one Costwatch knows how to parse (.tf / compose)."""
    p = Path(filename)
    return p.suffix == ".tf" or _is_compose(p)


def parse_source_text(filename: str, text: str) -> list[Resource]:
    """Parse in-memory IaC content, dispatching on the filename. Non-IaC → []."""
    p = Path(filename)
    if p.suffix == ".tf":
        return parse_tf_text(text, filename)
    if _is_compose(p):
        return parse_compose_text(text, filename)
    return []


def discover_and_parse(
    root: str | Path,
    plan_path: str | Path | None = None,
    ignore_paths: list[str] | None = None,
) -> tuple[list[Resource], list[str]]:
    """Walk `root` for IaC files and parse every one we recognise.

    Returns (resources, notes). `root` may be a single file or a directory.
    A Terraform plan JSON, when provided, is parsed too and its resources are
    merged in — plan output is the most accurate source since it has resolved
    variables.
    """
    root = Path(root)
    ignore = ignore_paths or []
    resources: list[Resource] = []
    notes: list[str] = []

    if plan_path:
        try:
            resources.extend(parse_tf_plan(Path(plan_path)))
            notes.append(f"Parsed Terraform plan: {plan_path}")
        except ParseError as exc:
            notes.append(f"Could not parse plan {plan_path}: {exc}")

    files = [root] if root.is_file() else _iter_iac_files(root, ignore)
    if not files and not resources:
        notes.append(f"No IaC files (.tf / docker-compose) found under {root}.")

    for path in files:
        try:
            if path.suffix == ".tf":
                resources.extend(parse_tf_file(path))
            elif _is_compose(path):
                resources.extend(parse_compose_file(path))
        except ParseError as exc:
            notes.append(f"Skipped {path}: {exc}")

    return resources, notes


def _iter_iac_files(root: Path, ignore: list[str]) -> list[Path]:
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.as_posix())
        if any(frag and frag in rel for frag in ignore):
            continue
        if path.suffix == ".tf" or _is_compose(path):
            out.append(path)
    return out
