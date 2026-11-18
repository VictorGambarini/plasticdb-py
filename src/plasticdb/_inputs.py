"""Turning what a caller has (a path, FASTA text, SeqRecords, a table) into job uploads.

The server sniffs gzip by its magic bytes, but labels compare-genomes results
after the file name and the HMM worker reads the ``.gz`` suffix, so every upload
is *named after its contents*: ``.gz`` when (and only when) the bytes are gzip.
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models._base import fasta_record

GZIP_MAGIC = b"\x1f\x8b"

#: The extensions the server strips before ``.gz`` to name a genome (``job_store``).
_INNER_EXTS = frozenset({".fasta", ".fa", ".faa", ".fna", ".fas", ".tsv", ".csv", ".txt", ".tab"})
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_BARE_SEQUENCE = re.compile(r"^[A-Za-z*\-\s]+$")

PathLike = str | os.PathLike[str]


@dataclass(frozen=True)
class Upload:
    """One file to send: its name (after its contents) and how to open it."""

    name: str
    path: Path | None = None
    content: bytes | None = None

    def open(self) -> Any:
        if self.path is not None:
            return self.path.open("rb")
        return io.BytesIO(self.content or b"")

    @property
    def gzipped(self) -> bool:
        if self.path is not None:
            with self.path.open("rb") as fh:
                return fh.read(2) == GZIP_MAGIC
        return (self.content or b"")[:2] == GZIP_MAGIC

    @property
    def size(self) -> int:
        if self.path is not None:
            return self.path.stat().st_size
        return len(self.content or b"")


def safe_stem(filename: str, index: int = 0) -> str:
    """The genome label the server derives from an upload's name (``job_store.safe_stem``)."""
    path = Path(filename or f"genome_{index + 1}")
    if path.suffix.lower() == ".gz":
        path = Path(path.stem)
        stem = path.stem if path.suffix.lower() in _INNER_EXTS else path.name
    else:
        stem = path.stem
    return _UNSAFE.sub("_", stem).strip("._-") or f"genome_{index + 1}"


def _named(stem: str, ext: str, gzipped: bool) -> str:
    return f"{stem}.{ext}.gz" if gzipped else f"{stem}.{ext}"


def _stem_of(name: str) -> str:
    path = Path(name)
    if path.suffix.lower() == ".gz":
        path = Path(path.stem)
    return path.stem if path.suffix.lower() in _INNER_EXTS else path.name


def _is_seqrecord(value: Any) -> bool:
    return hasattr(value, "seq") and hasattr(value, "id")


def _seqrecords_fasta(records: Iterable[Any]) -> str:
    out = []
    for rec in records:
        description = str(getattr(rec, "description", "") or "")
        if description.split(" ", 1)[0] == rec.id:
            description = description[len(rec.id) :].strip()
        out.append(fasta_record(str(rec.id), description or None, str(rec.seq)))
    return "".join(out)


