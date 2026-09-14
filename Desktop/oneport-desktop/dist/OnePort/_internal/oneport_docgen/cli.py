# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
oneport-docgen CLI.

  oneport-docgen generate ./repo     # branded PDF + editable .docx + Markdown
  oneport-docgen init                 # scaffold a docgen.toml branding file
  oneport-docgen formats              # list output formats / doc types

Reuses oneport-context's local RepoIndex (no upload). Logged in to Oneport, the
narrative sections are model-written (metered); logged out it still produces a
complete, accurate document in heuristic mode.
"""
from __future__ import annotations

import sys as _sys


def _force_utf8_stdio() -> None:
    """Windows consoles/pipes default to cp1252, which can't encode the emoji/
    box glyphs in this tool's output — rendering then crashes AFTER the real
    work (sometimes a paid model call) succeeded. Reconfigure to UTF-8 up
    front. Best-effort: a stream that can't reconfigure is left alone."""
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


_force_utf8_stdio()

import asyncio
import re
import sys
from pathlib import Path

import click

from oneport_debug_core.config.settings import load_config
from oneport_debug_core.cli.output import console, print_error, print_step, print_success

from oneport_docgen.document import DocMeta
from oneport_docgen.doctypes import DOCTYPES, resolve as resolve_doctype, keys as doctype_keys
from oneport_docgen.render import FORMATS, extension, render

_CLASSIFICATIONS = ("Public", "Internal", "Confidential", "Restricted")
_STATUSES = ("Draft", "In Review", "Approved")


def _index_path(root: Path) -> Path:
    return root / ".context" / "index.json"


def _maybe_llm(config):
    """Return the Oneport managed LLM if logged in, else None (heuristic mode).

    Narrative sections run through the Oneport proxy (metered, no BYOK key), so
    the gate is an Oneport login rather than a local API key.
    """
    from oneport_account import is_logged_in
    from oneport_docgen.managed_llm import ManagedLLM

    return ManagedLLM() if is_logged_in() else None


def _load_or_build_index(root: Path, llm):
    from oneport_context.models import RepoIndex
    from oneport_context.indexer import build_index
    p = _index_path(root)
    if p.exists():
        print_step(f"Loaded cached index ({RepoIndex.load(p).file_count} files)")
        return RepoIndex.load(p)
    print_step("No cached index — building one first…")
    idx = asyncio.run(build_index(root, llm=llm, model_name=(llm.active_provider if llm else "heuristic")))
    idx.save(p)
    return idx


def _csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "document"


@click.group()
@click.option("--config", default=None, type=click.Path())
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """oneport-docgen — enterprise documentation from any codebase, generated locally."""
    from oneport_debug_core.cli.output import ensure_utf8_console
    ensure_utf8_console()
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(Path(config) if config else None)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--type", "doc_type", default="tdd", show_default=True,
              help=f"Document type: {', '.join(doctype_keys())}")
@click.option("--format", "fmt", default="pdf,docx,md", show_default=True,
              help=f"Comma-separated outputs: {', '.join(FORMATS)}")
@click.option("--scope", default="all", show_default=True,
              help="'all' or 'module:<name>' to document a single module")
