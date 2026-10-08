"""Install the Chromium version required by this tool's Playwright package."""

import subprocess
import sys


class BrowserInstaller:
    """Run Playwright inside the same Python environment as urls2pdf."""

    def install(self, *, with_deps: bool = False) -> None:
        command = [sys.executable, "-m", "playwright", "install", "chromium"]
        if with_deps:
            command.append("--with-deps")
        subprocess.run(command, check=True)
