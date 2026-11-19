"""Jobs: annotation, genome comparison, pathway analysis, HMM screens and trees."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from ._base import PlasticDBModel
from .common import DegradationLink, link_columns

if TYPE_CHECKING:
    from .._pagination import ResultTable

#: Every status a job can have. ``done`` and ``error`` are final.
JOB_STATUSES = ("waiting", "running", "done", "error")
FINAL_STATUSES = frozenset({"done", "error"})


class JobHitOrganism(PlasticDBModel):
    """The organism a hit protein was isolated from."""

    id: int
    name: str
    accession: str | None = None
    tax_id: int | None = None


class JobHitProtein(PlasticDBModel):
    """The PlasticDB protein a hit landed on."""

    id: int
    name: str
    plasticdb_id: str | None = None
    accession: str | None = None
    genbank_id: str | None = None
    uniprot_accession: str | None = None
    source_microorganism: JobHitOrganism | None = None
    degradation_links: list[DegradationLink] = Field(default_factory=list)
    paper_count: int = 0


class JobHitVariant(PlasticDBModel):
    """The engineered variant a hit landed on."""

    id: int
    variant_name: str
    accession: str | None = None
    variant_type: str | None = None


class HitEvidence(PlasticDBModel):
    """Evidence counts, by outcome, for one plastic on a hit's protein or variant."""

    plastic: str
    positive: int = 0
    negative: int = 0
    inconclusive: int = 0


class JobHit(PlasticDBModel):
    """One hit of a search job (annotate, compare genomes, HMM screen).

    For ``compare_genomes`` ``source_label`` names the genome. For
    ``hmm_screen`` the subject is a profile (``subject_seq_id`` is its NAME,
    ``profile_accession`` its ACC) and ``calibration_status`` says whether its
    cut-off separates the polymer classes.

    HMM hits are not alignments: ``pident``, ``length``, ``qcov`` and ``scov``
    are always ``None`` (never 0), old jobs included, so identity or coverage
    filters do not apply to them. ``evalue`` and ``bitscore`` are the
    full-sequence values; ``domain_score`` is the best domain's bit score,
    ``hmm_from``/``hmm_to`` its span on the profile and ``env_from``/``env_to``
    its envelope on the submitted sequence (1-based, inclusive). The HMM fields
    are ``None`` on DIAMOND hits and on HMM jobs run before server 2.90.0.
    ``evidence`` summarises,
    per plastic, the evidence on the protein (or variant) the hit landed on; as
    a table row it becomes ``plastics_positive`` / ``_negative`` /
    ``_inconclusive``.
    """

    _row_exclude: ClassVar[Any] = {"evidence": True, "protein": {"degradation_links"}}

    id: int
    query_seq_id: str | None = None
    subject_seq_id: str | None = None
    subject_accession: str | None = None
    source_label: str | None = None
    pident: float | None = None
    length: int | None = None
    evalue: float | None = None
    bitscore: float | None = None
    qcov: float | None = None
    scov: float | None = None
    profile_accession: str | None = None
    domain_score: float | None = None
    domain_c_evalue: float | None = None
    domain_i_evalue: float | None = None
    hmm_from: int | None = None
    hmm_to: int | None = None
    env_from: int | None = None
    env_to: int | None = None
    evidence: list[HitEvidence] = Field(default_factory=list)
    evidence_polarity: str = "none"
    protein: JobHitProtein | None = None
    variant: JobHitVariant | None = None
    calibration_status: str | None = None
    calibration_status_reason: str | None = None

    def _summary_columns(self) -> dict[str, Any]:
        return {
            "plastics_positive": [e.plastic for e in self.evidence if e.positive > 0],
            "plastics_negative": [e.plastic for e in self.evidence if e.negative > 0],
            "plastics_inconclusive": [e.plastic for e in self.evidence if e.inconclusive > 0],
        }

    @property
    def protein_plastics(self) -> dict[str, list[str]]:
        """The hit protein's own per-plastic outcome lists (its ``degradation_links``)."""
        return link_columns(self.protein.degradation_links if self.protein else [])


class GenomeComparisonRow(PlasticDBModel):
    """One genome of a comparison: hit counts per plastic, raw and normalised.

    As a table row, ``counts`` and ``normalised`` become ``counts.<plastic>`` /
    ``normalised.<plastic>`` columns.
    """

    label: str
    hits: int = 0
    positive: int = 0
    not_positive: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    normalised: dict[str, float | None] | None = None
    positive_normalised: float | None = None
    residues: int | None = None
    sequences: int | None = None


class ComparisonUnit(PlasticDBModel):
    key: str
    label: str
    denominator: str


class GenomeComparison(PlasticDBModel):
    """The genome x plastic matrix of a ``compare_genomes`` job (``rows()`` / ``to_pandas()``)."""

    blast_type: str | None = None
    counted: str | None = None
    normalisation: ComparisonUnit | None = None
    normalised: bool = False
    plastics: list[str] = Field(default_factory=list)
    genomes: list[GenomeComparisonRow] = Field(default_factory=list)

    def table(self) -> ResultTable[GenomeComparisonRow]:
        """The genomes as a table: one row per genome."""
        from .._pagination import ResultTable

        return ResultTable(self.genomes, name="comparison")

    def to_pandas(self) -> Any:
        return self.table().to_pandas()

    def to_polars(self) -> Any:
        return self.table().to_polars()


