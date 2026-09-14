"""
Deterministic scanner.

Three modes, all feeding the same detector catalog:
  - worktree : every text file under PATH (default)
  - staged   : only the staged diff (git diff --cached) — the pre-commit gate
  - history  : every added line across the FULL git history (all commits)

History matters because leaked keys live in old commits: a secret deleted from
HEAD is still exposed forever in the object database. Requesting --history
outside a git repo is a hard error (missing history = fail), never a silent pass.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

from git import InvalidGitRepositoryError, Repo

from oneport_secrets.detectors import Detector, detect
from oneport_secrets.exceptions import ScanError
from oneport_secrets.result import Finding, ScanResult

# Directories never worth scanning; keeps big/binary trees out of the walk.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
              ".pytest_cache", "dist", "build", ".tox", ".idea", ".ruff_cache"}
_MAX_FILE_BYTES = 2_000_000  # 2 MB — bigger files are almost always assets/blobs

# Machine-generated files whose high-entropy content is package hashes or
# minified blobs — never secrets. ENTROPY detection is suppressed for these;
# regex detectors still run (a real AWS-key pattern is worth surfacing even
# here). uv.lock alone produced 7,806 false "high-entropy string" hits on a
# stock FastAPI clone — one per sha256 wheel digest — which hard-blocks op ship
# and buries any genuine finding. Every ecosystem's lockfile is the same shape.
_LOCKFILE_NAMES = {
    "uv.lock", "poetry.lock", "pipfile.lock", "pdm.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "bun.lockb", "cargo.lock", "composer.lock", "gemfile.lock", "go.sum",
    "flake.lock", "mix.lock", "packages.lock.json", "conda-lock.yml",
}
# .svg is vector-image XML — full of base64 blobs and path-coordinate runs that
# score as high-entropy noise (FastAPI's two sponsor SVGs alone = 1,836 hits).
_GENERATED_ENTROPY_SUFFIXES = (".lock", ".min.js", ".min.css", ".map", ".svg")


def _entropy_suppressed(rel_path: str) -> bool:
    """True for files where an entropy hit is always noise (lockfiles, minified)."""
    name = rel_path.replace(os.sep, "/").rsplit("/", 1)[-1].lower()
    return name in _LOCKFILE_NAMES or name.endswith(_GENERATED_ENTROPY_SUFFIXES)


# ── ignore matching ─────────────────────────────────────────────────────────────

def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """True if the repo-relative path matches any ignore glob or directory prefix."""
    rel = rel_path.replace(os.sep, "/")
    for pat in patterns:
        p = pat.rstrip("/")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"{p}/*"):
            return True
        # bare directory name anywhere in the path (e.g. "fixtures")
        if p and p in rel.split("/"):
            return True
    return False


def _is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _in_skipped_dir(rel_path: str) -> bool:
    """True if any path segment is a vendored/build dir we never scan.

    Applies to diff/patch scanning too (staged/head/history) — a committed
    `.venv` or `node_modules` must not surface thousands of dependency 'secrets'
    just because they appear in a diff.
    """
    return any(part in _SKIP_DIRS for part in rel_path.replace(os.sep, "/").split("/"))


# ── working-tree scan ───────────────────────────────────────────────────────────

def _iter_worktree_files(root: Path, ignore: list[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            fpath = Path(dirpath) / name
            rel = str(fpath.relative_to(root))
            if _is_ignored(rel, ignore):
                continue
            yield fpath


def scan_file(fpath: Path, rel: str, detectors: list[Detector] | None,
              entropy_enabled: bool) -> list[Finding]:
    """Scan one text file. Returns [] for unreadable/binary/oversized files."""
    try:
        raw = fpath.read_bytes()
    except OSError:
        return []
    if len(raw) > _MAX_FILE_BYTES or _is_probably_binary(raw):
        return []
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[Finding] = []
    file_entropy = entropy_enabled and not _entropy_suppressed(rel)
    for lineno, line in enumerate(content.splitlines(), start=1):
        for hit in detect(line, detectors, file_entropy):
            findings.append(Finding(
                detector_id=hit.detector_id,
                detector_name=hit.detector_name,
                severity=hit.severity,
                path=rel,
                line=lineno,
                match=hit.match,
                entropy=hit.entropy,
                line_text=line.strip()[:400],
            ))
    return findings


def scan_worktree(
    root: Path,
    detectors: list[Detector] | None,
    ignore: list[str],
    entropy_enabled: bool,
) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for fpath in _iter_worktree_files(root, ignore):
        rel = str(fpath.relative_to(root)).replace(os.sep, "/")
        file_findings = scan_file(fpath, rel, detectors, entropy_enabled)
        # scanned counts files we actually read (skip returns [] but so does a
        # clean file); count via a cheap re-check of readability is overkill —
        # count every regular file we attempted.
        scanned += 1
        findings.extend(file_findings)
    return findings, scanned


# ── unified-diff parsing (shared by staged + history) ───────────────────────────

def iter_added_lines(patch: str) -> Iterator[tuple[str, int, str]]:
    """Yield (path, new_line_number, added_text) for every '+' line in a unified
    diff. Context lines advance the counter; removed lines are ignored.
    """
    path = ""
    new_line = 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            path = p[2:] if p.startswith("b/") else p
            if path == "/dev/null":
                path = ""
        elif raw.startswith("@@"):
            # @@ -a,b +c,d @@
            try:
                plus = raw.split("+", 1)[1]
                new_line = int(plus.split(",", 1)[0].split(" ", 1)[0])
            except (IndexError, ValueError):
                new_line = 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if path:
                yield path, new_line, raw[1:]
            new_line += 1
        elif raw.startswith(" "):
            new_line += 1
        # '-' and metadata lines don't advance the new-file counter


def _scan_patch(
    patch: str,
    detectors: list[Detector] | None,
    ignore: list[str],
    entropy_enabled: bool,
    commit: str | None = None,
    author: str = "",
    date: str = "",
) -> list[Finding]:
    findings: list[Finding] = []
    for path, lineno, text in iter_added_lines(patch):
        if _in_skipped_dir(path) or _is_ignored(path, ignore):
            continue
        line_entropy = entropy_enabled and not _entropy_suppressed(path)
        for hit in detect(text, detectors, line_entropy):
            findings.append(Finding(
                detector_id=hit.detector_id,
                detector_name=hit.detector_name,
                severity=hit.severity,
                path=path,
                line=lineno,
                match=hit.match,
                commit=commit,
                author=author,
                date=date,
                entropy=hit.entropy,
                line_text=text.strip()[:400],
            ))
    return findings


def _open_repo(root: Path) -> Repo:
    try:
        return Repo(root, search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise ScanError(
            f"{root} is not inside a git repository — cannot scan git history. "
            "Run without --history, or run inside a repo."
        ) from exc


def scan_staged(
    root: Path,
    detectors: list[Detector] | None,
    ignore: list[str],
    entropy_enabled: bool,
) -> tuple[list[Finding], int]:
    repo = _open_repo(root)
    patch = repo.git.diff("--cached", "--no-color", "--unified=0")
    findings = _scan_patch(patch, detectors, ignore, entropy_enabled)
    return findings, 1 if patch.strip() else 0


def scan_head(
    root: Path,
    detectors: list[Detector] | None,
    ignore: list[str],
    entropy_enabled: bool,
) -> tuple[list[Finding], int]:
    """Scan only lines ADDED in the last commit (HEAD~1..HEAD).

    This is the pre-ship gate scope — the same diff-scoping every other `op ship`
    gate uses. It keeps a ship cheap (one small patch, not the whole tree) and
    answers the real question: did *this change* introduce a secret?
    """
    repo = _open_repo(root)
    try:
        patch = repo.git.diff("HEAD~1", "HEAD", "--no-color", "--unified=0")
    except Exception:  # noqa: BLE001 — initial commit has no parent: show the whole first commit
        patch = repo.git.show("HEAD", "--no-color", "--unified=0", "--pretty=format:")
    findings = _scan_patch(patch, detectors, ignore, entropy_enabled)
    return findings, 1 if patch.strip() else 0


def scan_history(
    root: Path,
    detectors: list[Detector] | None,
    ignore: list[str],
    entropy_enabled: bool,
    on_commit=None,
) -> tuple[list[Finding], int]:
    repo = _open_repo(root)
    findings: list[Finding] = []
    commits = 0
    for commit in repo.iter_commits("--all"):
        if len(commit.parents) > 1:
            continue  # merges rarely introduce new content; avoids double counting
        commits += 1
        if on_commit is not None and commits % 25 == 0:
            on_commit(commits)  # progress heartbeat for the CLI spinner
        try:
            patch = repo.git.show(
                commit.hexsha, "--no-color", "--unified=0", "--pretty=format:",
            )
        except Exception:  # noqa: BLE001 — a bad object shouldn't abort the whole scan
            continue
        findings.extend(_scan_patch(
            patch, detectors, ignore, entropy_enabled,
            commit=commit.hexsha,
            author=str(commit.author.name or ""),
            date=commit.committed_datetime.date().isoformat(),
        ))
    return findings, commits


# ── dedupe ──────────────────────────────────────────────────────────────────────

def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.commit or "", f.path, f.line, f.detector_id, f.fingerprint)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# ── entry point ─────────────────────────────────────────────────────────────────

def scan(
    path: str,
    mode: str,
    detectors: list[Detector] | None = None,
    ignore: list[str] | None = None,
    entropy_enabled: bool = True,
    include_worktree_with_history: bool = True,
    on_commit=None,
) -> ScanResult:
    """Run a deterministic scan. `mode` is one of worktree | staged | history.

    In history mode the working tree is *also* scanned by default, so a secret
    still present in HEAD is reported alongside its historical commits.
    """
    root = Path(path).resolve()
    ignore = ignore or []
    findings: list[Finding] = []
    files = 0
    commits = 0

    if mode == "staged":
        findings, files = scan_staged(root, detectors, ignore, entropy_enabled)
    elif mode == "head":
        findings, files = scan_head(root, detectors, ignore, entropy_enabled)
    elif mode == "history":
        h_findings, commits = scan_history(root, detectors, ignore, entropy_enabled,
                                           on_commit=on_commit)
        findings.extend(h_findings)
        if include_worktree_with_history and root.is_dir():
            wt_findings, files = scan_worktree(root, detectors, ignore, entropy_enabled)
            findings.extend(wt_findings)
    else:  # worktree
        if root.is_file():
            findings = scan_file(root, root.name, detectors, entropy_enabled)
            files = 1
        else:
            findings, files = scan_worktree(root, detectors, ignore, entropy_enabled)

    return ScanResult(
        findings=_dedupe(findings),
        scanned_files=files,
        scanned_commits=commits,
        target=str(root),
        mode=mode,
    )
