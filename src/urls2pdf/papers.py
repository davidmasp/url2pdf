"""Explicit publisher rules and validated, atomic PDF downloads."""

from dataclasses import dataclass
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import tomllib
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page


class PapersConfigError(ValueError):
    """A malformed publisher rule, including an invalid CSS selector."""


class PaperUnavailable(ValueError):
    """Discovery or retrieval failed; the article can still be rendered."""


@dataclass(frozen=True)
class PaperRule:
    selectors: tuple[str, ...]
    attribute: str = "href"


BUILTIN_RULES = {
    "www.nature.com": PaperRule(
        ('a[data-test="download-pdf"][data-article-pdf="true"]',)
    ),
}


def load_rules(config: Path | None = None) -> dict[str, PaperRule]:
    """Merge an explicit TOML file into the built-ins, replacing entire rules."""
    rules = dict(BUILTIN_RULES)
    if config is None:
        return rules
    try:
        with config.open("rb") as source:
            data = tomllib.load(source)
    except (OSError, ValueError) as exc:
        raise PapersConfigError(f"Cannot read papers config {config}: {exc}") from exc

    def table(value: object, fields: set[str], label: str) -> dict:
        if not isinstance(value, dict) or set(value) - fields:
            raise PapersConfigError(
                f"{config}: {label} must be a table containing only "
                f"{', '.join(sorted(fields))}"
            )
        return value

    root = table(data, {"papers"}, "config")
    papers = table(root.get("papers"), {"domains"}, "papers")
    domains = papers.get("domains")
    if not isinstance(domains, dict):
        raise PapersConfigError(f"{config}: papers.domains must be a hostname-to-rule table")
    seen = set()
    for host, value in domains.items():
        normalized = host.lower()
        # Hostnames, not URL patterns, ports, or wildcard/subdomain matches.
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in normalized.split(".")
        ):
            raise PapersConfigError(f"{config}: invalid hostname {host!r}")
        if normalized in seen:
            raise PapersConfigError(f"{config}: duplicate normalized hostname {host!r}")
        seen.add(normalized)
        rule = table(value, {"selectors", "attribute"}, f"rule for {host}")
        selectors = rule.get("selectors")
        attribute = rule.get("attribute", "href")
        if not isinstance(selectors, list) or not selectors or not all(
            isinstance(selector, str) and selector.strip() for selector in selectors
        ):
            raise PapersConfigError(
                f"{config}: {host} selectors must be a nonempty list of nonempty strings"
            )
        if not isinstance(attribute, str) or not attribute.strip():
            raise PapersConfigError(f"{config}: {host} attribute must be a nonempty string")
        rules[normalized] = PaperRule(tuple(selectors), attribute)
    return rules


def discover_pdf(page: Page, host: str, rule: PaperRule) -> str:
    """Read actual DOM attributes without clicking, visibility checks, or waits."""
    for selector in rule.selectors:
        # Query all matches in one immediate DOM operation. Returning syntax errors
        # separately keeps configuration mistakes distinct from browser failures.
        found = page.evaluate(
            """({selector, attribute}) => {
                let elements;
                try { elements = document.querySelectorAll(selector); }
                catch (error) { return {error: error.message}; }
                return {base: document.baseURI,
                        links: Array.from(elements, el => el.getAttribute(attribute))};
            }""",
            {"selector": selector, "attribute": rule.attribute},
        )
        if "error" in found:
            raise PapersConfigError(
                f"Invalid CSS selector for {host}: {selector!r}: {found['error']}"
            )
        candidates = set()
        for link in found["links"]:
            if not isinstance(link, str) or not link.strip():
                continue
            try:
                candidate = urljoin(found["base"], link.strip())
                parsed = urlsplit(candidate)
                if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
                    parsed.port  # Reject malformed ports before making a request.
                    candidates.add(candidate)
            except ValueError:
                continue
        if len(candidates) > 1:
            raise PaperUnavailable(
                f"Ambiguous PDF links for {host} selector {selector!r}: "
                f"{len(candidates)} distinct URLs"
            )
        if candidates:
            return candidates.pop()
    raise PaperUnavailable(f"No usable publisher PDF link found for {host}")


def download_pdf(
    context: BrowserContext, pdf_url: str, article_url: str, timeout_ms: int
) -> bytes:
    """Use the context's shared cookies; accept only basic PDF-like responses."""
    response = None
    validated = False
    try:
        response = context.request.get(
            pdf_url,
            headers={"Referer": article_url},
            timeout=timeout_ms,
            max_redirects=5,
        )
        if not response.ok:
            raise PaperUnavailable(f"Publisher PDF returned HTTP {response.status}")
        content_type = (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if content_type not in {"application/pdf", "application/octet-stream"}:
            raise PaperUnavailable(
                f"Publisher PDF has unexpected content type {content_type or '(missing)'}"
            )
        body = response.body()
        if not body.startswith(b"%PDF-") or len(body) <= 5:
            raise PaperUnavailable("Publisher response is empty or missing a PDF header")
        validated = True
        return body
    except PlaywrightError as exc:
        raise PaperUnavailable(f"Publisher PDF request failed: {exc}") from exc
    finally:
        if response is not None:
            try:
                response.dispose()
            except PlaywrightError as exc:
                if validated:
                    raise PaperUnavailable(f"Publisher response cleanup failed: {exc}") from exc


def save_pdf(destination: Path, body: bytes) -> None:
    """Replace the destination only after writing the complete publisher bytes."""
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=destination.parent, prefix=".urls2pdf-", suffix=".tmp", delete=False
        ) as target:
            temporary = Path(target.name)
            target.write(body)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
