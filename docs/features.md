# Feature requests

Track requested features and research possible implementations. Give each feature
its own H2 section, followed immediately by an implementation status. Use
`🔴 Not implemented`, `🟡 Partially implemented`, or `🟢 Implemented`, and update
the status as the code and validation change.

## Continue processing when one URL fails

**Implementation status:** 🟡 Partially implemented — ordinary page failures,
HTTP error responses, and page cleanup errors now allow later URLs to run;
explicit reporting of shared-browser interruptions remains pending. Updated on
2026-10-08 alongside papers mode.

### Requested behavior

When the input contains several URLs, a failure for one URL should be recorded
and processing should continue with the remaining URLs. Successful PDFs should
be retained, and the final report should identify failed URLs and explain why
they failed.

### Current implementation

- [`PDFRenderer._render_page()`](../src/urls2pdf/renderer.py) returns a
  `RenderResult` for navigation timeouts, Playwright errors, filesystem errors,
  and value errors. The batch processes URLs sequentially using a fresh page
  for each URL.
- [`render()`](../src/urls2pdf/cli.py) prints each recorded failure and a final
  success/failure count. It exits with code `1` after processing if any URL
  failed; an all-successful batch exits with code `0`.
- Expected Playwright errors from `page.close()` are recorded separately in
  `RenderResult.cleanup_error`, preserving the original result and continuing.
- Main-document HTTP statuses `>= 400` fail before download or rendering. A
  `None` navigation response is handled without accessing its status.
- [Tests](../tests/test_app.py) and [papers-mode regressions](../tests/test_papers.py)
  cover continuation, HTTP errors, cleanup, PDF write errors, and summaries.
  A real Chromium check also saved valid pages around a local HTTP `404`.

### Implementation research

