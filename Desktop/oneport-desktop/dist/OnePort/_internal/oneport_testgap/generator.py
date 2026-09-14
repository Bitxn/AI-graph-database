"""
Verified test generation — the differentiator.

Codecov and diff-cover report numbers. Qodo generates tests. Testgap only
SHOWS tests it has already executed: generate → run in a sandbox temp file →
keep only if the tests pass AND the target gap lines were actually covered.
A failing test gets one repair round (its pytest output goes back to the
model); if it still fails, it is discarded and reported as discarded.

Verified tests land in tests/generated/ for human review — never committed.
"""

from __future__ import annotations

import re
from pathlib import Path

from oneport_testgap.config import Config
from oneport_testgap.gaps import Gap, GeneratedTest
from oneport_testgap.llm import complete
from oneport_testgap.verifier import module_import_path, verify_test

GENERATE_SYSTEM_PROMPT = """\
You are a senior test engineer writing pytest tests for code that was changed
but never executed by the existing test suite.

Rules:
- Output ONLY a complete, runnable pytest file. No prose, no explanations.
  You may wrap the code in one ```python fence, nothing outside it.
- The tests MUST execute the listed uncovered lines. That is the whole point.
- Import the code under test using the exact module path you are given.
- No placeholders, no TODOs, no skipped tests, no mocking of the function
  under test itself (mock only its external dependencies: network, DB, clock).
- Tests must be deterministic and self-contained: no real network, no real
  services, no files outside tmp_path.
- Use plain pytest style (functions + fixtures), assert real behaviour, and
  cover at least one failure/edge case when the code has error branches.
"""

REPAIR_USER_TPL = """\
The pytest file you wrote failed verification. Fix it and output the complete
corrected file (same rules: only code, one optional ```python fence).

YOUR PREVIOUS FILE:
{code}

PYTEST OUTPUT:
{output}

Reminder — module path to import: {module_path}
The tests must pass AND execute these lines of {file}: {line_ranges}
"""


def generate_tests(
    gaps: list[Gap],
    top_n: int,
    config: Config,
    repo_root: str | Path,
    guidelines: str = "",
) -> tuple[list[GeneratedTest], int]:
    """
    Generate + verify tests for the top N gaps (gaps arrive risk-sorted).

    Returns (all attempts with their verdicts, total_tokens). Only verified
    entries have been written to disk (config.generate.output_dir).
    """
    repo_root = Path(repo_root).resolve()
    results: list[GeneratedTest] = []
    total_tokens = 0

    for gap in gaps[:top_n]:
        module_path = module_import_path(gap.file)
        user_prompt = _build_user_prompt(gap, module_path, repo_root, guidelines)

        text, tokens = complete(
            model=config.model,
            api_key=config.api_key,
            system=GENERATE_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=config.max_tokens,
        )
        total_tokens += tokens
        code = _extract_code(text)
        attempts = 1

        verdict = verify_test(
            code, repo_root, gap.file, gap.lines, timeout=config.generate.test_timeout
        )

        # Repair rounds: feed the failure back once (configurable), then give up.
        rounds = 0
        while not verdict.verified and rounds < config.generate.repair_rounds:
            rounds += 1
            attempts += 1
            repair_prompt = REPAIR_USER_TPL.format(
                code=code,
                output=verdict.output or "(tests passed but did not execute the target lines)",
                module_path=module_path,
                file=gap.file,
                line_ranges=gap.line_ranges,
            )
            text, tokens = complete(
                model=config.model,
                api_key=config.api_key,
                system=GENERATE_SYSTEM_PROMPT,
                user=repair_prompt,
                max_tokens=config.max_tokens,
            )
            total_tokens += tokens
            code = _extract_code(text)
            verdict = verify_test(
                code, repo_root, gap.file, gap.lines, timeout=config.generate.test_timeout
            )

        generated = GeneratedTest(
            gap=gap,
            code=code,
            verified=verdict.verified,
            attempts=attempts,
            newly_covered=verdict.newly_covered,
            failure_output="" if verdict.verified else verdict.output,
        )
        if verdict.verified:
            generated.path = _write_verified(generated, repo_root, config.generate.output_dir)
        results.append(generated)

    return results, total_tokens


# ── Prompt building ────────────────────────────────────────────────────────────

def _build_user_prompt(gap: Gap, module_path: str, repo_root: Path, guidelines: str) -> str:
    parts: list[str] = []
    if guidelines:
        parts.append(f"TEAM GUIDELINES (testing rules here are mandatory):\n{guidelines}\n")

    full_source = _read_limited(repo_root / gap.file)
    parts.append(
        f"TARGET FILE: {gap.file}\n"
        f"MODULE PATH TO IMPORT: {module_path}\n"
        f"FUNCTION WITH THE GAP: {gap.function}\n"
        f"UNCOVERED CHANGED LINES (must be executed by your tests): {gap.line_ranges}\n"
    )
    if gap.why:
        parts.append(f"WHY THIS GAP MATTERS: {gap.why}\n")
    parts.append(f"GAP CONTEXT ('>' marks the uncovered changed lines):\n{gap.snippet}\n")
    if full_source:
        parts.append(f"FULL FILE CONTENT:\n{full_source}")
    return "\n".join(parts)


def _read_limited(path: Path, max_chars: int = 30_000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n# ... (truncated)"
    return text


# ── Output handling ────────────────────────────────────────────────────────────

def _extract_code(text: str) -> str:
    """Strip an optional ```python fence; otherwise trust the raw output."""
    fence = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip() + "\n"
    return text.strip() + "\n"


def _write_verified(generated: GeneratedTest, repo_root: Path, output_dir: str) -> str:
    """Write a verified test into tests/generated/ (repo-relative path returned)."""
    gap = generated.gap
    out_dir = repo_root / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "_", gap.function.lower()).strip("_") or "module"
    base = f"test_gap_{slug}"
    path = out_dir / f"{base}.py"
    counter = 2
    while path.exists():
        path = out_dir / f"{base}_{counter}.py"
        counter += 1

    covered = len(generated.newly_covered)
    header = (
        f"# Generated by oneport-testgap — VERIFIED: executed with pytest, all tests\n"
        f"# passed, and {covered} of {len(gap.lines)} target gap line(s) of "
        f"{gap.file} ({gap.line_ranges}) were covered.\n"
        f"# Review before committing. oneport-testgap never commits this file.\n\n"
    )
    path.write_text(header + generated.code, encoding="utf-8")
    return path.relative_to(repo_root).as_posix()
