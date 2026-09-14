"""
Orchestrator: file pairs in → classified ApiDiffResult out.

Pipeline (strictly layered — see the project's core rule):
  1. deterministic: AST surface diff per .py pair + OpenAPI spec diff
  2. deterministic: internal-caller scan for every non-additive change
  3. model:         verdict/impact/migration classification (skippable, --no-llm)
"""

from __future__ import annotations

import time
from pathlib import Path

from oneport_apidiff.callers import find_callers, find_callers_in_texts
from oneport_apidiff.classifier import classify
from oneport_apidiff.config import Config
from oneport_apidiff.differ import diff_surfaces
from oneport_apidiff.guidelines import load_guidelines
from oneport_apidiff.integrations.github import (
    GitHubIntegration,
    collect_pr_file_pairs,
    parse_pr_url,
)
from oneport_apidiff.openapi_diff import diff_openapi, is_openapi_file
from oneport_apidiff.result import ApiDiffResult, Change, ChangeKind
from oneport_apidiff.sources import FilePair, LocalGitSource
from oneport_apidiff.surface import extract_surface
from oneport_apidiff.waivers import apply_waivers, load_waivers

# Additive changes don't need a caller scan — nobody's existing code references
# a thing that didn't exist yet.
_ADDITIVE_KINDS = {
    ChangeKind.SYMBOL_ADDED,
    ChangeKind.PARAM_ADDED_OPTIONAL,
    ChangeKind.ENDPOINT_ADDED,
}


def detect_changes(pairs: list[FilePair]) -> tuple[list[Change], int]:
    """Deterministic detection over all file pairs. Returns (changes, files_checked)."""
    changes: list[Change] = []
    files_checked = 0

    for pair in pairs:
        path = pair.path.replace("\\", "/")
        if is_openapi_file(path):
            files_checked += 1
            changes.extend(diff_openapi(pair.base, pair.head, path))
            continue
        if not path.endswith(".py"):
            continue
        files_checked += 1
        try:
            base_surface = extract_surface(pair.base, path) if pair.base else {}
            head_surface = extract_surface(pair.head, path) if pair.head else {}
        except SyntaxError:
            # Mid-refactor / non-parseable version — surface can't be compared.
            continue
        changes.extend(diff_surfaces(base_surface, head_surface, path))

    changes = _collapse_relocations(changes)
    for i, change in enumerate(changes, start=1):
        change.id = i
    return changes, files_checked


def _collapse_relocations(changes: list[Change]) -> list[Change]:
    """A top-level public symbol removed in one file and added under the same
    name in another is a *relocation*, not a delete + create. Collapse the pair
    into one SYMBOL_RELOCATED (RISKY): imports of the old path break, but the
    name still exists — far more honest than a false BREAKING removal.

    Only unambiguous 1-removed/1-added pairs for a top-level name are collapsed.
    """
    from oneport_apidiff.result import Verdict

    removed = [c for c in changes if c.kind == ChangeKind.SYMBOL_REMOVED and "." not in c.symbol]
    added = [c for c in changes if c.kind == ChangeKind.SYMBOL_ADDED and "." not in c.symbol]
    rem_by_name: dict[str, list[Change]] = {}
    add_by_name: dict[str, list[Change]] = {}
    for c in removed:
        rem_by_name.setdefault(c.symbol, []).append(c)
    for c in added:
        add_by_name.setdefault(c.symbol, []).append(c)

    drop: set[int] = set()
    relocations: list[Change] = []
    for name, rem in rem_by_name.items():
        add = add_by_name.get(name, [])
        if len(rem) != 1 or len(add) != 1:
            continue
        r, a = rem[0], add[0]
        if r.file == a.file:
            continue
        drop.add(id(r))
        drop.add(id(a))
        relocations.append(
            Change(
                id=0,
                kind=ChangeKind.SYMBOL_RELOCATED,
                file=a.file,
                line=a.line,
                symbol=name,
                detail=f"public symbol `{name}` moved from {r.file} to {a.file} — "
                f"imports of the old path break unless it is re-exported",
                old_signature=r.old_signature,
                new_signature=a.new_signature,
                verdict=Verdict.RISKY,
            )
        )
    if not drop:
        return changes
    kept = [c for c in changes if id(c) not in drop]
    return kept + relocations


