"""Microorganisms, plastics and papers."""

from __future__ import annotations

import builtins
import os
import warnings
from pathlib import Path
from typing import Any, Literal

from typing_extensions import Unpack

from .. import errors
from .._endpoints import Endpoint
from .._pagination import Collection
from ..models import (
    Microorganism,
    MicroorganismSummary,
    NamedRef,
    Paper,
    PaperFootprint,
    PaperSummary,
    Plastic,
    PlasticSummary,
    TaxonomyGroup,
)
from ._base import Resource
from ._filters import (
    MicroorganismListFilters,
    PaperExportFilters,
    PaperListFilters,
    PlasticListFilters,
    Polarity,
    RowEvidenceScope,
    SortOrder,
    check,
)

_MICRO_LIST = Endpoint("GET", "/microorganisms")
_MICRO_GET = Endpoint("GET", "/microorganisms/{ref}")
_MICRO_NAMES = Endpoint("GET", "/microorganisms/names", live_only=True)
_MICRO_LEVELS = Endpoint("GET", "/microorganisms/lineage-levels", live_only=True)
_MICRO_TAXON = Endpoint("GET", "/microorganisms/taxonomy/{rank}/{name}", live_only=True)

_PLASTIC_LIST = Endpoint("GET", "/plastics")
_PLASTIC_GET = Endpoint("GET", "/plastics/{plastic_id}")
_PLASTIC_NAMES = Endpoint("GET", "/plastics/names", live_only=True)

_PAPER_LIST = Endpoint("GET", "/papers")
_PAPER_GET = Endpoint("GET", "/papers/{paper_id}")
_PAPER_FOOTPRINT = Endpoint("GET", "/papers/{paper_id}/footprint", live_only=True)
_PAPERS_EXPORT = Endpoint("GET", "/papers/export", live_only=True)
_PAPER_EXPORT = Endpoint("GET", "/papers/{paper_id}/export", live_only=True)

CitationFormat = Literal["bibtex", "ris"]


