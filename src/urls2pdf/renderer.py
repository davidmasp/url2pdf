"""PDF rendering independent of command-line input and output."""

from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from urls2pdf.papers import (
    PaperRule,
    PapersConfigError,
    PaperUnavailable,
    discover_pdf,
    download_pdf,
    load_rules,
    save_pdf,
)
from urls2pdf.urls import pdf_filename


@dataclass(frozen=True)
class RenderResult:
    """The saved PDF or error for one URL."""

    url: str
    pdf_path: Path | None = None
    error: str | None = None
    method: Literal["downloaded", "rendered"] = "rendered"
    pdf_url: str | None = None
    fallback_reason: str | None = None
    cleanup_error: str | None = None


@dataclass(frozen=True)
class PDFRenderer:
    """Reuse one Chromium context for a batch and close all browser resources."""

    output_dir: Path
    timeout_ms: int = 30_000
    wait_ms: int = 1_000
    papers: bool = False
    papers_rules: dict[str, PaperRule] | None = None

    def render(self, urls: Iterable[str]) -> list[RenderResult]:
        iterator = iter(urls)
        first = next(iterator, None)
        if first is None:
            return []
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright, ExitStack() as resources:
            browser = playwright.chromium.launch(headless=True)
            resources.callback(browser.close)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            resources.callback(context.close)
            rules = {}
            if self.papers:
                rules = self.papers_rules if self.papers_rules is not None else load_rules()
            return [
                self._render_page(context, url, rules)
                for url in chain([first], iterator)
            ]

    def _render_page(
        self, context: BrowserContext, url: str, rules: dict[str, PaperRule]
    ) -> RenderResult:
        page = None
        result = None
        pdf_url = None
        fallback_reason = None
        try:
            page = context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            if response is not None and response.status >= 400:
                raise ValueError(f"Page returned HTTP {response.status}")
            page.wait_for_timeout(self.wait_ms)
            pdf_path = self.output_dir / pdf_filename(url)
            body = None
            if self.papers:
                host = (urlsplit(page.url).hostname or "").lower()
                rule = rules.get(host)
                if rule is not None:
                    try:
                        pdf_url = discover_pdf(page, host, rule)
                        body = download_pdf(
                            context, pdf_url, page.url, self.timeout_ms
                        )
                    except (PaperUnavailable, PlaywrightError) as exc:
                        fallback_reason = str(exc)
                elif (urlsplit(url).hostname or "").lower() in rules:
                    fallback_reason = (
                        f"Redirected to unsupported publisher host {host or '(missing)'}"
                    )
            if body is not None:
                save_pdf(pdf_path, body)
                result = RenderResult(url, pdf_path, method="downloaded", pdf_url=pdf_url)
            else:
                page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={side: "0.5in" for side in ("top", "bottom", "left", "right")},
                )
                result = RenderResult(
                    url, pdf_path, pdf_url=pdf_url, fallback_reason=fallback_reason
                )
        except PapersConfigError:
            raise
        except PlaywrightTimeoutError:
            result = RenderResult(
                url=url, error=f"Timed out after {self.timeout_ms / 1000:g}s",
                pdf_url=pdf_url, fallback_reason=fallback_reason,
            )
        except (PlaywrightError, OSError, ValueError) as exc:
            result = RenderResult(
                url=url, error=str(exc),
                pdf_url=pdf_url, fallback_reason=fallback_reason,
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except PlaywrightError as exc:
                    if result is not None:
                        result = replace(result, cleanup_error=str(exc))
        return result
