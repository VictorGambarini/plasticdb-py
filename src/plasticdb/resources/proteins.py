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
    EvidenceLevel,
    ProteinFilters,
    ProteinListFilters,
    VariantListFilters,
    check,
    keys_of,
    resolve_evidence_level,
)

_LIST = Endpoint("GET", "/proteins")
_GET = Endpoint("GET", "/proteins/{accession}")
_INTERACTIONS = Endpoint("GET", "/proteins/{accession}/interactions")
_KINETICS = Endpoint("GET", "/proteins/{accession}/kinetics")
_PROPERTIES = Endpoint("GET", "/proteins/{accession}/properties")
_VARIANTS = Endpoint("GET", "/proteins/{accession}/variants")
_FASTA = Endpoint("GET", "/proteins/{ref}/fasta")
_STRUCTURE = Endpoint("GET", "/proteins/{ref}/structure", live_only=True)
_LINEAGES = Endpoint("GET", "/engineering/proteins", live_only=True)
_LINEAGE = Endpoint("GET", "/engineering/proteins/{accession}", live_only=True)
DOWNLOAD_FASTA = Endpoint("GET", "/downloads/proteins.fasta")

_VARIANT_GET = Endpoint("GET", "/variants/{ref}")
_VARIANT_FASTA = Endpoint("GET", "/variants/{ref}/fasta")
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
        accession: str | int,
        *,
        view: Literal["full", "summary"] = "summary",
        include_sequenceless: bool = False,
        follow_merges: bool = False,
    ) -> Protein | Variant:
        """One protein by accession (``PB…``, any case) or numeric id.

        An engineered variant's accession returns the :class:`~plasticdb.models.Variant`
        (the server redirects there). A merged accession raises
        :class:`~plasticdb.errors.MergedError` naming the survivor; with
        ``follow_merges=True`` the survivor is returned instead, with a
        :class:`~plasticdb.errors.MergedRecordWarning`. A withdrawn record raises
        :class:`~plasticdb.errors.GoneError`. The default
        ``view="summary"`` (the server's, since 2.89.0) leaves out the per-record
        evidence lists (``interactions``, ``indirect_interactions``, ``variants``,
        ``kinetics``, ``properties``) and gives their sizes in ``counts``; page
        them with :meth:`interactions`, :meth:`variants`, :meth:`kinetics` and
        :meth:`properties`. ``view="full"`` embeds them (up to 12.5 MB for a
        well-studied enzyme).
        """
        params = {"view": view, "include_sequenceless": include_sequenceless or None}
        ref: str | int = accession
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
                _warn_merged(str(ref), exc.merged_into)
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
        ``include_sequence=True`` adds each item's ``sequence`` (a variant's own);
        ``.to_fasta()`` / ``.to_biopython()`` then need no second download.
        """
        params = check(filters, ProteinListFilters, "proteins.list")
        return protein_collection(self._client, params, page_size=page_size, limit=limit)

    def interactions(
        self,
        accession: str,
        *,
        evidence_level: EvidenceLevel | None = None,
        include_sequenceless: bool = False,
        page_size: int | None = None,
        limit: int | None = None,
        kind: EvidenceLevel | None = None,
    ) -> InteractionCollection[Interaction]:
        """The protein's evidence: ``direct`` (assayed on it), ``indirect`` (via its organism).

        ``evidence_level`` is ``all`` (default), ``direct`` or ``indirect``.
        ``kind=`` is the deprecated pre-0.1.0 name of the same argument: it
        emits a :class:`DeprecationWarning` and is sent to the server as
        ``evidence_level`` (never both).
        """
        level = resolve_evidence_level(evidence_level, kind, "proteins.interactions")
        return InteractionCollection(
            self._client,
            _INTERACTIONS,
            Interaction,
            params={"evidence_level": level, "include_sequenceless": include_sequenceless or None},
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
        """One protein's FASTA record, with the server's annotated header (pinnable).

        With a pinned release the record comes from that snapshot; a ref not in
        it is a plain :class:`~plasticdb.errors.NotFoundError` (no merge redirect).
        A variant's accession gives the variant's record (:meth:`Variants.fasta`).
        """
        try:
            return _text(self._client._request(_FASTA, ref=ref).data)
        except errors.EngineeredVariantError as exc:
            return self._client.variants.fasta(exc.accession)

    def structure(self, ref: str | int, path: str | os.PathLike[str] | None = None) -> str | Path:
        """The primary structure's PDB coordinates (live only).

        Returns the text, or writes it to ``path`` (a file or a directory) and returns the path.
        A variant's accession gives the variant's structure. For a pinned release use
        ``client.downloads.structure()``.
        """
        try:
            if path is not None:
                return self._client._download(_STRUCTURE, path, ref=ref, default_name=f"{ref}.pdb")
            return _text(self._client._request(_STRUCTURE, ref=ref).data)
        except errors.EngineeredVariantError as exc:
            return self._client.variants.structure(exc.accession, path)

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
    """Engineered variants: records, FASTA and lists answer a pinned release too."""

    def get(
        self, ref: str | int, *, include_sequenceless: bool = False, follow_merges: bool = False
    ) -> Variant:
        """One variant by accession or numeric id.

        Merges behave as in :meth:`Proteins.get`; a withdrawn variant (also one
        whose parent protein was withdrawn) raises :class:`~plasticdb.errors.GoneError`.
        On a past release the record comes from its snapshot: a variant the
        release did not contain is a :class:`~plasticdb.errors.NotFoundError`
        (merges are not followed there), ``include_sequenceless=True`` raises
        :class:`~plasticdb.errors.UnsupportedFilterError`, and the fields it
        cannot fill are in ``client.last_response.unavailable_fields``.
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
        """One variant's FASTA record with the server's annotated header (pinnable)."""
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
