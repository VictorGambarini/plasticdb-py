"""The ``plasticdb`` command-line tool (``pip install 'plasticdb[cli]'``).

A thin layer over :class:`plasticdb.Client`: every command is one SDK call,
its result written to stdout as tsv, csv, json, jsonl or fasta. See
``plasticdb --help`` and the README for the commands and the exit codes.
"""

from __future__ import annotations

import sys

MISSING_TYPER = (
    "plasticdb: the command-line tool needs the 'cli' extra, which is not installed.\n"
    "Install it with:  pip install 'plasticdb[cli]'\n"
)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the exit code (``python -m plasticdb`` and the console script)."""
    try:
        import typer  # noqa: F401
    except ImportError:
        sys.stderr.write(MISSING_TYPER)
        return 1
    from ._app import app

    try:
        app(args=argv, prog_name="plasticdb")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        sys.stderr.write(f"{code}\n")
        return 1
    return 0


__all__ = ["main"]
