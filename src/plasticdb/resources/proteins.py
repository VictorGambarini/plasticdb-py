from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from typing_extensions import Unpack

from .. import errors
from .._endpoints import Endpoint
from .._pagination import Collection, InteractionCollection, ProteinCollection
from ..models import (
    EngineeringLineage,
    Interaction,
    Kinetics,
    Properties,
    Protein,
    ProteinSummary,
    Variant,
    VariantSummary,
)
from ._base import Resource

if TYPE_CHECKING:
    from .._client import Client
from ._filters import (
    InteractionKind,
    ProteinFilters,
    ProteinListFilters,
    VariantListFilters,
    check,
    keys_of,
)

_LIST = Endpoint("GET", "/proteins")
_GET = Endpoint("GET", "/proteins/{accession}")
_INTERACTIONS = Endpoint("GET", "/proteins/{accession}/interactions")
_KINETICS = Endpoint("GET", "/proteins/{accession}/kinetics")
_PROPERTIES = Endpoint("GET", "/proteins/{accession}/properties")
_VARIANTS = Endpoint("GET", "/proteins/{accession}/variants")
_FASTA = Endpoint("GET", "/proteins/{ref}/fasta", live_only=True)
_STRUCTURE = Endpoint("GET", "/proteins/{ref}/structure", live_only=True)
_LINEAGES = Endpoint("GET", "/engineering/proteins", live_only=True)
_LINEAGE = Endpoint("GET", "/engineering/proteins/{accession}", live_only=True)
DOWNLOAD_FASTA = Endpoint("GET", "/downloads/proteins.fasta")

_VARIANT_GET = Endpoint("GET", "/variants/{ref}", live_only=True)
_VARIANT_FASTA = Endpoint("GET", "/variants/{ref}/fasta", live_only=True)
_VARIANT_STRUCTURE = Endpoint("GET", "/variants/{ref}/structure", live_only=True)

SUB_PAGE_SIZE = 25
MAX_MERGE_HOPS = 5


def _warn_merged(old: str, new: str) -> None:
    warnings.warn(
        f"PlasticDB: {old} was merged into {new}; returning {new}",
        errors.MergedRecordWarning,
        stacklevel=3,
    )


def protein_collection(
    client: Client, filters: dict[str, object], *, page_size: int | None, limit: int | None
) -> ProteinCollection[ProteinSummary]:
    fasta_keys = keys_of(ProteinFilters)
    return ProteinCollection(
        client,
        _LIST,
        ProteinSummary,
        params=filters,
        page_size=page_size,
        limit=limit,
        fasta_endpoint=DOWNLOAD_FASTA,
        fasta_params={k: v for k, v in filters.items() if k in fasta_keys},
    )


