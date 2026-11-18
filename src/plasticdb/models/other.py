"""Evidence tables, pathways, search, statistics and the change feed."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field, field_validator

from ._base import PlasticDBModel, fasta_record

# ── evidence tables ─────────────────────────────────────────────────────────


class BindingMeasurement(PlasticDBModel):
    id: int
    binding_assay_id: int | None = None
    kind: str | None = None
    qualifier: str | None = None
    value: float | None = None
    unit: str | None = None
    sd: float | None = None
    n: int | None = None
    text_description: str | None = None
    source_locator: str | None = None


class BindingAssay(PlasticDBModel):
    """A binding (adsorption) assay of a protein on a plastic, with its measurements."""

    id: int
    experiment_id: int | None = None
    plastic_id: int | None = None
    protein_id: int | None = None
    protein_variant_id: int | None = None
    assay_role: str | None = None
    evidence_kind: str | None = None
    notes: str | None = None
    paper: dict[str, Any] = Field(default_factory=dict)
    substrate: dict[str, Any] = Field(default_factory=dict)
    experiment: dict[str, Any] = Field(default_factory=dict)
    measurements: list[BindingMeasurement] = Field(default_factory=list)
    preparations: list[dict[str, Any]] = Field(default_factory=list)

    _row_exclude: ClassVar[Any] = {"measurements": True, "preparations": True}

    def _summary_columns(self) -> dict[str, Any]:
        return {
            "measurement_count": len(self.measurements),
            "preparation_ids": [p.get("id") for p in self.preparations],
        }


class Construct(PlasticDBModel):
    """An expressed construct: the sequence a paper actually assayed (tags, fusions, frames)."""

    id: int
    name: str | None = None
    protein_id: int | None = None
    protein_variant_id: int | None = None
    paper_id: int | None = None
    sequence: str | None = None
    sequence_role: str | None = None
    sequence_frame: str | None = None
    sequence_status: str | None = None
    certainty: str | None = None
    components: list[dict[str, Any]] = Field(default_factory=list)
    chains: list[dict[str, Any]] = Field(default_factory=list)
    coordinate_mappings: list[dict[str, Any]] = Field(default_factory=list)
    evidence_location: str | None = None
    evidence_text: str | None = None

    @property
    def seq_id(self) -> str:
        return f"construct_{self.id}"

    def to_fasta(self) -> str:
        if not self.sequence:
            return ""
        return fasta_record(self.seq_id, self.name, self.sequence)


class Preparation(PlasticDBModel):
    """The prepared material an assay used: host, purification, tags, modifications."""

    id: int
    name: str | None = None
    protein_id: int | None = None
    protein_variant_id: int | None = None
    paper_id: int | None = None
    construct_id: int | None = None
    expression_host: str | None = None
    purification: str | None = None
    tag_status: str | None = None
    supplier: str | None = None
    product: str | None = None
    lot: str | None = None
    notes: str | None = None
    certainty: str | None = None
    modifications: list[dict[str, Any]] = Field(default_factory=list)


class StructureLink(PlasticDBModel):
    """A structure attached to a construct (``/constructs/{id}/structures``)."""

    id: int | None = None


# ── pathways ────────────────────────────────────────────────────────────────


class PathwaySummary(PlasticDBModel):
    id: int
    plastic_id: int
    plastic_name: str | None = None
    name: str | None = None
    step_count: int = 0


class Compound(PlasticDBModel):
    id: int
    name: str
    formula: str | None = None
    smiles: str | None = None
    description: str | None = None
    is_additive: bool | None = None


class PathwayEnzyme(PlasticDBModel):
    id: int | None = None
    accession: str | None = None
    name: str | None = None


class ReactionStep(PlasticDBModel):
    id: int
    pathway_id: int | None = None
    step_order: int | None = None
    step_type: str | None = None
    description: str | None = None
    ec_number: str | None = None
    enzyme_name: str | None = None
    enzyme_accessions: list[str] = Field(default_factory=list)
    evidence: str | None = None
    protein_id: int | None = None
    protein: PathwayEnzyme | None = None
    substrate_compound_id: int | None = None
    substrate_compound: Compound | None = None
    substrate_compartment: str | None = None
    product_compound_id: int | None = None
    product_compound: Compound | None = None
    product_compartment: str | None = None


class Pathway(PlasticDBModel):
    """A curated degradation pathway: ordered reaction steps from polymer to metabolites."""

    id: int
    plastic_id: int
    name: str | None = None
    description: str | None = None
    organism_id: int | None = None
    steps: list[ReactionStep] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    _row_exclude: ClassVar[Any] = {"steps": True}

    def _summary_columns(self) -> dict[str, Any]:
        return {"step_count": len(self.steps)}


class PathwayGraph(PlasticDBModel):
    """Cytoscape-style ``{nodes, edges}``: each element's attributes are under ``data``."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)

    def to_networkx(self) -> Any:
        """A ``networkx.DiGraph`` (needs ``networkx``); compartments become node attributes."""
        import importlib

        try:
            nx = importlib.import_module("networkx")
        except ImportError as exc:
            raise ImportError("to_networkx() needs networkx: pip install networkx") from exc
        graph = nx.DiGraph()
        for node in self.nodes:
            data = dict(node.get("data") or {})
            graph.add_node(data.pop("id"), **data)
        for edge in self.edges:
            data = dict(edge.get("data") or {})
            graph.add_edge(data.pop("source"), data.pop("target"), **data)
        return graph