Playwright distinguishes navigation failures from HTTP error responses:
`page.goto()` can raise for an unreachable server, invalid URL, or timeout,
but HTTP `404` and `500` responses do not raise. The returned response exposes
the status code. Therefore, catching exceptions alone does not cover these
URL failures. See the [official navigation reference](https://playwright.dev/python/docs/api/class-page#page-goto).

Playwright also provides `page.is_closed()` and `page.close()` for page lifecycle
management. A closed-page check can avoid redundant cleanup, but the proposed
implementation should still catch expected cleanup errors because the browser
can disconnect between the check and the close operation. See
[`page.is_closed()`](https://playwright.dev/python/docs/api/class-page#page-is-closed)
and [`page.close()`](https://playwright.dev/python/docs/api/class-page#page-close).

Typer supports explicit exit codes with `typer.Exit(code=1)`. Keeping the existing
nonzero exit after the batch finishes lets scripts detect incomplete output
while allowing other URLs to run. See [Typer's termination documentation](https://typer.tiangolo.com/tutorial/terminating/#exit-with-an-error).

### Proposed implementation

1. Keep continuation as the default behavior using the existing sequential
   renderer and `RenderResult`; no additional dependency is needed.
2. Capture the navigation response. Proposed policy: record main-document HTTP
   statuses `>= 400` as URL failures and skip PDF creation for those responses.
   Handle a `None` response explicitly rather than accessing its status blindly.
3. Guard page cleanup against expected Playwright errors. Preserve any original
   rendering error, record cleanup diagnostics separately, and continue when
   the shared browser/context remains usable. A saved PDF should remain counted
   as saved if only page cleanup fails.
4. Keep input-file, output-directory creation, and browser startup failures as
   batch errors. If the shared browser disconnects, report the interruption and
   unprocessed URLs explicitly; automatic browser restart would require a
   separate recovery design. Let unexpected programming errors and user
   cancellation propagate.
5. Keep the final summary and exit-code contract. If cleanup or a shared-browser
   failure interrupts processing, preserve the results collected so far so
   the report can distinguish saved, failed, and unprocessed URLs.

### Acceptance criteria

- For a valid URL, a failing URL, and another valid URL, both valid PDFs are
  saved, the failed URL is reported, and the CLI exits with code `1` after the
  final URL has been attempted.
- Navigation timeouts, invalid URLs, HTTP `404`/`500` responses, and PDF write
  failures are covered by regression tests that confirm the next URL runs.
- A page cleanup error does not replace the original URL error or prevent the
  next URL from running when the shared browser remains usable.
- All-successful and empty inputs still exit with code `0`; browser startup
  failures produce a clear batch error.
- A local HTTP-server check with real Chromium verifies successful pages around
  an HTTP error response. This check passed during papers-mode implementation.

## Download publisher PDFs with an opt-in papers mode

**Implementation status:** 🟢 Implemented — CLI, configurable publisher rules,
validated atomic downloads, fallbacks, and regression tests are complete. Real
CLI downloads passed for all five Nature examples; saved PDFs were inspected on
2026-10-08.

### Requested behavior

Add `--papers` to `urls2pdf render`. When enabled, prefer an existing publisher
PDF discovered through a domain-specific HTML selector. Otherwise, render the
webpage using the existing defaults. Without `--papers`, retain the current
rendering behavior. Ship a Nature rule and allow users to configure additional
journal hosts without editing Python code.

### Live investigation

Checked the four supplied articles in the collaborative Chromium browser on
2026-10-08. On each page, this selector matched three links to one unique PDF:

```css
a[data-test="download-pdf"][data-article-pdf="true"]
```

Read the actual `href`, resolved it against the article page, and fetched the
complete response using browser `fetch` with same-origin credentials and a
30-second timeout. Every request returned HTTP `200`, `application/pdf`, and
bytes starting with `%PDF-`.

| Article | Journal | Linked PDF filename | Response bytes | PDF header |
| --- | --- | --- | ---: | --- |
| [s41586-024-08443-4](https://www.nature.com/articles/s41586-024-08443-4) | Nature | `s41586-024-08443-4.pdf` | 17,372,212 | `%PDF-1.4` |
| [s41586-026-11129-8](https://www.nature.com/articles/s41586-026-11129-8) | Nature | `s41586-026-11129-8.pdf` | 32,957,369 | `%PDF-1.4` |
| [s41588-026-02712-w](https://www.nature.com/articles/s41588-026-02712-w) | Nature Genetics | `s41588-026-02712-w.pdf` | 4,330,642 | `%PDF-1.4` |
| [s41598-026-71978-1](https://www.nature.com/articles/s41598-026-71978-1) | Scientific Reports | `s41598-026-71978-1_reference.pdf` | 4,862,228 | `%PDF-1.7` |

All filenames above are under `https://www.nature.com/articles/`; none of these
PDF requests changed URL through a redirect. The original
[s41586-026-10858-0 example](https://www.nature.com/articles/s41586-026-10858-0)
also passed the same browser check earlier in this investigation.

The Scientific Reports button links to `_reference.pdf`, whereas its
`citation_pdf_url` metadata names the plain `.pdf` path. Always use the matched
button's URL; neither appending `.pdf` to the article URL nor preferring the
metadata reproduces that button's behavior. Duplicate buttons should cause
only one download. Styling classes are unnecessary for the selector.

These checks establish discovery and retrieval in this browser session. They
do not validate the package's headless context, `context.request` transport,
filesystem saving, or PDF page layout. PDFs were fetched into memory, not saved
or visually inspected. Access may differ by browser session or institutional
network; the plain web fetch for some articles showed subscription content or
failed even though the browser checks succeeded.

### CLI and configuration

Usage:

```fish
urls2pdf render urls.txt --papers
urls2pdf render urls.txt --papers --papers-config journals.toml -o saved_papers
```

Use a built-in hostname-to-rule mapping and an optional explicit TOML file.
Load the file once per batch; merge its rules by normalized hostname, replacing
the complete built-in rule for an existing hostname and adding new hosts.
Avoid implicit config-file discovery in the first version. `--papers-config`
requires `--papers`; it must not silently activate papers mode.

The repository's [example.journals.toml](../example.journals.toml) includes this
Nature rule and a bioRxiv rule using `a.article-dl-pdf-link[href]`. The bioRxiv
selector matched the supplied article's versioned `.full.pdf` link in the
collaborative browser on 2026-10-08. A real CLI run with the example config
downloaded [the supplied article](https://www.biorxiv.org/content/10.64898/2026.10.06.757072v1)
without fallback: 3,785,415 bytes and 50 pages. `pdfinfo` succeeded and the first
page was visually inspected. Enable this rule with `--papers-config`.

Example `journals.toml` using the built-in Nature rule's schema:

```toml
[papers.domains."www.nature.com"]
selectors = ['a[data-test="download-pdf"][data-article-pdf="true"]']
attribute = "href"
```

Each rule contains an ordered, nonempty list of CSS selectors and a URL
attribute, defaulting to `href`. Additional journals get their own hostname
tables with the same schema. The first selector yielding candidates is used;
selectors later in the list are alternatives when earlier ones find nothing.
Match the parsed hostname exactly, case-insensitively; do not use URL substring
matching or apply the Nature selector to every site. Recheck the final page
hostname after navigation so a rule is not used on an unrelated redirect.

Validate file readability, TOML syntax, table shape, hostname keys, nonempty
strings, and supported fields. Configuration errors should be clear CLI usage
errors before browser startup for a nonempty batch. Invalid CSS encountered
during discovery should also identify the host and selector as a configuration
error, rather than silently appearing to be a missing button. Empty input
continues to succeed without starting Chromium.

Python 3.11 already includes
[`tomllib`](https://docs.python.org/3.11/library/tomllib.html), so parsing this
format needs no new dependency.

### Current implementation

1. [`cli.py`](../src/urls2pdf/cli.py) exposes `--papers` (default `False`) and
   `--papers-config PATH`, validates explicit configuration once before browser
   startup, and preserves existing commands, output options, and version behavior.
   Empty input skips config loading and browser startup.
2. [`papers.py`](../src/urls2pdf/papers.py) defines `PaperRule`, the built-in
   Nature mapping, strict TOML validation, discovery, download, and atomic saving.
   Discovery reads all matching DOM attributes in one immediate `page.evaluate()`
   operation per selector, using standard `document.querySelectorAll()` without
   clicks, visibility checks, or element waits. Syntax errors are returned
   separately and reported as configuration errors with the host and selector.
3. [`renderer.py`](../src/urls2pdf/renderer.py) checks the final loaded hostname
   before discovery. Links resolve against `document.baseURI`, preserving
   suffixes and query strings. Empty, malformed, and non-HTTP(S) links are
   rejected; resolved URLs are deduplicated. Multiple distinct candidates cause
   a reported rendering fallback.
4. Downloads use `context.request.get()` with a 30-second timeout, the final
   article URL as Referer, and at most five redirects. Its
   [cookie jar is shared with the browser](https://playwright.dev/python/docs/api/class-apirequestcontext#cookie-management).
   A successful status, PDF or octet-stream content type, and `%PDF-` header
   are required. Bytes come from
   [`APIResponse.body()`](https://playwright.dev/python/docs/api/class-apiresponse#api-response-body);
   responses are disposed in `finally`. This transport passed the real CLI
   checks below, so browser download capture was unnecessary.
5. Validated bytes are saved unchanged to `pdf_filename(original_input_url)`
   through a temporary file in the output directory, then an atomic replacement.
   Temporary files are cleaned up on failures, and a failed download write
   preserves the old destination. Existing overwrite/collision rules remain.
   Publisher PDFs keep their own layout; rendering defaults apply to fallbacks.
6. Unsupported hosts render normally. Discovery ambiguity and expected download
   failures produce fallback diagnostics for supported hosts. Successful
   fallback counts as saved; filesystem write failures remain URL failures.
   Both-path failures retain both diagnostics and allow subsequent URLs to run.
   HTTP error pages fail before either path. Expected page cleanup errors
   preserve the result and are reported separately.
7. `RenderResult` includes defaulted `method`, `pdf_url`, `fallback_reason`, and
   `cleanup_error` fields. Papers mode reports downloaded/rendered/failed counts;
   default mode keeps its existing summary. One browser/context serves the
   batch, pages close in `finally`, and `ExitStack` closes batch resources.
   The [README](../README.md#publisher-pdfs) documents configuration and behavior.

### Acceptance criteria and validation

- Without `--papers`, no publisher lookup or PDF request occurs and existing
  rendering settings and CLI output remain unchanged.
- Nature's repeated buttons cause exactly one request. Both ordinary `.pdf`
  links and Scientific Reports' `_reference.pdf` link are preserved exactly;
  conflicting metadata does not override the selected button.
- Configured rules can add another host and override Nature's selector. Tests
  cover exact hostname matching, unrelated redirects, malformed config, invalid
  selectors, relative/query-bearing URLs, empty links, and ambiguous candidates.
- Mocked responses cover valid PDFs, HTML with HTTP `200`, HTTP errors,
  unexpected content types, empty/truncated bodies, and timeouts. Response and
  page resources close after successful downloads and failures. Header checking
  is only a basic format check; it does not prove a PDF is structurally complete.
- Tests cover fallback reporting/counts, both paths failing while the next URL
  succeeds, filesystem errors, preservation of existing files after failed
  download writes, and empty input without Chromium.
- Before development, run `uv sync --locked`; after code changes, run
  `uv run urls2pdf --help`, `uv run urls2pdf render --help`, and
  `uv run python -m unittest discover -s tests -v`. Run `uv build` if packaging
  changes. Keep `pyproject.toml` and `uv.lock` consistent if dependencies change.
- Perform a separate real CLI check with `urls2pdf install-browser` and
  `uv run urls2pdf render urls.txt --papers` against all four examples plus
  the original article. Verify downloaded bytes, inspect saved PDFs, and record
  any access-dependent failures separately from mocked tests. Also verify
  ordinary rendering without the flag and a mixed batch with an unsupported
  host. These implementation checks passed as recorded below.

### Implementation validation — 2026-10-08

- `uv sync --locked`, both CLI help commands, and the mocked unit suite passed.
  The unit suite now contains 47 tests. No dependency or version change was needed.
- `uv build --offline` built the wheel and source distribution, including the
  publisher module and source-distribution regression tests. The initial online
  build could not resolve PyPI inside the sandbox; cached build dependencies
  completed the build without network access.
- `uv run urls2pdf install-browser` confirmed the installed Chromium. The
  sandbox initially prevented macOS Chromium startup; the real CLI checks ran
  outside that sandbox and wrote only temporary validation artifacts.
- A five-URL file containing all four investigated articles plus
  `s41586-026-10858-0` produced **5 downloaded, 0 rendered, 0 failed**, exit `0`.
  All PDFs passed `pdfinfo`; their first pages were rendered with `pdftoppm`
  and visually inspected. The four previously recorded byte sizes matched.

| Article | Saved bytes | Pages |
| --- | ---: | ---: |
| `s41586-024-08443-4` | 17,372,212 | 36 |
| `s41586-026-11129-8` | 32,957,369 | 38 |
| `s41588-026-02712-w` | 4,330,642 | 20 |
| `s41598-026-71978-1` | 4,862,228 | 25 |
| `s41586-026-10858-0` | 14,616,780 | 24 |

- A local HTTP server verified custom TOML rules, `<base>` resolution, query
  preservation, three duplicate buttons causing exactly one PDF request,
  shared cookies and the article Referer, and byte-for-byte preservation of the
  served PDF. Conflicting metadata did not override the button.
- The configured local batch saved **1 downloaded, 3 rendered, 1 failed**,
  exit `1`: missing links and an HTTP `200` HTML download response rendered
  with diagnostics; a main-page `404` failed; the following valid page saved.
  An invalid CSS selector produced exit `2` identifying its host and selector.
- With the flag off, the same local article rendered without requesting its
  linked PDF and kept the default summary. A mixed Scientific Reports/local
  unsupported-host batch saved **1 downloaded, 1 rendered, 0 failed**, exit `0`.
  Rendered fixtures passed `pdfinfo` as A4 and were visually inspected.
- Nature retrieval was successful in this environment on this date. Session,
  access, network, and publisher markup changes can still cause fallbacks.
  Visual inspection covered first pages, not every page of the publisher PDFs.
