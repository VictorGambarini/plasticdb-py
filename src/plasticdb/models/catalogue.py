"""Catalogue records: proteins, variants, microorganisms, plastics and papers.

Each has a *summary* (a list item) and a full record (``get()``). Every model
keeps fields this version does not know about, and almost every field is
optional: the server may add fields and a past release may leave some empty
(named in ``APIResponse.unavailable_fields``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field

from ._base import PlasticDBModel, fasta_record
from .common import (
    DegradationLink,
    Interaction,
    Kinetics,
    Mutation,
    PaperRef,
    PlasticRef,
    Properties,
    ProteinOrigin,
    Structure,
    link_columns,
    paper_columns,
)

_ORGANISM_NESTED = {"degradation_links", "papers", "paper", "environments", "locations"}


def protein_seq_id(accession: str | None, pk: int, *, engineered: bool) -> str:
    """The id the server's FASTA files use: the accession, else ``PlasticDB_[var_]<id>``."""
    if accession:
        return accession
    return f"PlasticDB_var_{pk}" if engineered else f"PlasticDB_{pk}"


# ── microorganisms ──────────────────────────────────────────────────────────


class MicroorganismSummary(PlasticDBModel):
    """A ``/microorganisms`` list item (``MicroorganismListItem``)."""

    id: int
    name: str
    tax_id: int | None = None
    accession: str | None = None
    taxonomy_status: str | None = None
    taxonomy_cause: str | None = None
    taxonomy_note: str | None = None
    growth_temp_opt: float | None = None
    oxygen_requirement: str | None = None
    assay_temp_min: float | None = None
    assay_temp_max: float | None = None
    environments: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    strain_designations: list[str] = Field(default_factory=list)
    degradation_links: list[DegradationLink] = Field(default_factory=list)
    paper: PaperRef | None = None
    papers: list[PaperRef] = Field(default_factory=list)
    updated_at: datetime | None = None

    _row_exclude: ClassVar[Any] = {"degradation_links": True, "papers": True}

    def _summary_columns(self) -> dict[str, Any]:
        return {**link_columns(self.degradation_links), **paper_columns(self.papers)}


class StrainSummary(PlasticDBModel):
    designation: str | None = None
    counts_for_species: bool | None = None
    culture_collection_ids: list[str] = Field(default_factory=list)
    genome_assembly_accessions: list[str] = Field(default_factory=list)
    isolation_sources: list[str] = Field(default_factory=list)
    occurrence_ids: list[int] = Field(default_factory=list)
    papers: list[PaperRef] = Field(default_factory=list)
    plastics: list[dict[str, Any]] = Field(default_factory=list)


class Microorganism(MicroorganismSummary):
    """One microorganism with its evidence (``GET /microorganisms/{ref}``)."""

    lineage: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    also_known_as: list[str] = Field(default_factory=list)
    growth_temp_min: float | None = None
    growth_temp_max: float | None = None
    plastics: list[PlasticRef] = Field(default_factory=list)
    proteins: list[ProteinSummary] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    indirect_interactions: list[Interaction] = Field(default_factory=list)
    construct_interactions: list[Interaction] = Field(default_factory=list)
    strains: list[StrainSummary] = Field(default_factory=list)
    strain_occurrences: list[dict[str, Any]] = Field(default_factory=list)

    def lineage_ranks(self) -> dict[str, str]:
        """``lineage`` (``rank:name; …``) as ``{rank: name}``; a repeated rank keeps its first."""
        out: dict[str, str] = {}
        for part in (self.lineage or "").split(";"):
            rank, sep, name = part.strip().partition(":")
            if sep and rank and name:
                out.setdefault(rank.strip(), name.strip())
        return out


# ── proteins and variants ───────────────────────────────────────────────────


class ProteinSummary(PlasticDBModel):
    """A ``/proteins`` list item (``ProteinListItem``): a wild type or an engineered variant.

    List items carry no ``sequence`` unless the list asked for
    ``include_sequence=True`` (``to_fasta()`` does that itself).
    """

    sequence: str | None = None

    id: int
    name: str
    kind: str = "wild_type"
    accession: str | None = None
    plasticdb_id: str | None = None
    variant_type: str | None = None
    parent_accession: str | None = None
    activity_class: str | None = None
    ec_number: str | None = None
    genbank_id: str | None = None
    uniprot_accession: str | None = None
    pdb_id: str | None = None
    has_signal_peptide: bool | None = None
    best_kcat_km: float | None = None
    best_kcat_km_unit: str | None = None
    year: int | None = None
    source_microorganism: MicroorganismSummary | None = None
    degradation_links: list[DegradationLink] = Field(default_factory=list)
    paper: PaperRef | None = None
    papers: list[PaperRef] = Field(default_factory=list)
    updated_at: datetime | None = None

    _row_exclude: ClassVar[Any] = {
        "degradation_links": True,
        "papers": True,
        "source_microorganism": _ORGANISM_NESTED,
    }

    @property
    def engineered(self) -> bool:
        return self.kind == "engineered"

    @property
    def seq_id(self) -> str:
        """The record's id in the server's FASTA downloads."""
        return protein_seq_id(self.accession, self.id, engineered=self.engineered)

    def to_row(self) -> dict[str, Any]:
        row = super().to_row()
        if row.get("sequence") is None:
            row.pop("sequence", None)  # no column unless the list asked for sequences
        return row

    def _summary_columns(self) -> dict[str, Any]:
        return {**link_columns(self.degradation_links), **paper_columns(self.papers)}