# ── search ──────────────────────────────────────────────────────────────────


class SearchHit(PlasticDBModel):
    """One search result. ``route_key`` is what the record's ``get()`` takes."""

    id: int
    entity_type: str
    name: str | None = None
    route_key: str | None = None
    rank: float | None = None
    extra: str | None = None
    match: Any = None
    detail_url: str | None = None

    @field_validator("route_key", mode="before")
    @classmethod
    def _key_as_text(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int) else value


class SearchGroup(PlasticDBModel):
    total: int = 0
    more_query: str | None = None
    items: list[SearchHit] = Field(default_factory=list)


class GroupedSearch(PlasticDBModel):
    """``/search/grouped``: the top hits per entity type."""

    total: int = 0
    scope: str | None = None
    groups: dict[str, SearchGroup] = Field(default_factory=dict)
    plastic_matches: list[Any] = Field(default_factory=list)
    suggestion: str | None = None


# ── statistics ──────────────────────────────────────────────────────────────


class Stats(PlasticDBModel):
    """Headline counts of the public data (``GET /stats``)."""

    proteins: int = 0
    proteins_wild_type: int = 0
    proteins_variants: int = 0
    microorganisms: int = 0
    microorganisms_whole_organism: int = 0
    microorganisms_via_enzyme: int = 0
    plastics: int = 0
    papers: int = 0
    pathways: int = 0
    compounds: int = 0


class ActivityCounts(PlasticDBModel):
    degrader: int = 0
    negative_only: int = 0
    inconclusive_only: int = 0
    mixed_nonpositive: int = 0
    untested: int = 0
    tested: int = 0


class ProteinActivityStats(PlasticDBModel):
    wild_type: ActivityCounts
    engineered: ActivityCounts
    both: ActivityCounts


class TaxonomyGroup(PlasticDBModel):
    """Aggregate statistics for one taxon (``/microorganisms/taxonomy/{rank}/{name}``)."""

    rank: str
    name: str
    microbe_total: int = 0
    evidenced_microbe_total: int = 0
    protein_total: int = 0
    plastic_total: int = 0
    paper_total: int = 0
    top_plastics: list[dict[str, Any]] = Field(default_factory=list)
    top_environments: list[dict[str, Any]] = Field(default_factory=list)


# ── changes ─────────────────────────────────────────────────────────────────


class Change(PlasticDBModel):
    """One record that changed: ``status`` is ``updated``, ``merged`` or ``removed``."""

    entity_type: str
    id: int
    status: str
    accession: str | None = None
    merged_into: str | None = None
    updated_at: datetime | None = None
