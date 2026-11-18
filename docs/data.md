# Data guide

Everything hangs off one [`Client`](api/client.md). Namespaces below are
attributes of it; signatures are in the [API reference](api/resources.md).

```python
import plasticdb

client = plasticdb.Client()  # or Client(release="2026.50"), see Reproducibility
```

!!! info "Live only"
    Marked **(live only)**: a [pinned](reproducibility.md#what-cannot-be-pinned)
    client raises `LiveOnlyError`.

## Tables, FASTA and conversions

Every list is a lazy [`Collection`](quickstart.md#lists-are-lazy):

- `to_pandas()` / `to_polars()`: one flat row per record. Nested objects become
  `parent.child` columns (`source_microorganism.name`), lists of plain values
  stay lists, per-plastic evidence becomes `plastics_positive` /
  `plastics_negative` / `plastics_inconclusive`.
- `rows()` is the same as plain dicts; `records()` keeps the full JSON nesting.
- `to_fasta(path=None)` and `to_biopython()` on collections that carry
  sequences (protein and variant lists, constructs). FASTA ids are accessions.
- `Collection.release` and `.response` say which release answered.

## `client.releases`

```python
client.releases.current()  # version, DOI, licence, citation of the release being served
client.releases.get("2026.50")  # one release with its artefacts; UnknownReleaseError if absent
client.releases.list()  # every completed release, newest first
client.releases.status()  # is a release being cut right now?
```

## `client.proteins`

```python
pet = client.proteins.list(plastic="PET", kind="wild_type")  # kind: wild_type | engineered | both
p = client.proteins.get("PBFCTW")  # view="summary" (default): counts, no evidence lists
full = client.proteins.get(
    "PBFCTW", view="full"
)  # embeds interactions, variants, kinetics, properties
client.proteins.interactions("PBFCTW", evidence_level="direct")  # direct | indirect | all
client.proteins.kinetics("PBFCTW")  # kcat, Km ... of the protein and its variants
client.proteins.properties("PBFCTW")  # optimal pH/temperature, Tm, half-life
client.proteins.variants("PBFCTW").to_fasta("variants.fasta")  # engineered variants, with sequences
client.proteins.fasta("PBFCTW")  # annotated FASTA header (live only)
client.proteins.structure("PBFCTW", "structures/")  # PDB (live only; pinned: downloads.structure)
client.proteins.lineage("PBFCTW")  # parent plus every engineered variant (live only)
client.proteins.engineered_parents()  # wild types that have variants (live only)
```

`get()` returns the **summary** view unless you ask for `view="full"` (0.1.0; the
server's own default since 2.89.0). The summary is the record without its
evidence lists: `interactions`, `indirect_interactions`, `variants`, `kinetics`
and `properties` are empty and their sizes are in `p.counts`
(`p.counts["variants"]`); page them with `proteins.interactions()`,
`.variants()`, `.kinetics()` and `.properties()`. `view="full"` embeds all of
them in one response, which for a well-studied enzyme can be up to 12.5 MB.
`p.view` says which you got.

`proteins.interactions(accession, evidence_level=...)` takes `all`, `direct` or
`indirect`; the old `kind=` still works but emits a `DeprecationWarning`.

`proteins.list(include_sequence=True)` adds each item's `sequence`;
`to_fasta()` and `to_biopython()` use it themselves (one request series, also on
a pinned client) instead of downloading the FASTA separately.

`get()` takes an accession or the numeric id, `follow_merges=True` to return a
merge survivor instead of raising [`MergedError`](errors.md), and
`include_sequenceless=True` for records without a sequence. An engineered
variant's accession returns a `Variant` (and `fasta()` / `structure()` the
variant's); the per-protein collections raise `EngineeredVariantError` for one.
A withdrawn record raises `GoneError` (`.reason == "withdrawn"`).

## `client.variants`

```python
client.variants.list(plastic="PET")  # the protein filters; kind is fixed to engineered
client.variants.get("PBXXXX")  # accession or numeric id
client.variants.of("PBFCTW")  # same as proteins.variants()
client.variants.fasta("PBXXXX")
client.variants.structure("PBXXXX")  # (live only)
```

A pinned client reads a variant (and its FASTA) from the release's snapshot: a
variant the release did not contain is a `NotFoundError` (no merge redirect
there), `include_sequenceless=True` is an `UnsupportedFilterError`, and the
fields the snapshot cannot fill are served empty and named in
`client.last_response.unavailable_fields`.

## `client.microorganisms`

```python
client.microorganisms.list(genus="Pseudomonas", plastic="PET").to_polars()
client.microorganisms.get(1547922)  # NCBI tax-id, or a PB... accession
client.microorganisms.get(1547922, evidence_scope="enzyme_only")  # only its enzymes' evidence
client.microorganisms.get(1547922, evidence_scope="whole_organism")
client.microorganisms.names()  # every {id, name} (live only)
client.microorganisms.lineage_levels(domain="Bacteria")  # taxon names per rank (live only)
client.microorganisms.taxonomy("genus", "Pseudomonas")  # aggregate stats for a taxon (live only)
```

`evidence_scope` decides whether an organism is credited with a plastic through
its enzymes (`organism_or_enzyme`, the default) or only through assays on the
organism itself (`whole_organism`).

## `client.plastics`

```python
client.plastics.list(has_evidence=True)
client.plastics.get(16, protein_limit=10)  # one page each of organisms and proteins
client.plastics.names()  # (live only)
```

`plastics.get()` embeds only one page of organisms and proteins
(`microorganism_limit`, `protein_limit`, `*_offset`, `*_sort_by`); to get all of
them, page through `microorganisms.list(plastic_id=...)` or
`proteins.list(plastic=...)`.

## `client.papers`

```python
client.papers.list(protein="PBFCTW", min_year=2020).all()
client.papers.get(42)  # with the organisms, proteins and variants it reports
client.papers.footprint(42)  # every record one paper contributed (live only)
client.papers.export(42, format="bibtex")  # BibTeX or RIS text (live only)
client.papers.export(format="ris", path="papers.ris", plastic="PET")  # every matching paper
```

## `client.evidence`

The evidence spine is interaction, then experiment, then observation. It is
available per protein (nested) and, since 0.1.0, across the whole database as
three flat tables that link by `interaction_id` and `experiment_id`:

```python
# flat lists: every public record, ordered by id, pinnable
client.evidence.interactions.list(plastic="PET", evidence_level="direct", protein="PBFCTW")
client.evidence.interactions.get(7)  # InteractionRecord
client.evidence.experiments.list(interaction_id=7)  # ExperimentRecord
client.evidence.observations.list(plastic="PET", has_quantitative=True).to_pandas()
client.evidence.observations.get(21)  # ObservationRecord

# per protein, with experiments and observations nested
ev = client.evidence.interactions("PBFCTW")
ev.to_pandas(level="observation")  # level: interaction | experiment | observation
client.evidence.kinetics("PBFCTW")
client.evidence.properties("PBFCTW")
client.evidence.binding_assays(protein="PBFCTW")  # adsorption assays; also binding_assay(id)
client.evidence.constructs(protein="PBFCTW")  # sequences as expressed; also construct(id)
client.evidence.construct_structures(7)
client.evidence.preparations(protein="PBFCTW")  # host, purification, tags; also preparation(id)
client.evidence.experiment_preparations(123)
```

The flat lists take `plastic` (+ `include_subtypes`), `plastic_id`,
`microorganism`, `protein`, `evidence_level`, `paper_id`, `doi`, `polarity`,
`evidence_method`, `substrate_form`, `measurement_type` and `has_quantitative`;
experiments also take `interaction_id`, observations `interaction_id` and
`experiment_id`. Interaction filters apply to a record's interaction; the
experiment and observation filters ask one of its experiments to match them all.
`evidence_level="direct"` or `"indirect"` needs `protein` or `microorganism`
(the server answers 422 otherwise); `microorganism` matches the evidence that
credits the organism, through its own assays or its enzymes'. The items are the
release tables' columns, as `InteractionRecord`, `ExperimentRecord` and
`ObservationRecord`; the per-protein call keeps its nested `Interaction`.
The `{total, items, offset, limit}` envelope is handled by the usual
[collection](api/collections.md) (500 per page, `page_size`, `limit`).

Constructs and preparations take `protein`, `protein_id`, `protein_variant_id`,
`paper_id` and `doi`; binding assays also `plastic`, `plastic_id` and
`include_subtypes`. The per-protein collections (`interactions`, `kinetics`,
`properties`) raise `EngineeredVariantError` for a variant accession.

## `client.search` (live only)

```python
client.search("cutinase").first()  # ranked hits across every record type
client.search("PETase", limit=50).all()
client.search.grouped("PETase", limit=10)  # top hits per record type (at most 100)
client.search.autocomplete("pseudo")  # name suggestions, as the website's search box
```

## `client.pathways`

```python
client.pathways.list()
client.pathways.get(1)  # ordered reaction steps
client.pathways.for_plastic(16)
client.pathways.graph(16).to_networkx()  # compounds and reactions as a graph
```

## `client.stats` (live only)

```python
client.stats.summary()  # headline counts
client.stats.summary(evidence_scope="enzyme_only")  # organisms credited only via an enzyme
client.stats.protein_activity()  # degrade / do not degrade / untested
client.stats.charts()  # the data behind the website's charts
client.stats.by_country()
```

## `client.changes()` (live only)

```python
feed = client.changes(since="2026-11-01T00:00:00Z", entity_type="protein")
for change in feed:  # status: updated, merged or removed; merges name their survivor
    ...
next_since = feed.as_of  # store it; pass it as the next since=
```

## `client.downloads`

```python
client.downloads.proteins("data/", format="csv", plastic="PET")  # csv | json | fasta
client.downloads.microorganisms("data/")  # also plastics, papers, protein_variants; csv | json
client.downloads.dataset("data/")  # every public record: ZIP of CSVs (live only; max_wait=)
client.downloads.search("hits.json", "cutinase")  # search results as a file (live only)
```

`path` is a file or a directory (the server's file name is used). Release
artefacts (`artefacts`, `release_file`, `manifest`, `structure`) are in
[Reproducibility](reproducibility.md#download-the-release-itself). `dataset()`
is built on demand: while it is, the server answers 503 with `Retry-After`
(`ServiceUnavailableError.retry_after`), and `max_wait=` keeps waiting for it.

## Merged and withdrawn records

`get()` on a merged accession raises `MergedError` with `.merged_into`; pass
`follow_merges=True` for the survivor (with a `MergedRecordWarning`). A withdrawn
record raises `GoneError`. `client.changes()` lists merges with their survivors.
