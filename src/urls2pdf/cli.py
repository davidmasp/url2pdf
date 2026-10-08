"""Typer commands for installing Chromium and rendering URL lists."""

from importlib.metadata import version
from pathlib import Path
import subprocess
from typing import Annotated

from playwright.sync_api import Error as PlaywrightError
from tqdm import tqdm
import typer

from urls2pdf.browser import BrowserInstaller
from urls2pdf.papers import PapersConfigError, load_rules
from urls2pdf.renderer import PDFRenderer
from urls2pdf.urls import URLFile

app = typer.Typer(
    help="Render lists of web pages to individual PDFs.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"urls2pdf {version('urls2pdf')}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Save web pages as PDFs with Chromium."""


@app.command("install-browser")
def install_browser(
    with_deps: Annotated[
        bool,
        typer.Option("--with-deps", help="Also install Linux system dependencies (may require root)."),
    ] = False,
) -> None:
    """Download the Chromium browser required for PDF rendering."""
    try:
        BrowserInstaller().install(with_deps=with_deps)
    except (OSError, subprocess.CalledProcessError) as exc:
        typer.echo(f"Browser installation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Chromium is ready.")


@app.command()
def render(
    url_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="UTF-8 file with one URL per line; # lines are comments.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", file_okay=False, help="Directory for generated PDFs."),
    ] = Path("pdfs"),
    papers: Annotated[
        bool,
        typer.Option("--papers", help="Prefer publisher PDFs for supported journal hosts."),
    ] = False,
    papers_config: Annotated[
        Path | None,
        typer.Option("--papers-config", help="Explicit TOML publisher rules; requires --papers."),
    ] = None,
) -> None:
    """Save every URL in URL_FILE as a PDF; optionally prefer publisher PDFs."""
    if papers_config is not None and not papers:
        raise typer.BadParameter("--papers-config requires --papers", param_hint="--papers-config")
    try:
        urls = URLFile(url_file).read()
        if not urls:
            typer.echo("No URLs found in the file.")
            return
        rules = load_rules(papers_config) if papers else None
        typer.echo(f"Saving {len(urls)} PDFs to: {output_dir.resolve()}")
        with tqdm(urls, desc="Rendering", unit="url") as progress:
            renderer = (
                PDFRenderer(output_dir, papers=True, papers_rules=rules)
                if papers else PDFRenderer(output_dir)
            )
            results = renderer.render(progress)
    except PapersConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="papers rules") from exc
    except (OSError, UnicodeError, PlaywrightError) as exc:
        typer.echo(f"Rendering failed: {exc}", err=True)
        if isinstance(exc, PlaywrightError) and "Executable doesn't exist" in str(exc):
            typer.echo("To install Chromium, run: urls2pdf install-browser", err=True)
        raise typer.Exit(code=1) from exc

    failures = [result for result in results if result.error is not None]
    for result in results:
        if papers and result.fallback_reason:
            typer.echo(f"Rendering fallback [{result.url}]: {result.fallback_reason}", err=True)
        if result.cleanup_error:
            typer.echo(f"Page cleanup warning [{result.url}]: {result.cleanup_error}", err=True)
    for result in failures:
        typer.echo(f"Failed [{result.url}]: {result.error}", err=True)
    if papers:
        downloaded = sum(
            result.error is None and result.method == "downloaded"
            for result in results
        )
        rendered = len(results) - len(failures) - downloaded
        typer.echo(
            f"Saved {len(results) - len(failures)} PDF(s): "
            f"{downloaded} downloaded; {rendered} rendered; {len(failures)} failed."
        )
    else:
        typer.echo(f"Saved {len(results) - len(failures)} PDF(s); {len(failures)} failed.")
    if failures:
        raise typer.Exit(code=1)
