"""Publish the repository's CHANGELOG.md as a docs page (one source of truth)."""

from pathlib import Path

import mkdocs_gen_files

text = Path("CHANGELOG.md").read_text(encoding="utf-8")
with mkdocs_gen_files.open("changelog.md", "w") as handle:
    handle.write(text)
