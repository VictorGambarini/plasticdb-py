"""The API's one query vocabulary (docs/api-conventions.md), as keyword arguments.

Names are the server's, so a filter means the same thing on every resource and in
the API reference. The one exception is the taxonomic rank ``class``, a Python
keyword, spelled ``class_``. Closed vocabularies with a handful of values are
``Literal``s; larger ones (``evidence_method``, ``substrate_form``, environments)
are plain strings, checked by the server (a 422 names the valid values).
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from typing_extensions import TypedDict

Polarity = Literal["positive", "negative", "inconclusive"]
SortOrder = Literal["asc", "desc"]
ProteinKind = Literal["wild_type", "engineered", "both"]
EvidenceScope = Literal["organism_or_enzyme", "whole_organism", "enzyme_only"]
RowEvidenceScope = Literal["organism_or_enzyme", "whole_organism"]
EvidenceLevel = Literal["all", "direct", "indirect"]
#: The pre-0.1.0 name of :data:`EvidenceLevel` (the argument was ``kind=``).
InteractionKind = EvidenceLevel
ActivityClass = Literal[
    "degrader", "negative_only", "inconclusive_only", "mixed_nonpositive", "untested", "tested"
]
Timestamp = datetime | str


class ProteinFilters(TypedDict, total=False):
    """Filters shared by ``proteins.list`` and ``downloads.proteins``."""

    q: str
    name: str
    plasticdb_id: str
    accession: str
    plastic: str
    include_subtypes: bool
    microorganism: str | int
    lineage: str
    year: int
    min_year: int
    max_year: int
    substrate_form: str
    evidence_method: str
    include_negative: bool
    polarity: Polarity
    activity_class: ActivityClass
    min_kcat: float
    max_km: float
    min_kcat_km: float
    min_vmax: float
    has_structure: bool
    has_sequence: bool
    ec_number: str
    min_tm: float
    max_tm: float
    origin_type: str
    origin_environment: str
    kind: ProteinKind


class ProteinListFilters(ProteinFilters, total=False):
    include_sequenceless: bool
    include_sequence: bool
    sort_by: Literal["year", "name", "accession", "id", "plasticdb_id", "genbank_id"]
    sort_order: SortOrder
    updated_since: Timestamp


class VariantListFilters(TypedDict, total=False):
    """``proteins.list`` filters without ``kind`` (variants are ``kind=engineered``)."""

    q: str
    name: str
    accession: str
    plastic: str
    include_subtypes: bool
    microorganism: str | int
    lineage: str
    year: int
    min_year: int
    max_year: int
    substrate_form: str
    evidence_method: str
    include_negative: bool
    polarity: Polarity
    activity_class: ActivityClass
    min_kcat: float
    max_km: float
    min_kcat_km: float
    min_vmax: float
    has_structure: bool
    has_sequence: bool
    ec_number: str
    min_tm: float
    max_tm: float
    origin_type: str
    origin_environment: str
    include_sequenceless: bool
    include_sequence: bool
    sort_by: Literal["year", "name", "accession", "id"]
    sort_order: SortOrder
    updated_since: Timestamp


class MicroorganismFilters(TypedDict, total=False):
    q: str
    name: str
    plastic: str
    include_subtypes: bool
    plastic_id: int
    tax_id: int
    year: int
    min_year: int
    max_year: int
    temp_class: Literal["psychrophile", "mesophile", "thermophile"]
    environment: str
    location: str
    lineage: str
    domain: str
    kingdom: str
    phylum: str
    class_: str
    order: str
    family: str
    genus: str
    evidence_method: str
    substrate_form: str
    sole_carbon_source: Literal["yes", "no", "not_stated"]
    aerobicity: Literal["aerobic", "anaerobic", "microaerophilic", "not_reported"]
    include_negative: bool
    polarity: Polarity
    evidence_scope: EvidenceScope


class MicroorganismListFilters(MicroorganismFilters, total=False):
    sort_by: Literal["name", "tax_id", "growth_temp_opt", "id"]
    sort_order: SortOrder
    updated_since: Timestamp


class PlasticFilters(TypedDict, total=False):
    q: str
    name: str
    cas_number: str
    plastic_id: int
    polymer_class: str
    parent_id: int
    has_evidence: bool


class PlasticListFilters(PlasticFilters, total=False):
    evidence_scope: RowEvidenceScope
    sort_by: Literal[
        "name", "full_name", "cas_number", "microorganism_count", "protein_count", "id"
    ]
    sort_order: SortOrder
    updated_since: Timestamp


class PaperFilters(TypedDict, total=False):
    q: str
    year: int
    min_year: int
    max_year: int
    doi: str
    journal: str
    authors: str
    country: str
    plastic: str
    include_subtypes: bool
    microorganism: str | int
    protein: str
    evidence_method: str
    polarity: Polarity
    has_quantitative: bool


class PaperListFilters(PaperFilters, total=False):
    sort_by: Literal["year", "citation", "doi", "title", "journal", "id"]
    sort_order: SortOrder
    updated_since: Timestamp


class PaperExportFilters(PaperFilters, total=False):
    updated_since: Timestamp


class VariantDownloadFilters(TypedDict, total=False):
    parent_protein_id: int
    parent_plasticdb_id: str
    variant_type: str
    engineering_method: str


class MaterialFilters(TypedDict, total=False):
    """Filters of the evidence tables (constructs, preparations)."""

    protein: str
    protein_id: int
    protein_variant_id: int
    paper_id: int
    doi: str


class BindingAssayFilters(MaterialFilters, total=False):
    plastic: str
    plastic_id: int
    include_subtypes: bool


class EvidenceListFilters(TypedDict, total=False):
    """Filters of ``evidence.interactions.list`` (and, below, experiments and observations).

    ``evidence_level="direct"|"indirect"`` needs ``protein`` or ``microorganism``.
    The interaction filters apply to a record's interaction; the experiment and
    observation filters ask one of its experiments (observations) to match.
    """

    plastic: str
    include_subtypes: bool
    plastic_id: int
    microorganism: str | int
    protein: str
    evidence_level: EvidenceLevel
    paper_id: int
    doi: str
    polarity: Polarity
    evidence_method: str
    substrate_form: str
    measurement_type: str
    has_quantitative: bool


class ExperimentListFilters(EvidenceListFilters, total=False):
    interaction_id: int


class ObservationListFilters(EvidenceListFilters, total=False):
    interaction_id: int
    experiment_id: int


def resolve_evidence_level(
    evidence_level: EvidenceLevel | None, kind: EvidenceLevel | None, where: str
) -> EvidenceLevel:
    """``evidence_level`` with the deprecated ``kind`` alias folded in (only one is sent)."""
    if kind is None:
        return evidence_level or "all"
    warnings.warn(
        f"{where}(kind=...) is deprecated; use evidence_level=...",
        DeprecationWarning,
        stacklevel=3,
    )
    if evidence_level is not None and evidence_level != kind:
        raise ValueError(
            f"{where}(): evidence_level={evidence_level!r} and kind={kind!r} disagree; "
            "pass evidence_level only"
        )
    return evidence_level or kind


_ALIASES = {"class_": "class"}


def keys_of(schema: Any) -> frozenset[str]:
    return frozenset(schema.__required_keys__ | schema.__optional_keys__)


def check(given: Mapping[str, Any], schema: Any, where: str) -> dict[str, Any]:
    """The filters as query parameters; a name the endpoint does not take is a TypeError."""
    allowed = keys_of(schema)
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise TypeError(
            f"{where}() got unexpected filter(s) {', '.join(unknown)}. "
            f"Valid filters: {', '.join(sorted(allowed))}"
        )
    return {_ALIASES.get(k, k): v for k, v in given.items() if v is not None}
