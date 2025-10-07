#!/usr/bin/env python3
"""
url2pdf.py
~~~~~~~~~~

Render a list of URLs to PDFs using Playwright.

Quickstart (via uv)
-------------------
    uv add playwright tqdm click
    uv run playwright install         # downloads browsers (first run)
    uv run script.py urls.txt -o pdfs

* urls.txt      – file that contains one URL per line (comments start with #)
* -o/--output-dir – optional folder where PDFs will be saved (default: ./pdfs)

Author: (vibe coded by David Mas Ponte)
"""

import re
import pathlib
from typing import Iterable, Union
from urllib.parse import urlparse
from tqdm import tqdm
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import click

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def slugify_url(url: str) -> str:
    """
    Turn a URL into a safe filename.
    e.g. https://example.com/page?a=1 → example.com_page_a=1.pdf
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_")
    query = parsed.query.replace("&", "_").replace("=", "-")
    # Combine parts, drop empties, limit length
    parts = [netloc, path, query]
    name = "_".join([p for p in parts if p])
    # Make sure the filename is filesystem‑safe
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    # Truncate to ~200 chars (most filesystems allow 255)
    return (name[:200] or "page") + ".pdf"


def read_urls(file_path: Union[str, pathlib.Path]) -> Iterable[str]:
    """Yield cleaned URLs from the given file."""
    path = pathlib.Path(file_path)
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            yield line


# ----------------------------------------------------------------------
# Main processing function
# ----------------------------------------------------------------------
def urls_to_pdfs(url_file: Union[str, pathlib.Path], out_dir: Union[str, pathlib.Path]):
    out_path = pathlib.Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    urls = list(read_urls(url_file))
    if not urls:
        click.echo("⚠️  No URLs found in the file.")
        return

    click.echo(f"🗂️  Saving PDFs to: {out_path.resolve()}")
    click.echo(f"🔢  {len(urls)} URLs will be processed.\n")

    # ------------------------------------------------------------------
    # Playwright context – we reuse the same browser for all pages.
    # ------------------------------------------------------------------
    with sync_playwright() as p:
        # Choose the engine you prefer – chromium is fastest & most feature‑complete.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            # Optional: emulate a desktop viewport
            viewport={"width": 1280, "height": 800},
            # Optional: set a user‑agent string
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )

        for url in tqdm(urls, desc="Rendering", unit="url"):
            page = None
            try:
                page = context.new_page()
                # Wait up to 30 s for the network to be idle (adjust if needed)
                page.goto(url, wait_until="networkidle", timeout=30_000)
                # Give the page a little extra time for lazy‑loaded images, etc.
                page.wait_for_timeout(1_000)

                # Build a safe filename
                pdf_name = slugify_url(url)
                pdf_path = out_path / pdf_name

                # Save the PDF – the defaults give a print‑ready layout.
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"},
                )
            except PlaywrightTimeoutError:
                tqdm.write(f"⏰ Timeout: {url}")
            except Exception as exc:
                tqdm.write(f"❌ Failed [{url}]: {exc}")
            finally:
                if page is not None:
                    page.close()

        context.close()
        browser.close()

# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("url_file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path))
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    default=pathlib.Path("pdfs"),
    show_default=True,
    help="Directory where generated PDFs will be stored.",
)
def main(url_file: pathlib.Path, output_dir: pathlib.Path):
    """Render every URL in URL_FILE to an individual PDF."""
    urls_to_pdfs(url_file, output_dir)


if __name__ == "__main__":
    main()

