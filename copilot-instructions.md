# Copilot instructions

- Manage dependencies with [uv](https://docs.astral.sh/uv/): use `uv add <package>` to record runtime dependencies and `uv sync` before running examples or tests.
- The entry point is `script.py`, a Click CLI invoked via `uv run script.py ...`. Keep support for the `--output-dir` option when extending commands.
- Prefer `pathlib.Path` for filesystem work and ensure Playwright pages/contexts are closed in `finally` blocks to avoid leaks.
- When adding documentation or examples, demonstrate commands with the Fish shell (the project default) and `uv run` usage.