def _looks_like_text(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith(">") or "\n" in stripped.strip()


def to_upload(value: Any, *, ext: str, stem: str = "input", kind: str = "FASTA") -> Upload:
    """One upload from a path, text, bytes, a file object, or SeqRecord(s).

    ``ext`` is ``fasta`` or ``tsv``. A string is a path when such a file exists,
    otherwise text (``>`` FASTA, or several lines); a single line of residues
    becomes a one-record FASTA (``>query``).
    """
    if isinstance(value, os.PathLike):
        return _from_path(Path(value), ext)
    if isinstance(value, bytes | bytearray | memoryview):
        data = bytes(value)
        if not data:
            raise ValueError(f"The {kind} input is empty")
        return Upload(_named(stem, ext, data[:2] == GZIP_MAGIC), content=data)
    if isinstance(value, str):
        if not _looks_like_text(value) and len(value) < 4096 and Path(value).expanduser().is_file():
            return _from_path(Path(value).expanduser(), ext)
        if not value.strip():
            raise ValueError(f"The {kind} input is empty")
        # A table's trailing empty cells are data; only FASTA is trimmed.
        text = value.strip() if ext == "fasta" else value.strip("\r\n")
        if ext == "fasta" and not text.startswith(">"):
            if "\n" not in text and _BARE_SEQUENCE.match(text) and len(text) >= 5:
                text = f">query\n{text}"
            elif "\n" not in text:
                raise FileNotFoundError(
                    f"{value!r} is neither an existing file nor FASTA text (which starts with '>')"
                )
        return Upload(_named(stem, ext, False), content=(text + "\n").encode("utf-8"))
    if hasattr(value, "read"):
        data = value.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        name = getattr(value, "name", None)
        base = _stem_of(os.path.basename(name)) if isinstance(name, str) and name else stem
        return to_upload(bytes(data), ext=ext, stem=base, kind=kind)
    if _is_seqrecord(value):
        return to_upload(_seqrecords_fasta([value]), ext=ext, stem=stem, kind=kind)
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        items = list(value)
        if items and all(_is_seqrecord(v) for v in items):
            return to_upload(_seqrecords_fasta(items), ext=ext, stem=stem, kind=kind)
        if ext == "tsv" and items and all(_is_row(v) for v in items):
            return to_upload(_rows_tsv(items), ext=ext, stem=stem, kind=kind)
        raise TypeError(
            f"A list as {kind} input must hold Bio.SeqRecord objects"
            + (" or rows (sequences of strings)" if ext == "tsv" else "")
            + "; to compare genomes, use jobs.compare_genomes()"
        )
    raise TypeError(
        f"Cannot use {type(value).__name__} as {kind} input: pass a path, text, bytes, "
        "a file object or Bio.SeqRecord(s)"
    )


def _is_row(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _rows_tsv(rows: list[Sequence[Any]]) -> str:
    return "".join(
        "\t".join("" if c is None else str(c).replace("\t", " ") for c in row) + "\n"
        for row in rows
    )


def _from_path(path: Path, ext: str) -> Upload:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"{path} is empty")
    with path.open("rb") as fh:
        gzipped = fh.read(2) == GZIP_MAGIC
    return Upload(_named(_stem_of(path.name), ext, gzipped), path=path)


def genome_uploads(genomes: Any, labels: Sequence[str] | None = None) -> list[Upload]:
    """The uploads of a genome comparison; each name is the genome's label in the results.

    ``genomes`` is a list (paths, texts, SeqRecord lists, …) or a mapping
    ``{label: genome}``. Labels default to the file stem, else ``genome_<n>``.
    """
    if isinstance(genomes, Mapping):
        if labels is not None:
            raise TypeError("Give labels either as the mapping's keys or as labels=, not both")
        labels = [str(k) for k in genomes]
        items = list(genomes.values())
    elif isinstance(genomes, str | bytes | os.PathLike) or _is_seqrecord(genomes):
        raise TypeError("compare_genomes needs a list of at least two genomes")
    else:
        items = list(genomes)
    if len(items) < 2:
        raise ValueError("compare_genomes needs at least two genomes to compare")
    if labels is not None and len(labels) != len(items):
        raise ValueError(f"{len(labels)} labels for {len(items)} genomes")

    uploads: list[Upload] = []
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        upload = to_upload(item, ext="fasta", stem=f"genome_{i + 1}", kind="genome FASTA")
        stem = safe_stem(labels[i], i) if labels is not None else safe_stem(upload.name, i)
        if stem in seen:
            raise ValueError(
                f"Genomes {seen[stem] + 1} and {i + 1} would both be labelled {stem!r}; "
                "labels name the results, so they must be distinct (pass labels=)"
            )
        seen[stem] = i
        name = _named(stem, "fasta", upload.gzipped)
        uploads.append(Upload(name, path=upload.path, content=upload.content))
    return uploads


def sequence_text(value: Any) -> str:
    """A single query sequence (residues only) from a string, FASTA text or a SeqRecord."""
    if _is_seqrecord(value):
        return str(value.seq)
    if not isinstance(value, str):
        raise TypeError("sequence must be a string or a Bio.SeqRecord")
    text = value.strip()
    if text.startswith(">"):
        records = [r for r in text.split(">") if r.strip()]
        if len(records) != 1:
            raise ValueError(
                "search_sequence takes one sequence; submit several with jobs.annotate_gene()"
            )
        lines = records[0].splitlines()[1:]
        text = "".join(line.strip() for line in lines)
    text = "".join(text.split())
    if not text:
        raise ValueError("The sequence is empty")
    return text
