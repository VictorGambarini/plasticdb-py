from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class PlasticDBModel(BaseModel):
    """Base for response models. Unknown fields are kept: the API only ever adds them."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    #: Fields left out of :meth:`to_row` (summarised by ``_summary_columns`` instead).
    _row_exclude: ClassVar[Any] = frozenset()

    def to_row(self) -> dict[str, Any]:
        """One flat table row: nested objects become ``parent.child`` columns.

        Lists of plain values stay lists; lists of objects are kept as lists of
        dicts unless the model summarises them (for example a protein's
        ``degradation_links`` become ``plastics_positive`` / ``plastics_negative``).
        """
        data = self.model_dump(mode="json", exclude=self._row_exclude or None)
        row = flatten(data)
        row.update(self._summary_columns())
        return row

    def _summary_columns(self) -> dict[str, Any]:
        return {}


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(flatten(value, f"{name}."))
        elif isinstance(value, dict):
            out[name] = None
        else:
            out[name] = value
    return out


def wrap(sequence: str, width: int = 60) -> str:
    return "\n".join(sequence[i : i + width] for i in range(0, len(sequence), width))


def fasta_record(identifier: str, description: str | None, sequence: str) -> str:
    header = f">{identifier} {description}".rstrip() if description else f">{identifier}"
    return f"{header}\n{wrap(sequence)}\n"
