# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""
oneport-context CLI.

  oneport-context index ./repo    # build the hierarchical understanding (.context/)
  oneport-context show            # localhost narrated walkthrough (auto-indexes)
  oneport-context demo            # zero-config walkthrough of a bundled sample repo
  oneport-context lsp             # editor hover-to-explain (Language Server)
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
import sys
from pathlib import Path

import click

from oneport_debug_core.config.settings import load_config
from oneport_debug_core.cli.output import console, print_error, print_step, print_success


def _index_path(root: Path) -> Path:
    return root / ".context" / "index.json"


def _maybe_llm(config):
    """Return the Oneport managed LLM if logged in, else None (heuristic mode).

    The model runs through the Oneport proxy (metered, no BYOK key) — so the
    gate is an Oneport login, not a local API key.
    """
    from oneport_account import is_logged_in
    from oneport_context.managed_llm import ManagedLLM

    return ManagedLLM() if is_logged_in() else None


def _index_for(root: Path, llm, fresh: bool):
    """Return an index for a downstream command (show/video/pdf/chat).

    Builds fresh when asked or when none is cached; otherwise loads the cached
    index and — the honesty step — checks it against the repo as it is now,
    warning clearly when the tour would be driven by a stale map."""
    from oneport_context.indexer import build_index
    from oneport_context.models import RepoIndex
    from oneport_context.scan import load_scan_config

    scan = load_scan_config(root)
    idx_path = _index_path(root)

    if fresh or not idx_path.exists():
        print_step("Rebuilding index (--fresh)…" if fresh and idx_path.exists()
                   else "No cached index — building one first…")
        idx = asyncio.run(build_index(root, llm=llm,
                                      model_name=(llm.active_provider if llm else "heuristic"),
                                      scan=scan))
        idx.save(idx_path)
        _warn_if_truncated(idx)
        return idx

    idx = RepoIndex.load(idx_path)
    from oneport_context.freshness import check_freshness
    report = check_freshness(idx, root, scan)
    if report.is_stale:
        console.print(
            f"[yellow]⚠ {report.summary()}.[/yellow] "
            "[dim]Showing the cached view — run `oneport-context index` "
            "or re-run with --fresh to update.[/dim]"
        )
    _warn_if_truncated(idx)
    return idx


def _warn_if_truncated(idx) -> None:
    if getattr(idx, "truncated", False):
        console.print(
            f"[yellow]⚠ Scan hit the file cap ({idx.file_count} files) — "
            "some files were not indexed.[/yellow] "
            "[dim]Raise `scan.max_files` in .oneport-context.yml to include them.[/dim]"
        )


@click.group()
@click.option("--config", default=None, type=click.Path())
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """oneport-context — understand any codebase without uploading it anywhere."""
    from oneport_debug_core.cli.output import ensure_utf8_console
    ensure_utf8_console()
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(Path(config) if config else None)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--check", is_flag=True,
              help="Report whether the cached index is stale, without rebuilding. "
                   "Exit 1 if stale (useful in CI / pre-commit).")
@click.pass_context
def index(ctx: click.Context, repo: str, check: bool) -> None:
    """Build a hierarchical understanding of REPO (cached in .context/)."""
    from oneport_context.indexer import build_index
    from oneport_context.scan import load_scan_config

    root = Path(repo).resolve()
    scan = load_scan_config(root)

    if check:
        from oneport_context.models import RepoIndex
        from oneport_context.freshness import check_freshness
        idx_path = _index_path(root)
        if not idx_path.exists():
            print_error("No index yet.", hint="Run `oneport-context index` first.")
            sys.exit(1)
        report = check_freshness(RepoIndex.load(idx_path), root, scan)
        if report.is_stale:
            console.print(f"[yellow]{report.summary()}.[/yellow]")
            for label, items in (("added", report.added), ("removed", report.removed),
                                 ("modified", report.modified)):
                for p in items[:5]:
                    console.print(f"  [dim]{label}:[/dim] {p}")
            sys.exit(1)
        print_success("Index is up to date.")
        return

    config = ctx.obj["config"]
    llm = _maybe_llm(config)
    model = llm.active_provider if llm else "heuristic (no model key set)"

    print_step(f"Indexing {root.name}  (brain: {model})")
    try:
        idx = asyncio.run(build_index(root, llm=llm,
                                      model_name=(llm.active_provider if llm else "heuristic"),
                                      scan=scan))
    except Exception as err:
        print_error(f"Indexing failed: {err}", hint="Is REPO a readable source directory?")
        sys.exit(1)

    idx.save(_index_path(root))
    _render_index_summary(idx)
    _warn_if_truncated(idx)
    print_success(f"Index saved to {_index_path(root)}")


