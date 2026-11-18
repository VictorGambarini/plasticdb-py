"""Small records embedded in many responses, and the evidence spine.

Evidence flows interaction -> experiment -> observation: an *interaction* is one
actor (organism, enzyme or variant) tested on one plastic in one paper, with a
``result``; its *experiments* are the assays (method and conditions), and each
experiment's *observations* are the measured values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field

from ._base import PlasticDBModel


class PaperRef(PlasticDBModel):
    """A paper as embedded in other records (``PaperRead``)."""

    id: int
    citation: str | None = None
    doi: str | None = None
    year: int | None = None
    title: str | None = None
    authors: str | None = None
    journal: str | None = None
    corresponding_author_country: str | None = None


class PlasticRef(PlasticDBModel):
    """A plastic as embedded in other records: id, name and abbreviation."""

    id: int
    name: str
    abbreviation: str | None = None
    full_name: str | None = None
    polymer_class: str | None = None
    parent_id: int | None = None
    cas_number: str | None = None
    smiles: str | None = None


class NamedRef(PlasticDBModel):
    """An ``{id, name}`` option (``/plastics/names``, ``/microorganisms/names``)."""

    id: int
    name: str


class DegradationLink(PlasticDBModel):
    """Per-plastic evidence summary for one record: counts by outcome."""

    plastic: PlasticRef
    result: str | None = None
    result_polarity: str | None = None
    methods: list[str] = Field(default_factory=list)
    positive_count: int = 0
    negative_count: int = 0
    inconclusive_count: int = 0
    positive_papers: int | None = None
    negative_papers: int | None = None
    inconclusive_papers: int | None = None
    paper_count: int | None = None


def link_columns(links: list[DegradationLink]) -> dict[str, list[str]]:
    """``plastics_positive`` / ``_negative`` / ``_inconclusive``: plastic names by outcome."""
    return {
        "plastics_positive": [lk.plastic.name for lk in links if lk.positive_count > 0],
        "plastics_negative": [lk.plastic.name for lk in links if lk.negative_count > 0],
        "plastics_inconclusive": [lk.plastic.name for lk in links if lk.inconclusive_count > 0],
    }


def paper_columns(papers: list[PaperRef]) -> dict[str, list[Any]]:
    return {
        "paper_ids": [p.id for p in papers],
        "paper_dois": [p.doi for p in papers if p.doi],
    }


class Observation(PlasticDBModel):
    """One measured value of an experiment."""

    measurement_type: str | None = None
    value: float | None = None
    unit: str | None = None
    sd: float | None = None
    n: int | None = None
    control_value: float | None = None
    time_point: float | None = None
    time_point_unit: str | None = None
    text_description: str | None = None


class Experiment(PlasticDBModel):
    """One assay behind an interaction: its method, conditions and observations."""

    id: int
    method: str | None = None
    assay_result: str | None = None
    assay_result_notes: str | None = None
    substrate_form: str | None = None
    temperature: float | None = None
    temperature_unit: str | None = None
    ph: float | None = None
    duration: float | None = None
    duration_unit: str | None = None
    aerobicity: str | None = None
    sole_carbon_source: bool | None = None
    culture_medium: str | None = None
    buffer: str | None = None
    observations: list[Observation] = Field(default_factory=list)


class StrainOccurrenceRef(PlasticDBModel):
    strain_designation: str | None = None
    counts_for_species: bool | None = None
    genetic_background: str | None = None
    genome_assembly_accession: str | None = None
    culture_collection_ids: Any = None
    sample_source: dict[str, Any] | None = None


class Interaction(PlasticDBModel):
    """One actor tested on one plastic in one paper, with its outcome (``result``)."""

    id: int
    actor_type: str | None = None
    result: str | None = None
    plastic: PlasticRef | None = None
    paper: PaperRef | None = None
    paper_id: int | None = None
    strain_designation: str | None = None
    strain_occurrence: StrainOccurrenceRef | None = None
    expression_hosts: list[str] = Field(default_factory=list)
    indirect_source_label: str | None = None
    indirect_source_url: str | None = None
    notes: str | None = None
    experiments: list[Experiment] = Field(default_factory=list)

    _row_exclude: ClassVar[Any] = {"experiments": True}

    def _summary_columns(self) -> dict[str, Any]:
        return {
            "methods": sorted({e.method for e in self.experiments if e.method}),
            "experiment_count": len(self.experiments),
            "observation_count": sum(len(e.observations) for e in self.experiments),
        }

    def experiment_rows(self) -> list[dict[str, Any]]:
        """One row per experiment, prefixed ``experiment.``, with this interaction's columns."""
        base = self.to_row()
        rows = []
        experiments: list[Experiment | None] = [*self.experiments] or [None]
        for exp in experiments:
            row = dict(base)
            if exp is not None:
                data = exp.model_dump(mode="json", exclude={"observations"})
                row.update({f"experiment.{k}": v for k, v in data.items()})
                row["experiment.observation_count"] = len(exp.observations)
            rows.append(row)
        return rows

    def observation_rows(self) -> list[dict[str, Any]]:
        """One row per observation (``experiment.*`` and ``observation.*`` columns)."""
        rows = []
        base = self.to_row()
        experiments: list[Experiment | None] = [*self.experiments] or [None]
        for exp in experiments:
            exp_cols: dict[str, Any] = {}
            observations: list[Observation | None] = [None]
            if exp is not None:
                data = exp.model_dump(mode="json", exclude={"observations"})
                exp_cols = {f"experiment.{k}": v for k, v in data.items()}
                observations = [*exp.observations] or [None]
            for obs in observations:
                row = {**base, **exp_cols}
                if obs is not None:
                    row.update(
                        {f"observation.{k}": v for k, v in obs.model_dump(mode="json").items()}
                    )
                rows.append(row)
        return rows


