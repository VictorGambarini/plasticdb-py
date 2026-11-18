"""The ``plasticdb`` command tree: a thin layer over :class:`plasticdb.Client`.

Every command calls the SDK and returns what it returns; ``_core.command``
renders it. Filter options come from the SDK's filter ``TypedDict``\\ s and
keyword options from the SDK methods' own signatures, so their names are the
SDK's (and the API's).
"""

from __future__ import annotations

import inspect
import os
import sys
import typing
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import errors
from .._version import __version__
from ..resources import _filters as F
from ..resources.catalogue import Microorganisms, Plastics
from ..resources.downloads import MANIFEST
from ..resources.jobs import JOB_PARAMS, TREE_FILTERS, Job, Jobs
from ..resources.proteins import Proteins, Variants
from . import _config
from ._core import (
    KW,
    OUTPUT_PANEL,
    State,
    _global_params,
    command,
    exit_code_epilog,
    filter_params,
    root_state,
    typed_dict_hints,
)
from ._output import (
    RECORD,
    SEQUENCE_RECORD,
    SEQUENCE_TABLE,
    TABLE,
    Table,
    Text,
    format_for,
    render,
)

# ── shared option types ─────────────────────────────────────────────────────

Limit = Annotated[
    int | None,
    typer.Option("--limit", "-n", help="At most this many records.", rich_help_panel=OUTPUT_PANEL),
]
Dest = Annotated[
    str,
    typer.Option(
        "--output",
        "-o",
        metavar="PATH",
        help="A file, or a directory to save the server's file name in.",
    ),
]
OptionalOutput = Annotated[
    str | None,
    typer.Option(
        "--output",
        "-o",
        metavar="FILE",
        help="Write here instead of stdout.",
        rich_help_panel=OUTPUT_PANEL,
    ),
]


def sdk_options(
    func: Callable[..., Any], *, skip: Iterable[str] = (), panel: str = "Options"
) -> list[inspect.Parameter]:
    """Options for an SDK method's keyword arguments (only the ones given are passed)."""
    skip = {"self", "page_size", "limit", "path", "release", *skip}
    hints = typing.get_type_hints(func)
    keys = {
        name: hints[name]
        for name, p in inspect.signature(func).parameters.items()
        if p.kind is KW and name not in skip and name in hints
    }
    return filter_params(keys, prefix="x__", panel=panel)


def _group(help: str) -> typer.Typer:
    return typer.Typer(help=help, no_args_is_help=True)


app = typer.Typer(
    name="plasticdb",
    help=(
        "Command-line access to PlasticDB (https://v2.plasticdb.org): microorganisms and "
        "proteins linked to plastic biodegradation, and the evidence behind them.\n\n"
        "Data goes to stdout (or -o FILE); messages go to stderr. Pin a dataset release "
        "with --release for reproducible results. The data is CC BY 4.0: "
        "`plasticdb cite`."
    ),
    epilog=exit_code_epilog(),
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"plasticdb {__version__}")
        raise typer.Exit()


def _root(ctx: typer.Context, **kwargs: Any) -> None:
    kwargs.pop("version", None)
    root_state(ctx).merge({k[2:]: v for k, v in kwargs.items()})


_root_params = [
    inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context),
    inspect.Parameter(
        "version",
        KW,
        default=False,
        annotation=Annotated[
            bool,
            typer.Option(
                "--version",
                help="Print the version and exit.",
                is_eager=True,
                callback=_show_version,
            ),
        ],
    ),
    *_global_params(),
]
_root.__signature__ = inspect.Signature(_root_params)  # type: ignore[attr-defined]
_root.__annotations__ = {p.name: p.annotation for p in _root_params}
app.callback()(_root)


def _ref(value: str) -> str | int:
    return int(value) if value.isdigit() else value


# ── proteins ────────────────────────────────────────────────────────────────

proteins = _group("Wild-type proteins and engineered variants.")
app.add_typer(proteins, name="proteins")