def attach_callers(
    changes: list[Change],
    repo_root: str | Path | None,
    head_texts: dict[str, str] | None = None,
) -> None:
    """Deterministic caller scan for every non-additive Python-surface change.

    repo_root must only be passed when it is known to be a checkout of the
    repo under review (local modes, or PR mode inside the Actions checkout);
    otherwise the fetched head texts are scanned instead.
    """
    for change in changes:
        if change.kind in _ADDITIVE_KINDS or not change.file.endswith(".py"):
            continue
        if repo_root is not None:
            change.callers = find_callers(
                repo_root, change.symbol, change.file, change.line
            )
        elif head_texts:
            change.callers = find_callers_in_texts(
                head_texts, change.symbol, change.file, change.line
            )


def _finalise(
    changes: list[Change],
    files_checked: int,
    target: str,
    config: Config | None,
    use_llm: bool,
    guidelines: str,
    started: float,
    pr_ref: dict | None = None,
) -> ApiDiffResult:
    result = ApiDiffResult(
        changes=changes,
        target=target,
        files_checked=files_checked,
        pr_ref=pr_ref,
    )
    if use_llm and config is not None and changes:
        result.model = config.model
        classified, tokens = classify(changes, config, guidelines=guidelines)
        result.llm_used = classified > 0
        result.total_tokens = tokens
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def check_local(
    mode: str,
    config: Config | None,
    base_branch: str = "main",
    repo_root: str | Path | None = None,
    use_llm: bool = True,
) -> ApiDiffResult:
    """Run the gate against the local repo. mode: "staged" | "head" | "base"."""
    started = time.monotonic()
    root = Path(repo_root or Path.cwd())
    source = LocalGitSource(root)

    if mode == "staged":
        pairs, target = source.staged_pairs(), "--staged"
    elif mode == "head":
        pairs, target = source.head_pairs(), "--head"
    elif mode == "base":
        pairs, target = source.base_pairs(base_branch), f"--base {base_branch}"
    else:  # pragma: no cover - CLI validates the mode
        raise ValueError(f"Unknown mode: {mode}")

    changes, files_checked = detect_changes(pairs)
    attach_callers(changes, repo_root=root)
    apply_waivers(changes, load_waivers(root))
    guidelines = load_guidelines(
        root, config.guidelines_path if config else ".oneport/guidelines.md"
    )
    return _finalise(changes, files_checked, target, config, use_llm, guidelines, started)


def check_pr(
    url: str,
    config: Config | None,
    use_llm: bool = True,
    token: str = "",
    repo_root: str | Path | None = None,
) -> ApiDiffResult:
    """Run the gate against a GitHub PR URL.

    When run inside a checkout of the same repo (the GitHub Actions case),
    the caller scan covers the whole tree; otherwise it falls back to the
    PR's own changed files.
    """
    started = time.monotonic()
    owner, repo, number = parse_pr_url(url)
    gh = GitHubIntegration(token=token)
    pairs, pr_ref = collect_pr_file_pairs(gh, owner, repo, number)

    changes, files_checked = detect_changes(pairs)
    head_texts = {p.path: p.head for p in pairs if p.head}
    # Trust the cwd for a full-repo caller scan only when it visibly IS a
    # checkout of this PR's repo (the GitHub Actions case) — i.e. at least one
    # changed file exists on disk. Otherwise scan the fetched files only.
    root = Path(repo_root or Path.cwd())
    is_checkout = any((root / p.path).exists() for p in pairs)
    attach_callers(
        changes, repo_root=root if is_checkout else None, head_texts=head_texts
    )
    apply_waivers(changes, load_waivers(root))
    guidelines = load_guidelines(
        root, config.guidelines_path if config else ".oneport/guidelines.md"
    )
    return _finalise(
        changes, files_checked, url, config, use_llm, guidelines, started, pr_ref=pr_ref
    )