class Microorganisms(Resource):
    """Microorganisms with evidence of acting on plastics."""

    def get(
        self,
        ref: str | int,
        *,
        evidence_scope: RowEvidenceScope = "organism_or_enzyme",
        follow_merges: bool = False,
    ) -> Microorganism:
        """One microorganism by NCBI tax-id or PlasticDB accession.

        ``evidence_scope="whole_organism"`` counts only assays on the organism
        itself, not on enzymes purified from it. Merges behave as in
        :meth:`Proteins.get <plasticdb.resources.Proteins.get>`.
        """
        current: str | int = ref
        for _ in range(5):
            try:
                data = self._client._request(
                    _MICRO_GET, params={"evidence_scope": evidence_scope}, ref=current
                ).data
            except errors.MergedError as exc:
                if not follow_merges:
                    raise
                warnings.warn(
                    f"PlasticDB: {current} was merged into {exc.merged_into}; "
                    f"returning {exc.merged_into}",
                    errors.MergedRecordWarning,
                    stacklevel=2,
                )
                current = exc.merged_into
                continue
            return Microorganism.model_validate(data)
        raise errors.UnexpectedResponseError(f"{ref}: too many merges")

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[MicroorganismListFilters],
    ) -> Collection[MicroorganismSummary]:
        """Microorganisms matching the filters (positive evidence unless ``polarity=`` says)."""
        params = check(filters, MicroorganismListFilters, "microorganisms.list")
        return Collection(
            self._client,
            _MICRO_LIST,
            MicroorganismSummary,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def names(self) -> builtins.list[NamedRef]:
        """Every microorganism's ``{id, name}`` (live only)."""
        data = self._client._request(_MICRO_NAMES).data or []
        return [NamedRef.model_validate(item) for item in data]

    def lineage_levels(
        self,
        *,
        domain: str | None = None,
        kingdom: str | None = None,
        phylum: str | None = None,
        class_: str | None = None,
        order: str | None = None,
        family: str | None = None,
    ) -> dict[str, builtins.list[str]]:
        """The taxon names at each rank, narrowed by the ranks given (live only)."""
        params = {
            "domain": domain,
            "kingdom": kingdom,
            "phylum": phylum,
            "class": class_,
            "order": order,
            "family": family,
        }
        data = self._client._request(_MICRO_LEVELS, params=params).data
        return dict(data) if isinstance(data, dict) else {}

    def taxonomy(self, rank: str, name: str) -> TaxonomyGroup:
        """Aggregate statistics for one taxon, e.g. ``taxonomy("genus", "Pseudomonas")``.

        A renamed taxon is followed to its current name (live only).
        """
        data = self._client._request(_MICRO_TAXON, follow_redirects=True, rank=rank, name=name)
        return TaxonomyGroup.model_validate(data.data)


class Plastics(Resource):
    """The plastic vocabulary, with the organisms and proteins acting on each."""

    def get(
        self,
        plastic_id: int,
        *,
        microorganism_limit: int = 25,
        microorganism_offset: int = 0,
        microorganism_sort_by: Literal["papers", "result", "name"] = "papers",
        microorganism_sort_order: SortOrder = "desc",
        microorganism_polarity: Polarity = "positive",
        protein_limit: int = 25,
        protein_offset: int = 0,
        protein_sort_by: Literal[
            "papers", "result", "plasticdb_id", "accession", "name", "id"
        ] = "id",
        protein_sort_order: SortOrder = "desc",
        protein_polarity: Polarity = "positive",
        evidence_scope: RowEvidenceScope = "organism_or_enzyme",
    ) -> Plastic:
        """One plastic with one page each of its microorganisms and proteins.

        For every organism or protein acting on a plastic, page through
        ``microorganisms.list(plastic_id=…)`` / ``proteins.list(plastic=…)`` instead.
        """
        params = {
            "microorganism_limit": microorganism_limit,
            "microorganism_offset": microorganism_offset,
            "microorganism_sort_by": microorganism_sort_by,
            "microorganism_sort_order": microorganism_sort_order,
            "microorganism_polarity": microorganism_polarity,
            "protein_limit": protein_limit,
            "protein_offset": protein_offset,
            "protein_sort_by": protein_sort_by,
            "protein_sort_order": protein_sort_order,
            "protein_polarity": protein_polarity,
            "evidence_scope": evidence_scope,
        }
        data = self._client._request(_PLASTIC_GET, params=params, plastic_id=plastic_id).data
        return Plastic.model_validate(data)

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[PlasticListFilters],
    ) -> Collection[PlasticSummary]:
        """Plastics matching the filters (``has_evidence=True``: only those with evidence)."""
        params = check(filters, PlasticListFilters, "plastics.list")
        return Collection(
            self._client,
            _PLASTIC_LIST,
            PlasticSummary,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def names(self) -> builtins.list[NamedRef]:
        """Every plastic's ``{id, name}`` (live only)."""
        data = self._client._request(_PLASTIC_NAMES).data or []
        return [NamedRef.model_validate(item) for item in data]


class Papers(Resource):
    """The literature behind the evidence."""

    def get(self, paper_id: int) -> Paper:
        """One paper with the organisms, proteins and variants it reports."""
        return Paper.model_validate(self._client._request(_PAPER_GET, paper_id=paper_id).data)

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[PaperListFilters],
    ) -> Collection[PaperSummary]:
        """Papers matching the filters. ``polarity`` has no default here (every paper)."""
        params = check(filters, PaperListFilters, "papers.list")
        return Collection(
            self._client,
            _PAPER_LIST,
            PaperSummary,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def footprint(self, paper_id: int) -> PaperFootprint:
        """Every record one paper contributed (live only)."""
        data = self._client._request(_PAPER_FOOTPRINT, paper_id=paper_id).data
        return PaperFootprint.model_validate(data)

    def export(
        self,
        paper_id: int | None = None,
        *,
        format: CitationFormat = "bibtex",
        path: str | os.PathLike[str] | None = None,
        **filters: Unpack[PaperExportFilters],
    ) -> str | Path:
        """Citations as BibTeX or RIS: one paper, or every paper matching the filters (live only).

        Returns the text, or writes it to ``path`` and returns the path.
        """
        params: dict[str, Any] = {"format": format}
        path_params: dict[str, Any] = {}
        if paper_id is not None:
            if filters:
                raise TypeError("papers.export(): pass a paper_id or filters, not both")
            endpoint = _PAPER_EXPORT
            path_params["paper_id"] = paper_id
        else:
            params.update(check(filters, PaperExportFilters, "papers.export"))
            endpoint = _PAPERS_EXPORT
        ext = "bib" if format == "bibtex" else "ris"
        if path is not None:
            return self._client._download(
                endpoint, path, params=params, default_name=f"papers.{ext}", **path_params
            )
        data = self._client._request(endpoint, params=params, **path_params).data
        return data.decode("utf-8") if isinstance(data, bytes) else str(data or "")