@command(
    proteins,
    "list",
    formats=SEQUENCE_TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.ProteinListFilters),
)
def proteins_list(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Proteins matching the filters (positive evidence unless --polarity says otherwise).

    --format fasta gives their sequences.
    """
    return run.client.proteins.list(limit=limit, **filters)


@command(
    proteins,
    "get",
    formats=SEQUENCE_RECORD,
    default_format="json",
    single=True,
    extra=sdk_options(Proteins.get),
)
def proteins_get(
    run: State,
    accession: Annotated[str, typer.Argument(help="Accession (PB…).")],
    extra: dict[str, Any],
) -> Any:
    """One protein (or the engineered variant an accession names)."""
    return run.client.proteins.get(accession, **extra)


@command(proteins, "fasta")
def proteins_fasta(
    run: State,
    ref: Annotated[str, typer.Argument(help="Accession or numeric id.")],
    output: OptionalOutput = None,
) -> Any:
    """One protein's FASTA record with the server's annotated header (live only)."""
    return Text(run.client.proteins.fasta(_ref(ref)))


@command(proteins, "structure")
def proteins_structure(
    run: State,
    ref: Annotated[str, typer.Argument(help="Accession or numeric id.")],
    output: OptionalOutput = None,
) -> Any:
    """The primary structure's PDB coordinates (live only; see `download structure`)."""
    if output:
        path = run.client.proteins.structure(_ref(ref), output)
        run.say(f"Wrote {path}")
        return None
    return Text(str(run.client.proteins.structure(_ref(ref))))


@command(
    proteins,
    "variants",
    formats=SEQUENCE_TABLE,
    default_format="tsv",
    extra=sdk_options(Proteins.variants, skip={"accession"}),
)
def proteins_variants(
    run: State,
    accession: Annotated[str, typer.Argument(help="Parent accession.")],
    extra: dict[str, Any],
    limit: Limit = None,
) -> Any:
    """The engineered variants of one wild-type protein, with sequences."""
    return run.client.proteins.variants(accession, limit=limit, **extra)


@command(proteins, "lineage", formats=RECORD, default_format="json", single=True)
def proteins_lineage(
    run: State, accession: Annotated[str, typer.Argument(help="Parent accession.")]
) -> Any:
    """A wild-type protein with every variant engineered from it (live only)."""
    return run.client.proteins.lineage(accession)


# ── variants ────────────────────────────────────────────────────────────────

variants = _group("Engineered variants.")
app.add_typer(variants, name="variants")


@command(
    variants,
    "list",
    formats=SEQUENCE_TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.VariantListFilters),
)
def variants_list(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Engineered variants matching the filters."""
    return run.client.variants.list(limit=limit, **filters)


@command(
    variants,
    "get",
    formats=SEQUENCE_RECORD,
    default_format="json",
    single=True,
    extra=sdk_options(Variants.get),
)
def variants_get(
    run: State,
    ref: Annotated[str, typer.Argument(help="Accession or numeric id.")],
    extra: dict[str, Any],
) -> Any:
    """One variant (live only)."""
    return run.client.variants.get(_ref(ref), **extra)


@command(variants, "fasta")
def variants_fasta(
    run: State,
    ref: Annotated[str, typer.Argument(help="Accession or numeric id.")],
    output: OptionalOutput = None,
) -> Any:
    """One variant's FASTA record (live only)."""
    return Text(run.client.variants.fasta(_ref(ref)))


@command(variants, "structure")
def variants_structure(
    run: State,
    ref: Annotated[str, typer.Argument(help="Accession or numeric id.")],
    output: OptionalOutput = None,
) -> Any:
    """A variant's structure coordinates (live only)."""
    if output:
        path = run.client.variants.structure(_ref(ref), output)
        run.say(f"Wrote {path}")
        return None
    return Text(str(run.client.variants.structure(_ref(ref))))


@command(variants, "of", formats=SEQUENCE_TABLE, default_format="tsv")
def variants_of(
    run: State,
    parent: Annotated[str, typer.Argument(help="Parent accession.")],
    limit: Limit = None,
) -> Any:
    """The variants of one parent protein, with sequences."""
    return run.client.variants.of(parent, limit=limit)


# ── microorganisms ──────────────────────────────────────────────────────────

microorganisms = _group("Microorganisms with evidence of acting on plastics.")
app.add_typer(microorganisms, name="microorganisms")


@command(
    microorganisms,
    "list",
    formats=TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.MicroorganismListFilters),
)
def microorganisms_list(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Microorganisms matching the filters (positive evidence unless --polarity says)."""
    return run.client.microorganisms.list(limit=limit, **filters)


@command(
    microorganisms,
    "get",
    formats=RECORD,
    default_format="json",
    single=True,
    extra=sdk_options(Microorganisms.get),
)
def microorganisms_get(
    run: State,
    ref: Annotated[str, typer.Argument(help="NCBI tax-id or PlasticDB accession.")],
    extra: dict[str, Any],
) -> Any:
    """One microorganism."""
    return run.client.microorganisms.get(_ref(ref), **extra)


@command(microorganisms, "names", formats=TABLE, default_format="tsv")
def microorganisms_names(run: State) -> Any:
    """Every microorganism's id and name (live only)."""
    return run.client.microorganisms.names()


@command(microorganisms, "taxonomy", formats=RECORD, default_format="json", single=True)
def microorganisms_taxonomy(
    run: State,
    rank: Annotated[str, typer.Argument(help="genus, family, order, class, phylum, …")],
    name: Annotated[str, typer.Argument(help="The taxon, e.g. Pseudomonas.")],
) -> Any:
    """Aggregate statistics for one taxon (live only)."""
    return run.client.microorganisms.taxonomy(rank, name)


# ── plastics ────────────────────────────────────────────────────────────────

plastics = _group("The plastic vocabulary.")
app.add_typer(plastics, name="plastics")


@command(
    plastics,
    "list",
    formats=TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.PlasticListFilters),
)
def plastics_list(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Plastics matching the filters."""
    return run.client.plastics.list(limit=limit, **filters)


@command(
    plastics,
    "get",
    formats=RECORD,
    default_format="json",
    single=True,
    extra=sdk_options(Plastics.get),
)
def plastics_get(
    run: State,
    plastic_id: Annotated[int, typer.Argument(help="Plastic id.")],
    extra: dict[str, Any],
) -> Any:
    """One plastic with a page each of its microorganisms and proteins."""
    return run.client.plastics.get(plastic_id, **extra)


@command(plastics, "names", formats=TABLE, default_format="tsv")
def plastics_names(run: State) -> Any:
    """Every plastic's id and name (live only)."""
    return run.client.plastics.names()


# ── papers ──────────────────────────────────────────────────────────────────

papers = _group("The literature behind the evidence.")
app.add_typer(papers, name="papers")


@command(
    papers,
    "list",
    formats=TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.PaperListFilters),
)
def papers_list(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Papers matching the filters."""
    return run.client.papers.list(limit=limit, **filters)


@command(papers, "get", formats=RECORD, default_format="json", single=True)
def papers_get(run: State, paper_id: Annotated[int, typer.Argument(help="Paper id.")]) -> Any:
    """One paper with the organisms, proteins and variants it reports."""
    return run.client.papers.get(paper_id)


@command(papers, "footprint", formats=RECORD, default_format="json", single=True)
def papers_footprint(run: State, paper_id: Annotated[int, typer.Argument(help="Paper id.")]) -> Any:
    """Every record one paper contributed (live only)."""
    return run.client.papers.footprint(paper_id)


@command(
    papers,
    "export",
    filters=typed_dict_hints(F.PaperExportFilters),
    choices={"fmt": ("bibtex", "ris")},
)
def papers_export(
    run: State,
    filters: dict[str, Any],
    paper_id: Annotated[
        int | None, typer.Argument(help="One paper; omit to export every match.")
    ] = None,
    fmt: Annotated[
        str | None,
        typer.Option("--format", help="bibtex (default) or ris.", show_default=False),
    ] = None,
    output: OptionalOutput = None,
) -> Any:
    """Citations as BibTeX or RIS: one paper, or every paper matching the filters."""
    chosen = fmt or format_for(output, ("bibtex", "ris"), "bibtex")
    fmt_lit: typing.Literal["bibtex", "ris"] = "ris" if chosen == "ris" else "bibtex"
    return Text(str(run.client.papers.export(paper_id, format=fmt_lit, **filters)))


# ── evidence ────────────────────────────────────────────────────────────────

evidence = _group(
    "The evidence spine (interaction -> experiment -> observation) and the material tables."
)
app.add_typer(evidence, name="evidence")


@command(
    evidence,
    "interactions",
    formats=TABLE,
    default_format="tsv",
    extra=sdk_options(Proteins.interactions, skip={"accession"}),
    choices={"level": ("interaction", "experiment", "observation")},
)
def evidence_interactions(
    run: State,
    protein: Annotated[str, typer.Argument(help="Protein accession.")],
    extra: dict[str, Any],
    level: Annotated[
        str,
        typer.Option(help="One row per interaction, experiment or observation (tsv/csv)."),
    ] = "interaction",
    limit: Limit = None,
) -> Any:
    """A protein's interactions with their experiments and observations."""
    coll = run.client.evidence.interactions(protein, limit=limit, **extra)
    return Table(rows=lambda: coll.rows(level), records=coll.records)


@command(evidence, "kinetics", formats=TABLE, default_format="tsv")
def evidence_kinetics(
    run: State,
    protein: Annotated[str, typer.Argument(help="Protein accession.")],
    limit: Limit = None,
) -> Any:
    """Kinetic parameters of a protein and its variants."""
    return run.client.evidence.kinetics(protein, limit=limit)


@command(evidence, "properties", formats=TABLE, default_format="tsv")
def evidence_properties(
    run: State,
    protein: Annotated[str, typer.Argument(help="Protein accession.")],
    limit: Limit = None,
) -> Any:
    """Stability and activity properties of a protein and its variants."""
    return run.client.evidence.properties(protein, limit=limit)


@command(
    evidence,
    "binding-assays",
    formats=TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.BindingAssayFilters),
)
def evidence_binding_assays(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Binding (adsorption) assays."""
    return run.client.evidence.binding_assays(limit=limit, **filters)


@command(evidence, "binding-assay", formats=RECORD, default_format="json", single=True)
def evidence_binding_assay(run: State, assay_id: Annotated[int, typer.Argument()]) -> Any:
    """One binding assay."""
    return run.client.evidence.binding_assay(assay_id)


@command(
    evidence,
    "constructs",
    formats=SEQUENCE_TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.MaterialFilters),
)
def evidence_constructs(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Expressed constructs, with their sequences."""
    return run.client.evidence.constructs(limit=limit, **filters)


@command(evidence, "construct", formats=SEQUENCE_RECORD, default_format="json", single=True)
def evidence_construct(run: State, construct_id: Annotated[int, typer.Argument()]) -> Any:
    """One construct."""
    return run.client.evidence.construct(construct_id)


@command(
    evidence,
    "preparations",
    formats=TABLE,
    default_format="tsv",
    filters=typed_dict_hints(F.MaterialFilters),
)
def evidence_preparations(run: State, filters: dict[str, Any], limit: Limit = None) -> Any:
    """Prepared materials (expression host, purification, tags)."""
    return run.client.evidence.preparations(limit=limit, **filters)


@command(evidence, "preparation", formats=RECORD, default_format="json", single=True)
def evidence_preparation(run: State, preparation_id: Annotated[int, typer.Argument()]) -> Any:
    """One preparation."""
    return run.client.evidence.preparation(preparation_id)


# ── search, pathways, stats, changes ────────────────────────────────────────


@command(app, "search", formats=TABLE, default_format="tsv")
def search(
    run: State,
    q: Annotated[str, typer.Argument(help="What to search for.")],
    grouped: Annotated[
        bool, typer.Option(help="The top hits per record type (JSON) instead of one ranking.")
    ] = False,
    limit: Limit = None,
) -> Any:
    """Full-text search across every record type (live only)."""
    if grouped:
        return Text(render(run.client.search.grouped(q, limit=limit or 10), "json", single=True))
    return run.client.search(q, limit=limit)


pathways = _group("Curated degradation pathways.")
app.add_typer(pathways, name="pathways")


@command(pathways, "list", formats=TABLE, default_format="tsv")
def pathways_list(run: State, limit: Limit = None) -> Any:
    """Every pathway."""
    return run.client.pathways.list(limit=limit)


@command(pathways, "get", formats=RECORD, default_format="json", single=True)
def pathways_get(run: State, pathway_id: Annotated[int, typer.Argument()]) -> Any:
    """One pathway with its ordered reaction steps."""
    return run.client.pathways.get(pathway_id)


@command(pathways, "for-plastic", formats=TABLE, default_format="json")
def pathways_for_plastic(run: State, plastic_id: Annotated[int, typer.Argument()]) -> Any:
    """Every pathway that degrades one plastic."""
    return run.client.pathways.for_plastic(plastic_id)


@command(pathways, "graph", formats=RECORD, default_format="json", single=True)
def pathways_graph(
    run: State,
    plastic_id: Annotated[int, typer.Argument()],
    pathway_id: Annotated[int | None, typer.Option(help="Only this pathway.")] = None,
) -> Any:
    """A plastic's pathways as a graph of compounds and reactions."""
    return run.client.pathways.graph(plastic_id, pathway_id=pathway_id)


@command(
    app,
    "stats",
    formats=RECORD,
    default_format="json",
    single=True,
    choices={
        "view": ("summary", "protein-activity", "charts", "by-country"),
        "evidence_scope": typing.get_args(F.EvidenceScope),
    },
)
def stats(
    run: State,
    view: Annotated[str, typer.Option(help="Which statistics.")] = "summary",
    evidence_scope: Annotated[
        str | None,
        typer.Option(
            help="Which organisms count: credited by an organism or an enzyme assay (default), "
            "by an organism assay, or only through an enzyme (enzyme_only: summary only)."
        ),
    ] = None,
) -> Any:
    """Summary statistics of the live database."""
    scope: Any = evidence_scope or "organism_or_enzyme"
    if view == "protein-activity":
        return run.client.stats.protein_activity()
    if view == "charts":
        return run.client.stats.charts(evidence_scope=scope)
    if view == "by-country":
        return run.client.stats.by_country()
    return run.client.stats.summary(evidence_scope=scope)


@command(
    app,
    "changes",
    formats=TABLE,
    default_format="tsv",
    choices={"entity_type": ("protein", "protein_variant", "microorganism", "plastic", "paper")},
)
def changes(
    run: State,
    since: Annotated[str, typer.Option(help="ISO 8601 time (the previous run's as_of).")],
    entity_type: Annotated[str | None, typer.Option(help="Only this record type.")] = None,
    limit: Limit = None,
) -> Any:
    """Records updated, merged or removed since a time (live only).

    The server time to pass as the next --since is printed on stderr.
    """
    feed = run.client.changes(since, entity_type=entity_type, limit=limit)  # type: ignore[arg-type]
    as_of = feed.as_of
    if as_of:
        run.say(f"as_of: {as_of} (pass it as the next --since)")
    return feed


# ── releases ────────────────────────────────────────────────────────────────

releases = _group("Dataset releases: the named, citable versions of the data.")
app.add_typer(releases, name="releases")


@command(releases, "list", formats=TABLE, default_format="tsv")
def releases_list(run: State, limit: Limit = None) -> Any:
    """Every completed release, newest first."""
    return run.client.releases.list(limit=limit)


@command(releases, "current", formats=RECORD, default_format="json", single=True)
def releases_current(run: State) -> Any:
    """The release the server is serving now."""
    return run.client.releases.current()


@command(releases, "get", formats=RECORD, default_format="json", single=True)
def releases_get(run: State, version: Annotated[str, typer.Argument(help="e.g. 2026.50")]) -> Any:
    """One release with its artefacts."""
    return run.client.releases.get(version)


@command(releases, "status", formats=RECORD, default_format="json", single=True)
def releases_status(run: State) -> Any:
    """Whether a release is being cut right now."""
    return run.client.releases.status()


# ── downloads ───────────────────────────────────────────────────────────────

download = _group(
    "Files: filtered tables, the whole dataset, and a release's checksummed artefacts."
)
app.add_typer(download, name="download")

_TABLES = ("proteins", "microorganisms", "plastics", "papers", "protein-variants")


@command(
    download,
    "table",
    filters=typed_dict_hints(
        F.ProteinFilters,
        F.MicroorganismFilters,
        F.PlasticFilters,
        F.PaperFilters,
        F.VariantDownloadFilters,
    ),
    choices={"table": _TABLES, "fmt": ("csv", "json", "fasta")},
)
def download_table(
    run: State,
    table: Annotated[str, typer.Argument(help="Which table.")],
    filters: dict[str, Any],
    fmt: Annotated[
        str | None, typer.Option("--format", help="csv (default), json, or fasta (proteins).")
    ] = None,
    output: Dest = ".",
) -> Any:
    """One table as the server publishes it (a pinned release: that release's table).

    Each table takes its own filters; an option another table's filter is an error.
    """
    chosen: Any = fmt or format_for(output, ("csv", "json", "fasta"), "csv")
    dl = run.client.downloads
    if table == "proteins":
        path = dl.proteins(output, format=chosen, **filters)
    elif chosen == "fasta":
        raise ValueError("only the proteins table comes as FASTA")
    elif table == "microorganisms":
        path = dl.microorganisms(output, format=chosen, **filters)
    elif table == "plastics":
        path = dl.plastics(output, format=chosen, **filters)
    elif table == "papers":
        path = dl.papers(output, format=chosen, **filters)
    else:
        path = dl.protein_variants(output, format=chosen, **filters)
    run.say(f"Wrote {path}")
    return None


@command(download, "dataset", choices={"fmt": ("zip", "json")})
def download_dataset(
    run: State,
    output: Dest = ".",
    fmt: Annotated[
        str | None,
        typer.Option("--format", help="zip (default: CSVs, README, licence) or json."),
    ] = None,
    max_wait: Annotated[
        float, typer.Option(help="Seconds to wait while the server builds the ZIP.")
    ] = 0,
) -> Any:
    """Every public record. With --release: every artefact that release published, verified.

    Unpinned, this is the live database's ZIP (or JSON). Pinned, -o is a
    directory that receives the release's checksummed files and MANIFEST.json.
    """
    client = run.client
    if client.release is None:
        chosen: Any = fmt or "zip"
        path = client.downloads.dataset(output, format=chosen, max_wait=max_wait)
        run.say(f"Wrote {path}")
        return None
    if fmt is not None:
        raise ValueError("--format applies to the live dataset; a release's files are as published")
    version = client.release
    files = client.downloads.artefacts(version)
    dest = Path(output).expanduser()
    if dest.exists() and not dest.is_dir():
        raise ValueError(f"{dest} is a file: a release's dataset needs a directory")
    dest.mkdir(parents=True, exist_ok=True)
    client.downloads.release_file(MANIFEST, dest / MANIFEST, release=version)
    for n, entry in enumerate(files, 1):
        run.say(f"[{n}/{len(files)}] {entry.path} ({_size(entry.size)})")
        client.downloads.release_file(entry.path, dest / entry.path, release=version)
    run.say(f"Wrote release {version} ({len(files)} files, verified) to {dest}")
    return None


def _size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


@command(download, "artefact")
def download_artefact(
    run: State,
    file: Annotated[str, typer.Argument(help="Path in the release, e.g. parquet/proteins.parquet")],
    output: Dest = ".",
) -> Any:
    """One artefact of a release (the pinned or current one), verified against its SHA-256."""
    path = run.client.downloads.release_file(file, output)
    run.say(f"Wrote {path}")
    return None


@command(download, "manifest", formats=("json",), default_format="json", single=True)
def download_manifest(run: State, output: OptionalOutput = None) -> Any:
    """A release's MANIFEST.json (every artefact with its SHA-256, size and licence)."""
    if output:
        path = run.client.downloads.release_file(MANIFEST, output)
        run.say(f"Wrote {path}")
        return None
    return run.client.downloads.manifest()


@command(download, "structure")
def download_structure(
    run: State,
    structure_id: Annotated[int, typer.Argument(help="Structure id (a record's structures[].id).")],
    output: Dest = ".",
) -> Any:
    """One structure's coordinates as a release published them (gzipped PDB), verified."""
    path = run.client.downloads.structure(structure_id, output)
    run.say(f"Wrote {path}")
    return None


# ── jobs ────────────────────────────────────────────────────────────────────

jobs = _group("Run PlasticDB's analyses (needs an API key).")
app.add_typer(jobs, name="jobs")

JOB_TYPES = (*(name.replace("_", "-") for name in JOB_PARAMS), "build-tree")
_HELPERS = (
    Jobs.annotate_gene,
    Jobs.compare_genomes,
    Jobs.annotate_list,
    Jobs.pathway_analysis,
    Jobs.hmm_screen,
)


def _job_param_hints() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for helper in _HELPERS:
        hints = typing.get_type_hints(helper)
        for name, p in inspect.signature(helper).parameters.items():
            if p.kind is KW and name not in {"release", "idempotency_key"}:
                out.setdefault(name, hints[name])
    return out


def _tree_hints() -> dict[str, Any]:
    known = typed_dict_hints(F.MicroorganismListFilters)
    out: dict[str, Any] = {"microorganism_ids": list[int]}
    for name in TREE_FILTERS:
        if name != "microorganism_ids":
            out[name] = known.get(name, str)
    return out


JOB_OPTIONS = _job_param_hints()
TREE_OPTIONS = _tree_hints()


def _job_help() -> dict[str, str]:
    users: dict[str, list[str]] = {}
    for kind, names in JOB_PARAMS.items():
        for name in names:
            users.setdefault(name, []).append(kind.replace("_", "-"))
    users["labels"] = ["compare-genomes"]
    text = {name: "For " + ", ".join(kinds) + "." for name, kinds in users.items()}
    text["labels"] = "A genome's name in the results, one per input, in order (compare-genomes)."
    return text


_JOB_EXTRA = [
    *filter_params(
        {k: v for k, v in JOB_OPTIONS.items() if k != "labels"},
        prefix="x__",
        panel="Job parameters",
        help=_job_help(),
    ),
    *filter_params(
        {"label": list[str]},
        prefix="x__",
        panel="Job parameters",
        help={"label": _job_help()["labels"]},
    ),
    *filter_params(
        TREE_OPTIONS,
        prefix="x__",
        panel="build-tree filters",
        help={"microorganism_ids": "Organism ids (repeat the option)."},
    ),
]
_RESULT_FORMATS = (*TABLE, "newick")


def _read_input(value: str) -> Any:
    if value == "-":
        return typer.get_binary_stream("stdin").read()
    path = Path(value).expanduser()
    if not path.is_file():
        raise typer.BadParameter(f"no such file: {value}", param_hint="INPUTS")
    return path


def _results_text(job: Job, fmt: str | None, output: str | None, comparison: bool = False) -> Text:
    tree = job.job_type == "build_tree"
    default = "newick" if tree else "tsv"
    allowed = _RESULT_FORMATS if tree else TABLE
    chosen = fmt or format_for(output, allowed, default)
    if chosen == "newick" and not tree:
        raise ValueError("only a build-tree result can be written as Newick")
    if comparison:
        return Text(render(job.comparison().table(), chosen))
    return Text(render(job.results(), chosen))


@command(
    jobs,
    "submit",
    extra=_JOB_EXTRA,
    choices={"job_type": JOB_TYPES, "fmt": _RESULT_FORMATS},
)
def jobs_submit(
    run: State,
    job_type: Annotated[str, typer.Argument(metavar="TYPE", help="The analysis.")],
    extra: dict[str, Any],
    inputs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Input file(s): FASTA (plain or gzip) or a taxa table; - reads stdin. "
            "compare-genomes takes 2-20, build-tree none."
        ),
    ] = None,
    wait: Annotated[bool, typer.Option(help="Wait for the job, then write its results.")] = False,
    timeout: Annotated[
        float | None, typer.Option(help="With --wait: give up after this many seconds.")
    ] = None,
    fmt: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="With --wait: the results as tsv (default), csv, json, jsonl or newick "
            "(build-tree). Without --format, -o saves the server's results file.",
            rich_help_panel=OUTPUT_PANEL,
            show_default=False,
        ),
    ] = None,
    output: OptionalOutput = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option(
            help="Name this submission (1-255 printable ASCII characters). Resubmitting with "
            "the same key within 24 hours returns the first job instead of a duplicate "
            "(default: a new UUID, reused on the client's own retries).",
        ),
    ] = None,
) -> Any:
    """Submit a job. Prints its id; with --wait, its results (or -o saves the results file).

    The job runs against the --release's archived databases when one is given.
    """
    kind = job_type.replace("-", "_")
    files = list(inputs or [])
    tree_given = {k: v for k, v in extra.items() if k in TREE_OPTIONS}
    params = {k: v for k, v in extra.items() if k not in TREE_OPTIONS}
    if "label" in params:
        params["labels"] = params.pop("label")
    allowed = set(JOB_PARAMS.get(kind, ())) | ({"labels"} if kind == "compare_genomes" else set())
    if kind == "build_tree":
        bad = sorted(params)
        if files:
            raise typer.BadParameter(
                "build-tree takes filters, not input files", param_hint="INPUTS"
            )
    else:
        bad = sorted(set(params) - allowed) + sorted(tree_given)
    if bad:
        names = ", ".join("--" + b.rstrip("_").replace("_", "-") for b in bad)
        raise typer.BadParameter(f"{job_type} does not take {names}", param_hint="options")
    if (output or fmt) and not wait:
        raise typer.BadParameter("-o and --format need --wait", param_hint="--wait")

    client = run.client
    key = idempotency_key
    if kind == "build_tree":
        job = client.jobs.submit("build_tree", None, idempotency_key=key, **tree_given)
    elif kind == "compare_genomes":
        if len(files) < 2:
            raise typer.BadParameter(
                "compare-genomes needs at least two genomes", param_hint="INPUTS"
            )
        job = client.jobs.submit(
            kind, [_read_input(f) for f in files], idempotency_key=key, **params
        )
    else:
        if len(files) != 1:
            raise typer.BadParameter(f"{job_type} takes exactly one input", param_hint="INPUTS")
        job = client.jobs.submit(kind, _read_input(files[0]), idempotency_key=key, **params)

    release = f" against release {job.release}" if job.release else ""
    if job.replayed:
        run.say(f"Idempotency key {key} already submitted job {job.id}: not submitted again.")
    if not wait:
        run.say(
            f"Submitted {job.job_type} job {job.id}{release}. "
            f"Check it with: plasticdb jobs status {job.id}"
        )
        return Text(job.id)
    run.say(f"Submitted {job.job_type} job {job.id}{release}; waiting...")
    _wait(run, job, timeout)
    if output and not fmt:
        path = job.download(output)
        run.say(f"Wrote {path}")
        return None
    return _results_text(job, fmt, output)