@click.option("--out", "out_dir", default=None, help="Output directory (default: <repo>/.docgen)")
@click.option("--title", default=None)
@click.option("--version", "version", default="1.0", show_default=True)
@click.option("--status", default="Draft", show_default=True)
@click.option("--classification", default="Confidential", show_default=True)
@click.option("--author", "authors", multiple=True, help="Author (repeatable)")
@click.option("--reviewer", "reviewers", multiple=True, help="Reviewer (repeatable)")
@click.option("--approver", "approvers", multiple=True, help="Approver (repeatable)")
@click.option("--company", default=None, help="Override the company name on the cover/header")
@click.option("-y", "--yes", is_flag=True, help="Non-interactive: accept defaults (for CI)")
@click.option("--no-open", is_flag=True, help="Don't open the finished document")
@click.pass_context
def generate(ctx, repo, doc_type, fmt, scope, out_dir, title, version, status,
             classification, authors, reviewers, approvers, company, yes, no_open):
    """Generate a formal document for REPO (PDF + editable Word + Markdown)."""
    from oneport_docgen.config import load_brand
    from oneport_docgen.authors import suggest_authors

    formats = [f.strip().lower() for f in fmt.split(",") if f.strip()]
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        print_error(f"Unknown format(s): {', '.join(bad)}", hint=f"Choose from: {', '.join(FORMATS)}")
        sys.exit(1)

    root = Path(repo).resolve()
    config = ctx.obj["config"]
    llm = _maybe_llm(config)
    brand = load_brand(root)
    if company is not None:
        brand.company = company

    idx = _load_or_build_index(root, llm)

    resolved_type, builder = resolve_doctype(doc_type)
    scope_module = scope.split(":", 1)[1] if scope.lower().startswith("module:") else None

    # ---- gather document-control metadata (interactive unless --yes) ---------
    author_list = list(authors) or suggest_authors(root)
    reviewer_list = list(reviewers)
    approver_list = list(approvers)
    resolved_title = title or f"{idx.name} — {resolved_type}"

    if not yes:
        console.print(f"\n[bold]Document details[/bold] [dim](Enter to accept the default)[/dim]")
        resolved_title = click.prompt("  Title", default=resolved_title)
        version = click.prompt("  Version", default=version)
        status = click.prompt("  Status", default=status)
        classification = click.prompt("  Classification", default=classification,
                                      type=click.Choice(_CLASSIFICATIONS, case_sensitive=False))
        if not brand.company:
            brand.company = click.prompt("  Company (optional)", default="", show_default=False)
        author_list = _csv(click.prompt("  Author(s)", default=", ".join(author_list) or ""))
        reviewer_list = _csv(click.prompt("  Reviewer(s)", default=", ".join(reviewer_list), show_default=bool(reviewer_list)))
        approver_list = _csv(click.prompt("  Approver(s)", default=", ".join(approver_list), show_default=bool(approver_list)))

    meta = DocMeta(
        project=idx.name, doc_type=resolved_type, title=resolved_title,
        version=version, status=status, classification=classification.capitalize()
        if classification.lower() in {c.lower() for c in _CLASSIFICATIONS} else classification,
        authors=author_list, reviewers=reviewer_list, approvers=approver_list,
    )

    if doc_type.lower().strip() == "sbom":
        brain = "deterministic (scanned from manifests)"
    else:
        brain = llm.active_provider if llm else "heuristic (no model key)"
    print_step(f"Writing {resolved_type} for {idx.name}  (brain: {brain})")
    try:
        document = asyncio.run(builder(idx, meta, llm=llm, scope_module=scope_module))
    except Exception as err:
        print_error(f"Content generation failed: {err}")
        sys.exit(1)

    out = Path(out_dir) if out_dir else root / ".docgen"
    stem = f"{_slug(idx.name)}-{_slug(resolved_type)}"
    written: list[Path] = []
    for f in formats:
        target = out / f"{stem}{extension(f)}"
        try:
            render(document, f, target, brand)
            written.append(target)
        except Exception as err:
            print_error(f"{f} render failed: {err}")

    if not written:
        sys.exit(1)
    console.print()
    for p in written:
        print_success(f"{p.suffix.lstrip('.').upper():4s} → {p}")
    if not no_open:
        _open_file(written[0])


@main.command()
@click.argument("directory", type=click.Path(file_okay=False), default=".")
def init(directory: str) -> None:
    """Scaffold a docgen.toml branding file in DIRECTORY."""
    from oneport_docgen.config import CONFIG_NAME, write_sample
    dest = Path(directory).resolve() / CONFIG_NAME
    if dest.exists():
        print_error(f"{dest} already exists.", hint="Delete it first if you want a fresh template.")
        sys.exit(1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_sample(dest)
    print_success(f"Wrote {dest}")
    console.print("[dim]Edit company, logo and colours, then run:  oneport-docgen generate ./your-repo[/dim]")


@main.command()
def formats() -> None:
    """List available output formats and document types."""
    console.print("\n[bold]Output formats[/bold]")
    for f in FORMATS:
        console.print(f"  [cyan]{f:5s}[/cyan] {extension(f)}")
    console.print("\n[bold]Document types[/bold] [dim](--type)[/dim]")
    for k in doctype_keys():
        console.print(f"  [cyan]{k:12s}[/cyan] {DOCTYPES[k][0]}")
    console.print()


def _open_file(path: Path) -> None:
    import os
    import webbrowser
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))       # Windows
        else:
            webbrowser.open(path.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