class Protein(ProteinSummary):
    """One protein (``GET /proteins/{accession}``), in the ``summary`` or ``full`` view.

    ``view`` says which. The summary (the default) leaves ``interactions``,
    ``indirect_interactions``, ``variants``, ``kinetics`` and ``properties``
    empty and gives their sizes in ``counts``; ``view="full"`` fills the lists
    (``counts`` is then empty).
    """

    view: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    gene: str | None = None
    database_name: str | None = None
    uniprot_protein_name: str | None = None
    v1_id: str | None = None
    has_structure: bool = False
    structure_file: str | None = None
    signal_peptide_cleavage_site: int | None = None
    active_site_residues: list[Any] | None = None
    uniprot_go_terms: list[Any] | None = None
    cross_references: dict[str, Any] | None = None
    origin: ProteinOrigin | None = None
    plastics: list[PlasticRef] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    indirect_interactions: list[Interaction] = Field(default_factory=list)
    variants: list[VariantSummary] = Field(default_factory=list)
    kinetics: list[Kinetics] = Field(default_factory=list)
    properties: list[Properties] = Field(default_factory=list)
    structures: list[Structure] = Field(default_factory=list)
    variant_degradation_links: list[DegradationLink] = Field(default_factory=list)
    reaction_products: dict[str, Any] | None = None
    identity_sources: list[dict[str, Any]] = Field(default_factory=list)
    model_input: dict[str, Any] | None = None

    def to_fasta(self) -> str:
        """This protein as one FASTA record (``>accession name``); empty without a sequence."""
        if not self.sequence:
            return ""
        return fasta_record(self.seq_id, self.name, self.sequence)


class VariantSummary(PlasticDBModel):
    """An engineered variant as listed under its parent (``ProteinVariantRead``).

    Unlike a :class:`ProteinSummary`, it carries its sequence.
    """

    id: int
    variant_name: str
    variant_type: str | None = None
    accession: str | None = None
    parent_protein_id: int | None = None
    sequence: str | None = None
    description: str | None = None
    engineering_method: str | None = None
    performance: str | None = None
    genbank_id: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    mutations: list[Mutation] = Field(default_factory=list)
    kinetics: list[Kinetics] = Field(default_factory=list)
    properties: list[Properties] = Field(default_factory=list)
    numbering_scheme: str | None = None
    numbering_scheme_code: str | None = None
    numbering_reference: str | None = None
    numbering_offset: int | None = None
    originating_doi: str | None = None
    originating_paper_id: int | None = None
    paper: PaperRef | None = None

    _row_exclude: ClassVar[Any] = {"mutations": True, "kinetics": True, "properties": True}

    @property
    def name(self) -> str:
        return self.variant_name

    @property
    def seq_id(self) -> str:
        return protein_seq_id(self.accession, self.id, engineered=True)

    def _summary_columns(self) -> dict[str, Any]:
        return {
            "mutations": [m.raw for m in self.mutations if m.raw],
            "kinetics_count": len(self.kinetics),
            "properties_count": len(self.properties),
        }

    def to_fasta(self) -> str:
        if not self.sequence:
            return ""
        return fasta_record(self.seq_id, self.variant_name, self.sequence)


class VariantParent(PlasticDBModel):
    id: int
    name: str
    accession: str | None = None
    genbank_id: str | None = None
    uniprot_accession: str | None = None


class Variant(VariantSummary):
    """One engineered variant with its evidence (``GET /variants/{ref}``)."""

    parent_protein: VariantParent | None = None
    activity_class: str | None = None
    has_signal_peptide: bool | None = None
    signal_peptide_cleavage_site: int | None = None
    origin: ProteinOrigin | None = None
    originating_paper: PaperRef | None = None
    originating_paper_source: str | None = None
    plastics: list[PlasticRef] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    degradation_links: list[DegradationLink] = Field(default_factory=list)
    structures: list[Structure] = Field(default_factory=list)
    primary_structure: Structure | None = None
    structure_file: str | None = None
    reaction_products: dict[str, Any] | None = None
    identity_sources: list[dict[str, Any]] = Field(default_factory=list)
    model_input: dict[str, Any] | None = None
    updated_at: datetime | None = None

    @property
    def kind(self) -> str:
        return "engineered"