def _wait(run: State, job: Job, timeout: float | None, poll_interval: float = 2.0) -> None:
    job.wait(timeout=timeout, poll_interval=poll_interval)
    seconds = job.info.runtime_seconds
    took = f" in {seconds:.0f} s" if seconds else ""
    run.say(f"Job {job.id} {job.status}{took} ({job.info.result_count} results)")


def _job(run: State, ref: str) -> Job:
    """A job by id, or by a share link (``…/jobs/shared/<token>``)."""
    if "/" in ref:
        return run.client.jobs.shared(ref)
    return run.client.jobs.get(ref)


JobRef = Annotated[str, typer.Argument(metavar="JOB", help="Job id, or a share link.")]


@command(jobs, "status", formats=RECORD, default_format="json", single=True)
def jobs_status(run: State, job: JobRef) -> Any:
    """A job's status, parameters and provenance."""
    return _job(run, job).info


@command(jobs, "wait", formats=RECORD, default_format="json", single=True)
def jobs_wait(
    run: State,
    job: JobRef,
    timeout: Annotated[float | None, typer.Option(help="Give up after this many seconds.")] = None,
    poll_interval: Annotated[float, typer.Option(help="First poll interval, seconds.")] = 2.0,
) -> Any:
    """Wait for a job to finish (exit 7 if it fails, 8 on --timeout); print its record."""
    found = _job(run, job)
    _wait(run, found, timeout, poll_interval)
    return found.info


