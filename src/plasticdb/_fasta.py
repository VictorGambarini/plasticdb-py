from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .models._base import fasta_record

_ID_END = re.compile(r"[\s|]")


def parse_fasta(text: str) -> list[tuple[str, str, str]]:
    """``(seq_id, header, sequence)`` per record; the id ends at whitespace or ``|``."""
    records: list[tuple[str, str, str]] = []
    header: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        if header is not None:
            seq_id = _ID_END.split(header, maxsplit=1)[0]
            records.append((seq_id, header, "".join(chunks)))

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            header, chunks = line[1:].strip(), []
        elif header is not None:
            chunks.append(line)
    flush()
    return records


def write_fasta(records: Iterable[tuple[str, str | None, str]]) -> str:
    return "".join(fasta_record(seq_id, desc, seq) for seq_id, desc, seq in records)


def to_seqrecords(records: Iterable[tuple[str, str | None, str]]) -> list[Any]:
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord

    return [
        SeqRecord(
            Seq(seq),
            id=seq_id,
            name=seq_id,
            description=desc or "",
            annotations={"molecule_type": "protein"},
        )
        for seq_id, desc, seq in records
    ]