def _render_index_summary(idx) -> None:
    from rich.table import Table
    from rich import box
    console.print()
    console.print(f"[bold]{idx.name}[/bold] — {idx.file_count} files · {idx.total_loc:,} LOC · brain: {idx.model_used}\n")
    console.print(f"[dim]{idx.summary}[/dim]\n")
    table = Table(title="Modules", box=box.SIMPLE_HEAVY, title_style="bold")
    table.add_column("Module", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Summary", style="white", overflow="fold")
    for m in sorted(idx.modules, key=lambda x: -x.loc)[:12]:
        table.add_row(m.name, str(len(m.files)), m.summary[:90])
    console.print(table)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--port", default=7000, show_default=True)
@click.option("--no-open", is_flag=True, help="Don't auto-open the browser")
@click.option("--fresh", is_flag=True, help="Rebuild the index before showing (ignore any cache).")
@click.pass_context
def show(ctx: click.Context, repo: str, port: int, no_open: bool, fresh: bool) -> None:
    """Open a localhost narrated architecture walkthrough of REPO."""
    from oneport_context.show.server import serve_walkthrough
    from oneport_context.storyboard import build_storyboard

    root = Path(repo).resolve()
    llm = _maybe_llm(ctx.obj["config"])
    idx = _index_for(root, llm, fresh)

    story = asyncio.run(build_storyboard(idx, llm=llm))
    serve_walkthrough(idx, story, port=port, open_browser=not no_open)


@main.command()
@click.option("--port", default=7000, show_default=True)
@click.option("--no-open", is_flag=True)
def demo(port: int, no_open: bool) -> None:
    """Zero-config walkthrough of a bundled sample repo (no API key needed)."""
    from oneport_context.demo import run_demo
    run_demo(port=port, open_browser=not no_open)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--out", "out_path", default=None, help="Output .mp4 path (default: <repo>/.context/walkthrough.mp4)")
@click.option("--voice", default="us", show_default=True,
              help="Voice accent for the Google narrator: us | uk | au | in | ca")
@click.option("--minutes", default=8.0, show_default=True, help="Target video length in minutes")
@click.option("--no-open", is_flag=True, help="Don't auto-open the finished video")
@click.option("--fresh", is_flag=True, help="Rebuild the index before rendering (ignore any cache).")
@click.pass_context
def video(ctx: click.Context, repo: str, out_path: str | None, voice: str, minutes: float,
          no_open: bool, fresh: bool) -> None:
    """Render a narrated MP4 walkthrough of REPO (slides + AI voice)."""
    try:
        from oneport_context.video import make_video
    except ImportError:
        print_error("Video rendering needs extra packages.",
                    hint="Install them with:  pip install 'oneport-context[video]'")
        sys.exit(1)

    from oneport_context.storyboard import build_storyboard

    root = Path(repo).resolve()
    config = ctx.obj["config"]
    llm = _maybe_llm(config)
    api_key = config.gemini.api_key.get_secret_value() or None

    idx = _index_for(root, llm, fresh)

    story = asyncio.run(build_storyboard(idx, llm=llm))
    out_mp4 = Path(out_path) if out_path else _index_path(root).parent / "walkthrough.mp4"

    # The script comes from the managed LLM (`llm`); `api_key` only selects the TTS
    # voice. Labelling the script by the voice key reported "heuristic script" even
    # when Gemini was writing it — and vice versa.
    script_brain = "Gemini script" if llm else "heuristic script (not logged in)"
    print_step(f"Rendering walkthrough video ({idx.file_count} files · {script_brain} · gTTS voice)")
    try:
        mp4, engine = asyncio.run(make_video(idx, story, out_mp4, api_key=api_key, llm=llm, voice=voice, minutes=minutes))
    except Exception as err:
        print_error(f"Video render failed: {err}",
                    hint="Ensure ffmpeg is available (moviepy bundles it via imageio-ffmpeg).")
        sys.exit(1)

    print_success(f"Video saved: {mp4}  (voice: {engine})")
    if not no_open:
        _open_file(mp4)


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


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--out", "out_path", default=None, help="Output .pdf path (default: <repo>/.context/handbook.pdf)")
@click.option("--port", default=7100, show_default=True)
@click.option("--no-open", is_flag=True, help="Just write the PDF; don't open a browser")
@click.option("--fresh", is_flag=True, help="Rebuild the index before writing (ignore any cache).")
@click.pass_context
def pdf(ctx: click.Context, repo: str, out_path: str | None, port: int, no_open: bool, fresh: bool) -> None:
    """Generate a PDF handbook for REPO and open it in the browser."""
    try:
        from oneport_context.pdf import make_pdf, serve_pdf
    except ImportError:
        print_error("PDF generation needs reportlab.",
                    hint="Install it with:  pip install 'oneport-context[pdf]'")
        sys.exit(1)

    root = Path(repo).resolve()
    llm = _maybe_llm(ctx.obj["config"])
    idx = _index_for(root, llm, fresh)

    brain = llm.active_provider if llm else "heuristic (no key)"
    print_step(f"Writing handbook ({idx.file_count} files · {brain})")
    out_pdf = Path(out_path) if out_path else _index_path(root).parent / "handbook.pdf"
    try:
        asyncio.run(make_pdf(idx, out_pdf, llm=llm))
    except Exception as err:
        print_error(f"PDF generation failed: {err}")
        sys.exit(1)

    print_success(f"Handbook saved: {out_pdf}")
    if no_open:
        return
    serve_pdf(out_pdf, name=idx.name, port=port, open_browser=True)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--fresh", is_flag=True, help="Rebuild the index before chatting (ignore any cache).")
@click.pass_context
def chat(ctx: click.Context, repo: str, fresh: bool) -> None:
    """Ask questions about REPO in the terminal (grounded in the local index)."""
    from oneport_context.chat import answer

    root = Path(repo).resolve()
    llm = _maybe_llm(ctx.obj["config"])
    if llm is None:
        print_error("Chat needs a model.",
                    hint="Log in to Oneport: `oneport-account login <token>` "
                         "(free token at https://oneport.dev), then retry.")
        sys.exit(1)

    idx = _index_for(root, llm, fresh)

    console.print(f"\n[bold]Chatting with {idx.name}[/bold] [dim]({idx.file_count} files · {llm.active_provider})[/dim]")
    console.print("[dim]Ask anything about the codebase. Type 'exit' to quit.[/dim]\n")
    history: list[tuple[str, str]] = []
    while True:
        try:
            q = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", ":q"):
            break
        try:
            ans, cited = asyncio.run(answer(idx, llm, history, q))
        except Exception as err:
            print_error(str(err))
            continue
        console.print(f"\n[cyan]{ans}[/cyan]")
        if cited:
            console.print(f"[dim]— sources: {', '.join(cited[:6])}[/dim]")
        console.print()
        history.append((q, ans))
        history[:] = history[-6:]


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False), default=".")
@click.pass_context
def lsp(ctx: click.Context, repo: str) -> None:
    """Start the hover-to-explain Language Server (connect from your editor)."""
    try:
        from oneport_context.lsp.server import run_lsp
    except ImportError:
        print_error(
            "LSP support needs the 'pygls' package.",
            hint="Install it with:  pip install 'oneport-context[lsp]'",
        )
        sys.exit(1)
    run_lsp(Path(repo).resolve(), _index_path(Path(repo).resolve()))