@command(jobs, "results", choices={"fmt": _RESULT_FORMATS})
def jobs_results(
    run: State,
    job: JobRef,
    fmt: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="tsv (default), csv, json, jsonl, or newick (build-tree; its default).",
            show_default=False,
        ),
    ] = None,
    comparison: Annotated[
        bool, typer.Option(help="compare-genomes: the genome x plastic matrix instead of hits.")
    ] = False,
    output: OptionalOutput = None,
) -> Any:
    """A finished job's results, typed (every stored hit; see `jobs download` for the file)."""
    return _results_text(_job(run, job), fmt, output, comparison)


@command(jobs, "download")
def jobs_download(
    run: State,
    job: JobRef,
    output: Dest = ".",
    scov: Annotated[float | None, typer.Option(help="Minimum subject coverage (0 = off).")] = None,
    qcov: Annotated[float | None, typer.Option(help="Minimum query coverage.")] = None,
    bitscore: Annotated[float | None, typer.Option(help="Minimum bitscore.")] = None,
    all_hits: Annotated[bool, typer.Option(help="Every hit, not the default view.")] = False,
) -> Any:
    """The server's results file (TSV with provenance lines; a ZIP for compare-genomes)."""
    path = _job(run, job).download(
        output, scov=scov, qcov=qcov, bitscore=bitscore, all_hits=all_hits
    )
    run.say(f"Wrote {path}")
    return None


