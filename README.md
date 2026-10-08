# urls2pdf

Save a list of web pages as individual PDFs using Playwright and Chromium.
Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## Install

From this checkout:

```fish
uv tool install .
urls2pdf install-browser
```

The first command installs the CLI and its Python dependencies in an isolated
tool environment. The second downloads the matching Chromium browser. Run it
again after upgrading the tool if Playwright requires a new browser version.
On Linux, `urls2pdf install-browser --with-deps` also installs system dependencies
and may require root privileges.

If your shell cannot find `urls2pdf`, run `uv tool update-shell` and restart it.

Installation also provides `url2pdf` as an alias for `urls2pdf`, with the same
commands and options.

## Render

Create a UTF-8 text file containing one URL per line. Blank lines and lines
starting with `#` are ignored:

```text
# Pages to save
https://en.wikipedia.org/wiki/Celltech
https://en.wikipedia.org/wiki/Mary_E._Brunkow
https://en.wikipedia.org/wiki/Nobel_Prize_in_Physiology_or_Medicine
```

```fish
urls2pdf render urls.txt
urls2pdf render urls.txt --output-dir saved_pages
```

The default output directory is `pdfs/`; `-o` is an alias for `--output-dir`.
Each page becomes an A4 PDF with backgrounds and half-inch margins. Chromium
waits for network idle for up to 30 seconds, then waits one more second before
printing. Navigation errors, HTTP responses of 400 or higher, and PDF write
errors are recorded as failures. Rendering continues after individual page
failures, reports a summary, and exits with status 1 if any page failed. Page
cleanup errors are reported as warnings and preserve the saved PDF or original
failure. Empty input exits successfully without starting Chromium.

PDF names come from each URL's host, path, and query, for example
`en.wikipedia.org_wiki_Celltech.pdf`. Existing files with the same name are
overwritten; URLs with identical sanitized or truncated names share an output
filename.

### Publisher PDFs

Opt in to downloading a publisher's existing PDF when an article page contains
a supported download link:

```fish
urls2pdf render urls.txt --papers
urls2pdf render urls.txt --papers --papers-config example.journals.toml -o saved_papers
```

The built-in rule matches `www.nature.com`, including Nature, Nature Genetics,
and Scientific Reports article pages. It reads the download button's actual
URL, including suffixes such as `_reference.pdf` and query strings; duplicate
buttons trigger one request. Hostnames match exactly, ignoring case, and are
checked after redirects. Access depends on the current browser session and
network; papers mode does not provide access to restricted articles.

Downloaded PDFs retain the publisher's bytes, page sizes, and layout. The tool
checks the HTTP status, content type (`application/pdf` or
`application/octet-stream`), and `%PDF-` header before saving. This is a basic
format check, not a guarantee of structural completeness. Downloads use a
30-second timeout, at most five redirects, the article Referer, and cookies
shared with the browser context. Validated bytes are written to a temporary file
and replace the destination only after the write succeeds.

Unsupported hosts render normally. Missing or unusable links, multiple distinct
PDF links, and download failures fall back to the same A4 rendering defaults.
For supported hosts, the summary reports the fallback reason. A successful
fallback counts as saved; a write failure or failed render counts as failed.
Papers mode reports separate downloaded, rendered, and failed counts. Output
names and overwrite/collision behavior remain the same in both modes.

The included [example.journals.toml](example.journals.toml) contains Nature and
bioRxiv rules. Use it directly as above, or copy it to `journals.toml` and edit
the rules. The bioRxiv rule uses `a.article-dl-pdf-link[href]` to read the actual
download URL, including its version and `.full.pdf` suffix. It is enabled when
you supply the config file.

Add or override hosts with an explicit UTF-8 TOML file:

```toml
[papers.domains."www.nature.com"]
selectors = ['a[data-test="download-pdf"][data-article-pdf="true"]']
attribute = "href"

[papers.domains."www.biorxiv.org"]
selectors = ['a.article-dl-pdf-link[href]']
```

Each rule needs an ordered, nonempty list of standard CSS selectors. The URL
attribute defaults to `href`. The first selector yielding usable URLs wins;
later selectors are alternatives. Relative links use the document's base URL.
The file is loaded once per nonempty batch and replaces the entire built-in
rule for each matching hostname, while adding other hosts. There is no implicit
config discovery. `--papers-config` requires `--papers`; invalid configuration
is a usage error, and invalid CSS reports the host and selector when encountered.
Empty input skips config loading.

```fish
urls2pdf --help
urls2pdf render --help
urls2pdf install-browser --help
urls2pdf --version
```

## Development

```fish
uv sync --locked
uv run urls2pdf install-browser
uv run urls2pdf render urls.txt
uv run python -m unittest discover -s tests -v
uv build
```

The code lives in `src/urls2pdf`: `cli.py` defines the Typer commands, `urls.py`
loads URL files, `browser.py` installs Chromium, `renderer.py` manages browser
resources and records per-page results, and `papers.py` defines publisher rules,
PDF discovery, and downloads. Runtime dependencies and the console
entry points are declared in `pyproject.toml`; `uv.lock` records the development
environment. Add dependencies with `uv add <package>`.
