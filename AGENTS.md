# Repository instructions

urls2pdf is a Python 3.11+ package that saves URL lists as individual PDFs using
Playwright and headless Chromium.

- Manage dependencies with `uv add <package>` and keep `pyproject.toml` and `uv.lock` consistent.
- Preserve the Typer CLI: `install-browser` (with opt-in `--with-deps`), `render URL_FILE`, `--output-dir`/`-o` (default `pdfs/`), and `--version`.
- Prefer `pathlib.Path`. Install Chromium through `sys.executable` so it uses the tool's Python environment.
- Reuse one browser and context per batch; close pages in `finally` and browser resources with `ExitStack`, including failure paths.
- Keep empty input successful without starting Chromium. Continue after individual page failures, report a summary, and exit with status 1 if any page failed.
- Keep `README.md` aligned with input rules, rendering defaults, and filename/overwrite behavior.
- Use Fish syntax in documentation. Show `uv tool install .` followed by `urls2pdf install-browser`; use `urls2pdf` in usage examples and `uv run urls2pdf` during development.

## Validation

Sync before development; run unit tests for code changes and build for packaging changes:

```fish
uv sync --locked
uv run urls2pdf --help
uv run python -m unittest discover -s tests -v
uv build
```

Tests mock browser operations. When real rendering needs validation, install
Chromium, render a small URL file, inspect the PDFs, and report that check separately.