@command(jobs, "share", choices={"fmt": ("text", "json")})
def jobs_share(
    run: State,
    job: JobRef,
    expires_in_days: Annotated[
        int | None, typer.Option(help="1-365; default: as long as the job.")
    ] = None,
    fmt: Annotated[
        str | None, typer.Option("--format", help="text (the URL, default) or json.")
    ] = None,
    output: OptionalOutput = None,
) -> Any:
    """Create a read-only link to a job's results. The link is shown once: keep it."""
    created = _job(run, job).share(expires_in_days=expires_in_days)
    if fmt == "json":
        return Text(render(created, "json", single=True))
    return Text(str(created.url))


@command(jobs, "shares", formats=TABLE, default_format="tsv")
def jobs_shares(run: State, job: JobRef) -> Any:
    """A job's share links (never their tokens)."""
    return _job(run, job).shares()


@command(jobs, "unshare")
def jobs_unshare(run: State, job: JobRef, share_id: Annotated[int, typer.Argument()]) -> Any:
    """Revoke a share link."""
    _job(run, job).unshare(share_id)
    run.say(f"Revoked share {share_id}")
    return None


@command(jobs, "list", formats=TABLE, default_format="tsv")
def jobs_list(run: State, limit: Limit = None) -> Any:
    """Your jobs, newest first."""
    return run.client.jobs.list(limit=limit)


