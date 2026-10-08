"""URL input and output filenames."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlparse


@dataclass(frozen=True)
class URLFile:
    """A UTF-8 file containing one URL per line."""

    path: Path

    def read(self) -> list[str]:
        with self.path.open(encoding="utf-8") as source:
            return [
                url
                for line in source
                if (url := line.strip()) and not url.startswith("#")
            ]


def pdf_filename(url: str) -> str:
    """Keep the original script's filesystem-safe URL naming convention."""
    parsed = urlparse(url)
    parts = (
        parsed.netloc.replace(":", "_"),
        parsed.path.strip("/").replace("/", "_"),
        parsed.query.replace("&", "_").replace("=", "-"),
    )
    name = "_".join(part for part in parts if part)
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return (name[:200] or "page") + ".pdf"