class Proteins(Resource):
    """Wild-type proteins and engineered variants (``/proteins``)."""

    def get(
        self,
        accession: str,
        *,
        view: Literal["full", "summary"] = "full",
        include_sequenceless: bool = False,
        follow_merges: bool = False,
    ) -> Protein | Variant:
        """One protein by accession (``PB…``, any case).

        An engineered variant's accession returns the :class:`~plasticdb.models.Variant`
        (the server redirects there). A merged accession raises
        :class:`~plasticdb.errors.MergedError` naming the survivor; with
        ``follow_merges=True`` the survivor is returned instead, with a
        :class:`~plasticdb.errors.MergedRecordWarning`. A withdrawn record raises
        :class:`~plasticdb.errors.GoneError`. ``view="summary"`` leaves out the
        per-record evidence lists (much smaller for well-studied enzymes).
        """
        params = {"view": view, "include_sequenceless": include_sequenceless or None}
        ref = accession
        for _ in range(MAX_MERGE_HOPS):
            try:
                data = self._client._request(_GET, params=params, accession=ref).data
            except errors.EngineeredVariantError as exc:
                return self._client.variants.get(
                    exc.accession,
                    include_sequenceless=include_sequenceless,
                    follow_merges=follow_merges,
                )
            except errors.MergedError as exc:
                if not follow_merges:
                    raise
                _warn_merged(ref, exc.merged_into)
                if exc.kind == "engineered":
                    return self._client.variants.get(
                        exc.merged_into, include_sequenceless=include_sequenceless
                    )
                ref = exc.merged_into
                continue
            return Protein.model_validate(data)
        raise errors.UnexpectedResponseError(f"{accession}: more than {MAX_MERGE_HOPS} merges")

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[ProteinListFilters],
    ) -> ProteinCollection[ProteinSummary]:
        """Proteins matching the filters (wild types and variants; ``kind=`` narrows).

        Like every entity list, only records with a positive result are listed
        unless ``polarity=`` or ``include_negative=True`` says otherwise.
        """
        params = check(filters, ProteinListFilters, "proteins.list")
        return protein_collection(self._client, params, page_size=page_size, limit=limit)

    def interactions(
        self,
        accession: str,
        *,
        kind: InteractionKind = "all",
        include_sequenceless: bool = False,
        page_size: int | None = None,
        limit: int | None = None,
    ) -> InteractionCollection[Interaction]:
        """The protein's evidence: ``direct`` (assayed on it), ``indirect`` (via its organism)."""
        return InteractionCollection(
            self._client,
            _INTERACTIONS,
            Interaction,
            params={"kind": kind, "include_sequenceless": include_sequenceless or None},
            path_params={"accession": accession},
            page_size=page_size or SUB_PAGE_SIZE,
            limit=limit,
        )

    def kinetics(
        self, accession: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[Kinetics]:
        """Kinetic parameters measured on the protein and its variants."""
        return Collection(
            self._client,
            _KINETICS,
            Kinetics,
            path_params={"accession": accession},
            page_size=page_size or SUB_PAGE_SIZE,
            limit=limit,
        )

    def properties(
        self, accession: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[Properties]:
        """Stability and activity properties (optimal pH/temperature, Tm, half-life)."""
        return Collection(
            self._client,
            _PROPERTIES,
            Properties,
            path_params={"accession": accession},
            page_size=page_size or SUB_PAGE_SIZE,
            limit=limit,
        )

    def variants(
        self,
        accession: str,
        *,
        include_sequenceless: bool = False,
        page_size: int | None = None,
        limit: int | None = None,
    ) -> Collection[VariantSummary]:
        """The engineered variants of one wild-type protein, with their sequences."""
        return Collection(
            self._client,
            _VARIANTS,
            VariantSummary,
            params={"include_sequenceless": include_sequenceless or None},
            path_params={"accession": accession},
            page_size=page_size or SUB_PAGE_SIZE,
            limit=limit,
        )

    def fasta(self, ref: str | int) -> str:
        """One protein's FASTA record, with the server's annotated header (live only)."""
        return _text(self._client._request(_FASTA, ref=ref).data)

    def structure(self, ref: str | int, path: str | os.PathLike[str] | None = None) -> str | Path:
        """The primary structure's PDB coordinates (live only).

        Returns the text, or writes it to ``path`` (a file or a directory) and returns the path.
        For a pinned release use ``client.downloads.structure()``.
        """
        if path is not None:
            return self._client._download(_STRUCTURE, path, ref=ref, default_name=f"{ref}.pdb")
        return _text(self._client._request(_STRUCTURE, ref=ref).data)

    def lineage(self, accession: str) -> EngineeringLineage:
        """A wild-type protein with every variant engineered from it (live only)."""
        data = self._client._request(_LINEAGE, accession=accession).data
        return EngineeringLineage.model_validate(data)

    def engineered_parents(
        self,
        *,
        updated_since: str | None = None,
        page_size: int | None = None,
        limit: int | None = None,
    ) -> Collection[ProteinSummary]:
        """Wild-type proteins that have engineered variants (live only)."""
        return Collection(
            self._client,
            _LINEAGES,
            ProteinSummary,
            params={"updated_since": updated_since},
            page_size=page_size,
            limit=limit,
        )


class Variants(Resource):
    """Engineered variants. Records are live only; lists also work on a pinned release."""

    def get(
        self, ref: str | int, *, include_sequenceless: bool = False, follow_merges: bool = False
    ) -> Variant:
        """One variant by accession or numeric id (live only: a past release is a 409).

        Merges behave as in :meth:`Proteins.get`.
        """
        params = {"include_sequenceless": include_sequenceless or None}
        current: str | int = ref
        for _ in range(MAX_MERGE_HOPS):
            try:
                data = self._client._request(_VARIANT_GET, params=params, ref=current).data
            except errors.MergedError as exc:
                if not follow_merges:
                    raise
                _warn_merged(str(current), exc.merged_into)
                current = exc.merged_into
                continue
            return Variant.model_validate(data)
        raise errors.UnexpectedResponseError(f"{ref}: more than {MAX_MERGE_HOPS} merges")

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[VariantListFilters],
    ) -> ProteinCollection[ProteinSummary]:
        """Engineered variants matching the filters (``/proteins?kind=engineered``)."""
        params = check(filters, VariantListFilters, "variants.list")
        params["kind"] = "engineered"
        return protein_collection(self._client, params, page_size=page_size, limit=limit)

    def of(
        self, parent: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[VariantSummary]:
        """The variants of one parent protein, with sequences (same as ``proteins.variants``)."""
        return self._client.proteins.variants(parent, page_size=page_size, limit=limit)

    def fasta(self, ref: str | int) -> str:
        """One variant's FASTA record with the server's annotated header (live only)."""
        return _text(self._client._request(_VARIANT_FASTA, ref=ref).data)

    def structure(self, ref: str | int, path: str | os.PathLike[str] | None = None) -> str | Path:
        """The variant's structure coordinates (live only); see :meth:`Proteins.structure`."""
        if path is not None:
            return self._client._download(
                _VARIANT_STRUCTURE, path, ref=ref, default_name=f"{ref}.pdb"
            )
        return _text(self._client._request(_VARIANT_STRUCTURE, ref=ref).data)


def _text(data: object) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return "" if data is None else str(data)