@command(
    jobs,
    "search-sequence",
    formats=TABLE,
    default_format="tsv",
    extra=sdk_options(Jobs.search_sequence, skip={"sequence"}),
)
def jobs_search_sequence(
    run: State,
    sequence: Annotated[
        str, typer.Argument(help="Residues, a one-record FASTA file, or - for stdin.")
    ],
    extra: dict[str, Any],
) -> Any:
    """Search one sequence now (synchronous DIAMOND; no job). --format json adds provenance."""
    if sequence == "-":
        text = typer.get_text_stream("stdin").read()
    elif os.path.isfile(sequence):
        text = Path(sequence).read_text(encoding="utf-8")
    else:
        text = sequence
    result = run.client.jobs.search_sequence(text, **extra)
    return Table(
        rows=lambda: [h.to_row() for h in result.hits],
        records=lambda: [h.model_dump(mode="json") for h in result.hits],
        document=lambda: result,
    )


# ── auth ────────────────────────────────────────────────────────────────────

auth = _group("Store, check or remove your API key.")
app.add_typer(auth, name="auth")


@command(auth, "login")
def auth_login(
    run: State,
    key: Annotated[
        str | None,
        typer.Option(
            help="The key (visible in your shell history and process list: prefer stdin)."
        ),
    ] = None,
    verify: Annotated[bool, typer.Option(help="Check the key with the server first.")] = True,
) -> Any:
    """Save an API key to the config file (0600). Reads it from --key, else stdin.

    Create a key on the website under Profile -> API keys, then e.g.
    `pbpaste | plasticdb auth login`, or `plasticdb auth login --interactive`
    to be prompted. PLASTICDB_API_KEY, when set, wins over the stored key.
    """
    if key is None:
        stdin = typer.get_text_stream("stdin")
        if stdin.isatty():
            if not run.interactive:
                raise typer.BadParameter(
                    "no key given: pipe it on stdin, pass --key, or add --interactive "
                    "to be prompted",
                    param_hint="--key",
                )
            key = str(typer.prompt("API key", hide_input=True, err=True))
        else:
            key = stdin.readline()
    key = key.strip()
    if not key:
        raise typer.BadParameter("the key is empty", param_hint="--key")
    if verify:
        from .._client import Client

        with Client(api_key=key, base_url=run.base_url) as client:
            client.jobs.list(limit=1).first()
    path = _config.store_key(key)
    run.say(f"Saved API key {_config.mask(key)} to {path}")
    if os.environ.get("PLASTICDB_API_KEY", "").strip():
        run.warn("PLASTICDB_API_KEY is set: it wins over the stored key")
    return None


