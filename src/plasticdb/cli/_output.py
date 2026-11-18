"""Turning what an SDK call returns into tsv / csv / json / jsonl / fasta text."""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import io
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .._download import default_mode
from ..models._base import flatten

TABLE = ("tsv", "csv", "json", "jsonl")
RECORD = ("json", "jsonl", "tsv", "csv")
SEQUENCE_TABLE = (*TABLE, "fasta")
SEQUENCE_RECORD = (*RECORD, "fasta")

#: ``-o`` file suffixes that choose a format when ``--format`` is not given.
SUFFIXES = {
    ".tsv": "tsv",
    ".tab": "tsv",
    ".csv": "csv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".fasta": "fasta",
    ".fa": "fasta",
    ".faa": "fasta",
    ".bib": "bibtex",
    ".ris": "ris",
    ".nwk": "newick",
    ".newick": "newick",
}


class OutputError(ValueError):
    """The result cannot be written in the format asked for."""


@dataclasses.dataclass
class Table:
    """A result given as callables, so only the format asked for is fetched."""

    rows: Callable[[], list[dict[str, Any]]]
    records: Callable[[], list[Any]]
    fasta: Callable[[], str] | None = None
    newick: Callable[[], str] | None = None
    #: What ``json`` writes instead of the records (one object, e.g. with provenance).
    document: Callable[[], Any] | None = None


@dataclasses.dataclass
class Text:
    """Text that is the result as-is (a FASTA record, a PDB file, BibTeX)."""

    text: str


def format_for(path: str | None, allowed: Iterable[str], default: str) -> str:
    """The format an ``-o`` file's suffix implies, if it is one of ``allowed``."""
    if path:
        guess = SUFFIXES.get(Path(path).suffix.lower())
        if guess in tuple(allowed):
            return guess
    return default


# ── conversion ──────────────────────────────────────────────────────────────


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        to_dict = getattr(value, "to_dict", None)
        data = to_dict() if callable(to_dict) else dataclasses.asdict(value)
        return jsonable(data)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _row_of(value: Any) -> dict[str, Any]:
    to_row = getattr(value, "to_row", None)
    if callable(to_row):
        row: dict[str, Any] = to_row()
        return row
    data = jsonable(value)
    if isinstance(data, dict):
        return flatten(data)
    return {"value": data}


def as_table(value: Any) -> Table:
    """Any SDK result as a :class:`Table`."""
    if isinstance(value, Table):
        return value
    if isinstance(value, list):
        items = value
        return Table(
            rows=lambda: [_row_of(v) for v in items],
            records=lambda: [jsonable(v) for v in items],
            fasta=_list_fasta(items),
        )
    rows = getattr(value, "rows", None)
    if callable(rows) and not isinstance(value, BaseModel):
        # Collection / ResultTable: rows() and records() fetch every page.
        records = getattr(value, "records", None)
        to_fasta = getattr(value, "to_fasta", None)
        return Table(
            rows=rows,
            records=records if callable(records) else lambda: jsonable(list(value)),
            fasta=_as_text(to_fasta),
        )
    if callable(rows):
        # A model with its own table view (TaxonomyTree): rows are its table,
        # json is the whole record.
        newick = getattr(value, "newick", None)
        return Table(
            rows=rows,
            records=lambda: [jsonable(value)],
            newick=(lambda: str(newick or "")) if hasattr(value, "newick") else None,
        )
    return Table(
        rows=lambda: [_row_of(value)],
        records=lambda: [jsonable(value)],
        fasta=_as_text(getattr(value, "to_fasta", None)),
    )


def _as_text(method: Any) -> Callable[[], str] | None:
    if not callable(method):
        return None
    return lambda: str(method())


def _list_fasta(items: list[Any]) -> Callable[[], str] | None:
    if not items or not all(callable(getattr(i, "to_fasta", None)) for i in items):
        return None
    return lambda: "".join(i.to_fasta() for i in items if getattr(i, "sequence", None))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            return ";".join(_cell(v) for v in value)
        return json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    keys: dict[str, None] = {}
    for row in rows:
        keys.update(dict.fromkeys(row))
    return list(keys)


def delimited(rows: list[dict[str, Any]], delimiter: str) -> str:
    columns = _columns(rows)
    buf = io.StringIO()
    if delimiter == "\t":
        # TSV: one line per record, so tabs and newlines inside a value become spaces.
        def clean(text: str) -> str:
            return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")

        if columns:
            buf.write("\t".join(columns) + "\n")
        for row in rows:
            buf.write("\t".join(clean(_cell(row.get(c))) for c in columns) + "\n")
        return buf.getvalue()
    writer = csv.writer(buf, lineterminator="\n")
    if columns:
        writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return buf.getvalue()


def render(value: Any, fmt: str, *, single: bool = False) -> str:
    """``value`` as text in ``fmt``. ``single``: json is the object, not a one-item list."""
    if isinstance(value, Text):
        return value.text if value.text.endswith("\n") or not value.text else value.text + "\n"
    if isinstance(value, str):
        return value if value.endswith("\n") or not value else value + "\n"
    if fmt == "text":
        if isinstance(value, dict):
            return "".join(f"{k}: {_cell(v)}\n" for k, v in value.items())
        return f"{value}\n"
    if fmt == "json" and single and not isinstance(value, Table):
        return json.dumps(jsonable(value), indent=2, ensure_ascii=False, default=str) + "\n"
    table = as_table(value)
    if fmt == "json" and table.document is not None:
        return json.dumps(jsonable(table.document()), indent=2, ensure_ascii=False) + "\n"
    if fmt == "tsv":
        return delimited(table.rows(), "\t")
    if fmt == "csv":
        return delimited(table.rows(), ",")
    if fmt == "json":
        return json.dumps(table.records(), indent=2, ensure_ascii=False, default=str) + "\n"
    if fmt == "jsonl":
        return "".join(
            json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in table.records()
        )
    if fmt == "fasta":
        if table.fasta is None:
            raise OutputError("this result carries no sequences: FASTA is not available")
        return table.fasta()
    if fmt == "newick":
        if table.newick is None:
            raise OutputError("only a build-tree result can be written as Newick")
        text = table.newick()
        return text if text.endswith("\n") else text + "\n"
    raise OutputError(f"unknown format {fmt!r}")


def write_file(path: str | os.PathLike[str], text: str) -> Path:
    """Write ``text`` to ``path`` atomically (a temporary file renamed into place)."""
    target = Path(path).expanduser()
    if target.is_dir():
        raise OutputError(f"{target} is a directory: give -o a file name")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        default_mode(tmp)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return target