class ExperimentConditions(PlasticDBModel):
    id: int
    substrate_form: str | None = None
    temperature: float | None = None
    temperature_unit: str | None = None
    ph: float | None = None
    duration: float | None = None
    duration_unit: str | None = None
    buffer: str | None = None
    assay_result: str | None = None
    notes: str | None = None


class VariantRef(PlasticDBModel):
    id: int
    variant_name: str | None = None


class Kinetics(PlasticDBModel):
    """Enzyme kinetics: kcat, Km, kcat/Km and Vmax as reported and normalised."""

    id: int
    protein_id: int | None = None
    protein_variant_id: int | None = None
    protein_variant: VariantRef | None = None
    substrate: str | None = None
    substrate_kind: str | None = None
    substrate_form: str | None = None
    kcat: float | None = None
    kcat_unit: str | None = None
    km: float | None = None
    km_unit: str | None = None
    kcat_km: float | None = None
    kcat_km_unit: str | None = None
    vmax: float | None = None
    vmax_unit: str | None = None
    kcat_normalised: float | None = None
    kcat_normalised_unit: str | None = None
    km_normalised: float | None = None
    km_normalised_unit: str | None = None
    kcat_km_normalised: float | None = None
    temperature: float | None = None
    ph: float | None = None
    plastic: PlasticRef | None = None
    paper: PaperRef | None = None
    paper_id: int | None = None
    experiment: ExperimentConditions | None = None
    experiment_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Properties(PlasticDBModel):
    """Enzyme properties: optimal pH and temperature, Tm, half-life, specific activity."""

    id: int
    protein_id: int | None = None
    protein_variant_id: int | None = None
    protein_variant: VariantRef | None = None
    substrate: str | None = None
    substrate_kind: str | None = None
    optimal_ph: float | None = None
    optimal_temperature: float | None = None
    tm: float | None = None
    t50: float | None = None
    half_life: float | None = None
    half_life_temperature: float | None = None
    specific_activity: float | None = None
    specific_activity_unit: str | None = None
    assay_notes: str | None = None
    paper: PaperRef | None = None
    paper_id: int | None = None
    experiment_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Mutation(PlasticDBModel):
    id: int
    protein_variant_id: int | None = None
    raw: str | None = None
    wild_type_residue: str | None = None
    position: int | None = None
    mutant_residue: str | None = None
    sequence_position: int | None = None


class Structure(PlasticDBModel):
    """A structure record (experimental PDB entry or predicted model)."""

    id: int
    pdb_id: str | None = None
    source: str | None = None
    method: str | None = None
    resolution: float | None = None
    confidence: float | None = None
    is_primary: bool = False
    model_url: str | None = None
    protein_variant_id: int | None = None
    deposition_date: str | None = None


class ProteinOrigin(PlasticDBModel):
    origin_type: str | None = None
    origin_label: str | None = None
    environment_type: str | None = None
    environment_label: str | None = None
    country: str | None = None
    region: str | None = None
    curated: bool | None = None
    inherited_from: str | None = None
    note: str | None = None
    paper: PaperRef | None = None
    source_record: dict[str, Any] | None = None