@command(auth, "logout")
def auth_logout(run: State) -> Any:
    """Remove the stored API key."""
    if _config.forget_key():
        run.say(f"Removed the API key from {_config.config_path()}")
    else:
        run.say("No API key was stored")
    if os.environ.get("PLASTICDB_API_KEY", "").strip():
        run.warn("PLASTICDB_API_KEY is still set in the environment")
    return None


@command(auth, "status", formats=("text", "json"), default_format="text", single=True)
def auth_status(
    run: State,
    verify: Annotated[bool, typer.Option(help="Also check the key with the server.")] = False,
) -> Any:
    """Which API key is in use and where it comes from (exit 4 when there is none)."""
    key, source = run.key()
    path = _config.config_path()
    info: dict[str, Any] = {
        "api_key": _config.mask(key) if key else None,
        "source": source,
        "config_file": str(path),
        "config_file_exists": path.exists(),
        "base_url": run.client.base_url,
    }
    if key is None:
        raise errors.AuthenticationError(f"No API key configured (config file: {path})")
    if verify:
        run.client.jobs.list(limit=1).first()
        info["verified"] = True
    return _Status(info)


class _Status(dict[str, Any]):
    """``auth status``'s answer: ``key: value`` lines as text, or JSON."""


# ── citation, provenance, version ───────────────────────────────────────────