class EngineeringLineage(PlasticDBModel):
    """A wild-type protein with every engineered variant derived from it."""

    id: int
    name: str
    accession: str | None = None
    plasticdb_id: str | None = None
    genbank_id: str | None = None
    paper: PaperRef | None = None
    variants: list[VariantSummary] = Field(default_factory=list)
    wild_type_kinetics: list[Kinetics] = Field(default_factory=list)
    wild_type_properties: list[Properties] = Field(default_factory=list)


# ── plastics ────────────────────────────────────────────────────────────────


class PlasticFamilyRef(PlasticDBModel):
    id: int
    name: str
    full_name: str | None = None
    kind: str | None = None
    parent_id: int | None = None


class PlasticSummary(PlasticDBModel):
    """A ``/plastics`` list item, with how many organisms and proteins act on it."""

    id: int
    name: str
    full_name: str | None = None
    abbreviation: str | None = None
    kind: str | None = None
    polymer_class: str | None = None
    trade_name: str | None = None
    cas_number: str | None = None
    smiles: str | None = None
    parent_id: int | None = None
    parent_name: str | None = None
    children: list[PlasticFamilyRef] = Field(default_factory=list)
    microorganism_count: int = 0
    protein_count: int = 0
    updated_at: datetime | None = None

    _row_exclude: ClassVar[Any] = {"children": True}

    def _summary_columns(self) -> dict[str, Any]:
        return {"children": [c.name for c in self.children]}


class Plastic(PlasticSummary):
    """One plastic (``GET /plastics/{id}``), with one page of its organisms and proteins.

    The embedded ``microorganisms`` and ``proteins`` are paged by the server
    (``microorganism_limit`` / ``protein_limit``); the ``*_count`` fields are the totals.
    """

    is_placeholder: bool = False
    vocabulary_note: str | None = None
    paper_count: int = 0
    microorganism_degrading_count: int = 0
    microorganism_no_activity_count: int = 0
    microorganism_inconclusive_count: int = 0
    protein_degrading_count: int = 0
    protein_no_activity_count: int = 0
    protein_inconclusive_count: int = 0
    microorganisms: list[MicroorganismSummary] = Field(default_factory=list)
    proteins: list[ProteinSummary] = Field(default_factory=list)
    monomers: list[dict[str, Any]] = Field(default_factory=list)
    components: list[dict[str, Any]] = Field(default_factory=list)
    containing_blends: list[dict[str, Any]] = Field(default_factory=list)
    degradation_links: list[DegradationLink] = Field(default_factory=list)
    chemical_reference: dict[str, Any] | None = None


# ── papers ──────────────────────────────────────────────────────────────────


class PaperSummary(PlasticDBModel):
    """A ``/papers`` list item."""

    id: int
    citation: str | None = None
    title: str | None = None
    doi: str | None = None
    pmid: str | None = None
    year: int | None = None
    authors: str | None = None
    journal: str | None = None
    corresponding_author_country: str | None = None
    updated_at: datetime | None = None


class PaperVariantRef(PlasticDBModel):
    id: int
    variant_name: str
    variant_type: str | None = None
    parent_protein_id: int | None = None
    parent_protein_accession: str | None = None
    engineering_method: str | None = None
    performance: str | None = None


class Paper(PaperSummary):
    """One paper (``GET /papers/{id}``) with the organisms, proteins and variants it reports."""

    microorganisms: list[MicroorganismSummary] = Field(default_factory=list)
    proteins: list[ProteinSummary] = Field(default_factory=list)
    protein_variants: list[PaperVariantRef] = Field(default_factory=list)


class PaperFootprint(PlasticDBModel):
    """Everything one paper contributed (``GET /papers/{id}/footprint``)."""

    paper: PaperSummary
    counts: dict[str, Any] = Field(default_factory=dict)
    microorganisms: list[dict[str, Any]] = Field(default_factory=list)
    strains: list[dict[str, Any]] = Field(default_factory=list)
    proteins: list[dict[str, Any]] = Field(default_factory=list)
    protein_variants: list[dict[str, Any]] = Field(default_factory=list)
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    kinetics: list[dict[str, Any]] = Field(default_factory=list)
    properties: list[dict[str, Any]] = Field(default_factory=list)
    ingestion_lineage: list[dict[str, Any]] = Field(default_factory=list)


Microorganism.model_rebuild()
Protein.model_rebuild()
