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
p = client.proteins.get("PBFCTW", view="summary")  # summary omits the per-record evidence lists
client.proteins.interactions("PBFCTW", kind="direct")  # direct | indirect | all
client.proteins.kinetics("PBFCTW")  # kcat, Km ... of the protein and its variants
client.proteins.properties("PBFCTW")  # optimal pH/temperature, Tm, half-life
client.proteins.variants("PBFCTW").to_fasta("variants.fasta")  # engineered variants, with sequences
client.proteins.fasta("PBFCTW")  # annotated FASTA header (live only)
client.proteins.structure("PBFCTW", "structures/")  # PDB (live only; pinned: downloads.structure)
client.proteins.lineage("PBFCTW")  # parent plus every engineered variant (live only)
client.proteins.engineered_parents()  # wild types that have variants (live only)
```

`get()` takes `follow_merges=True` to return a merge survivor instead of raising
[`MergedError`](errors.md), and `include_sequenceless=True` for records without
a sequence. An engineered variant's accession returns a `Variant`.

## `client.variants`

```python
client.variants.list(plastic="PET")  # the protein filters; kind is fixed to engineered
client.variants.get("PBXXXX")  # (live only)
client.variants.of("PBFCTW")  # same as proteins.variants()
client.variants.fasta("PBXXXX")  # (live only)
```

## `client.microorganisms`

```python
client.microorganisms.list(genus="Pseudomonas", plastic="PET").to_polars()
client.microorganisms.get(1547922)  # NCBI tax-id, or a PB... accession
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

The evidence spine is interaction, then experiment, then observation.

```python
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