@command(app, "cite", choices={"fmt": ("text", "bibtex", "json")})
def cite(
    run: State,
    fmt: Annotated[
        str | None, typer.Option("--format", help="text (default), bibtex or json.")
    ] = None,
    output: OptionalOutput = None,
) -> Any:
    """How to cite the data: the dataset release (--release, else the current one) and paper."""
    citation = run.client.cite()
    chosen = fmt or format_for(output, ("bibtex", "json"), "text")
    if chosen == "bibtex":
        return Text(_bibtex(citation))
    if chosen == "json":
        return Text(render(citation, "json", single=True))
    return Text(str(citation))


def _bibtex(citation: Any) -> str:
    release = citation.release
    year = release.split(".", 1)[0] if release and release[:4].isdigit() else None
    entries = []
    if release:
        lines = [
            f"@misc{{plasticdb_{release.replace('.', '_')},",
            "  author = {{PlasticDB}},",
            f"  title = {{PlasticDB dataset release {release}}},",
            "  publisher = {University of Auckland},",
        ]
        if year:
            lines.append(f"  year = {{{year}}},")
        if citation.doi:
            lines.append(f"  doi = {{{citation.doi}}},")
        lines.append(f"  note = {{Licensed {citation.license}}},")
        lines.append("}")
        entries.append("\n".join(lines))
    entries.append(
        "\n".join(
            [
                "@article{gambarini2022plasticdb,",
                "  author = {Gambarini, Victor and Pantos, Olga and Kingsbury, Joanne M. and "
                "Weaver, Louise and Handley, Kim M. and Lear, Gavin},",
                "  title = {{PlasticDB}: a database of microorganisms and proteins linked to "
                "plastic biodegradation},",
                "  journal = {Database},",
                "  volume = {2022},",
                "  pages = {baac008},",
                "  year = {2022},",
                f"  doi = {{{citation.paper_doi}}},",
                "}",
            ]
        )
    )
    return "\n\n".join(entries) + "\n"


@command(app, "provenance", formats=RECORD, default_format="json", single=True)
def provenance(run: State) -> Any:
    """The release, its source, and the server's and this tool's versions (record with results)."""
    return run.client.provenance()


@command(app, "version", formats=("text", "json"), default_format="text", single=True)
def version(
    run: State,
    server: Annotated[bool, typer.Option(help="Also ask the server for its version.")] = False,
) -> Any:
    """This tool's version (and with --server, the server's)."""
    info: dict[str, Any] = {"plasticdb": __version__, "python": sys.version.split()[0]}
    if server:
        info["server"] = run.client.server_version()
        info["base_url"] = run.client.base_url
    return _Status(info)