class PathwayStep(PlasticDBModel):
    """One step of a pathway analysis: whether your proteins cover it, and the best hit."""

    order: int
    step_id: int | None = None
    substrate: str | None = None
    product: str | None = None
    substrate_compartment: str | None = None
    product_compartment: str | None = None
    step_type: str | None = None
    enzyme: str | None = None
    ec: str | None = None
    present: bool = False
    best_hit: dict[str, Any] | None = None
    hits_considered: int = 0
    subunits_total: int | None = None
    subunits_matched: list[str] | None = None
    references: int = 0
    reference_organisms: int = 0
    single_organism: bool | None = None


class AnnotatedTaxon(PlasticDBModel):
    """One row of an ``annotate_list`` result: your columns, then PlasticDB's.

    The fields are the downloaded table's own column names (``row["Genus"]``).
    """

    def __getitem__(self, column: str) -> Any:
        return self.model_dump()[column]

    def to_row(self) -> dict[str, Any]:
        return self.model_dump()


class TaxonomyTree(PlasticDBModel):
    """A ``build_tree`` result: a Newick cladogram and the organism x plastic matrix.

    ``data`` maps each organism name to ``{plastic: "yes" | "no"}`` over
    ``columns``. ``to_pandas()`` gives it as a table; ``to_biopython()`` parses
    the Newick with ``Bio.Phylo`` (needs ``plasticdb[bio]``).
    """

    newick: str | None = None
    columns: list[str] = Field(default_factory=list)
    data: dict[str, dict[str, str]] = Field(default_factory=dict)
    organism_count: int | None = None
    leaf_count: int | None = None
    skipped_no_lineage: int | None = None
    excluded_non_organism: int | None = None
    excluded_names: list[str] = Field(default_factory=list)
    merged_into_ancestor: int | None = None

    def rows(self) -> list[dict[str, Any]]:
        """One row per organism: ``organism`` then one ``True``/``False`` column per plastic."""
        return [
            {"organism": name, **{p: cells.get(p) == "yes" for p in self.columns}}
            for name, cells in self.data.items()
        ]

    def to_pandas(self) -> Any:
        from .._pagination import _require

        pd = _require("pandas", "pandas", "to_pandas()")
        return pd.DataFrame.from_records(self.rows(), columns=["organism", *self.columns])

    def to_polars(self) -> Any:
        from .._pagination import _polars_frame, _require

        pl = _require("polars", "polars", "to_polars()")
        return _polars_frame(pl, self.rows())

    def to_biopython(self) -> Any:
        """The tree as a ``Bio.Phylo`` tree."""
        import io

        from .._pagination import _require

        phylo = _require("Bio.Phylo", "bio", "to_biopython()")
        if not self.newick:
            raise ValueError("This tree job has no Newick result")
        return phylo.read(io.StringIO(self.newick), "newick")


class JobInfo(PlasticDBModel):
    """A job as the API describes it (``JobRead``, or ``JobDetail`` with its hits).

    ``release`` / ``release_source`` / ``reference_sha256`` / ``tool_versions``
    are the provenance to report with the results; ``result_metadata`` holds the
    job type's own summary (``polymer_hits``, ``step_table``, ``taxa``, …) and
    the full provenance record under ``provenance``.
    """

    id: str
    job_type: str
    status: str
    created_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    evalue: float | None = None
    pident: float | None = None
    organism_type: str | None = None
    blast_type: str | None = None
    include_variants: bool = False
    genus_column: int | None = None
    species_column: int | None = None
    field_separator: str | None = None
    has_result_file: bool = False
    result_count: int = 0
    result_metadata: dict[str, Any] | None = None
    result_newick: str | None = None
    view_defaults: dict[str, Any] | None = None
    runtime_seconds: float | None = None
    input_filename: str | None = None
    parameters_summary: str | None = None
    result_summary: str | None = None
    results_expire_at: datetime | None = None
    results_expired_at: datetime | None = None
    release: str | None = None
    release_source: str | None = None
    reference_sha256: str | None = None
    tool_versions: dict[str, str] | None = None
    #: Only in a detail answer (``GET /jobs/{id}``): every stored hit.
    results: list[JobHit] | None = None
    comparison: GenomeComparison | None = None

    _row_exclude: ClassVar[Any] = {"results": True, "result_metadata": True, "comparison": True}

    @property
    def finished(self) -> bool:
        return self.status in FINAL_STATUSES


class JobShare(PlasticDBModel):
    """A share link as its owner sees it later (never the token)."""

    id: int
    created_at: datetime
    active: bool
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class JobShareCreated(JobShare):
    """A new share link. ``token`` is shown once only: keep ``url`` now.

    ``url`` is the results page anyone with the link can open; pass it (or the
    token) to ``client.jobs.shared()`` to read the job without a key.
    """

    token: str
    path: str
    url: str | None = None


class SequenceHit(PlasticDBModel):
    """One DIAMOND hit of a synchronous sequence search (BLAST outfmt 6 columns)."""

    query_seq_id: str
    subject_seq_id: str
    pident: float
    length: int
    mismatch: int
    gapopen: int
    qstart: int
    qend: int
    sstart: int
    send: int
    evalue: float
    bitscore: float
    plasticdb_id: str | None = None
    accession: str | None = None
    protein_name: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    detail_url: str | None = None
    variant_type: str | None = None


class SequenceSearchResult(PlasticDBModel):
    """A synchronous sequence search: the hits, and what they were searched against."""

    hits: list[SequenceHit] = Field(default_factory=list)
    release: str | None = None
    release_source: str | None = None
    reference_sha256: str | None = None
    diamond_version: str | None = None
