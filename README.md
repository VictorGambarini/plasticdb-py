# plasticdb

Python client for the [PlasticDB](https://v2.plasticdb.org) API: microorganisms
and proteins linked to plastic biodegradation, and the evidence behind them.

> **Pre-alpha.** This release covers the client core, dataset releases and every
> public data endpoint. Jobs (sequence annotation, HMM screens, trees) and the
> command-line tool come in later versions.

## Install

```bash
pip install plasticdb
# optional extras
pip install "plasticdb[pandas]"   # or polars, bio, cli, all
```

Python 3.10 or newer. The core install needs only `httpx` and `pydantic`.

## Quick start

```python
import plasticdb

client = plasticdb.Client()

# Proteins with positive evidence on PET, as a table and as sequences
pet = client.proteins.list(plastic="PET", kind="wild_type")
print(pet.total)  # 468
df = pet.to_pandas()  # one flat row per protein
pet.to_fasta("pet_enzymes.fasta")  # >PB8CAZ E4 ...
records = pet.to_biopython()  # Bio.SeqRecord list (plasticdb[bio])

# One record, with its evidence
petase = client.proteins.get("PBFCTW")
print(petase.name, len(petase.sequence or ""), petase.source_microorganism.name)

# The evidence spine: interaction -> experiment -> observation
evidence = client.evidence.interactions("PBFCTW")
measurements = evidence.to_pandas(level="observation")  # one row per measured value

# Engineered variants of PETase, with sequences
client.proteins.variants("PBFCTW").to_fasta("petase_variants.fasta")

# Organisms, plastics, papers
client.microorganisms.list(genus="Pseudomonas", plastic="PET").to_polars()
client.microorganisms.get(1547922)  # NCBI tax-id or PB… accession
client.plastics.get(16, protein_limit=10)  # PET, with one page of proteins
client.papers.list(protein="PBFCTW", min_year=2020).all()

# Search, pathways, statistics, the change feed
client.search("cutinase").first()
client.pathways.graph(16).to_networkx()
client.stats.summary()
feed = client.changes(since="2026-11-01T00:00:00Z")
for change in feed:  # status: updated, merged or removed
    ...
next_since = feed.as_of

# Files
client.downloads.proteins("data/", format="csv", plastic="PET")
client.downloads.dataset("data/")  # every public record, ZIP of CSVs
```

Every list is a lazy `Collection`: nothing is fetched until you iterate it, and
`len()` is the server's total. `all()`, `rows()`, `to_pandas()`, `to_polars()`
and `to_fasta()` fetch the remaining pages at 500 records each, up to four at
a time (`Client(max_concurrency=)`; it drops to one when the rate-limit
allowance runs low, and a 429 waits for `Retry-After`).

### Filters

Keyword arguments are the API's own query parameters, with the same meaning on
every resource (the server's
[conventions](https://v2.plasticdb.org/api/v1/docs)): `plastic`,
`include_subtypes`, `microorganism`, `polarity`, `include_negative`,
`evidence_method`, `min_year`/`max_year`, `updated_since`, `sort_by` /
`sort_order` and so on. The rank `class` is spelled `class_`. An unknown name
is a `TypeError` naming the valid ones; an unknown value is a server 422
(`ValidationError`). Entity lists default to records with a **positive** result,
as on the website: pass `polarity="negative"` or `include_negative=True` for the
rest.

### Tables

`to_pandas()` / `to_polars()` give one flat row per record: nested objects
become `parent.child` columns (`source_microorganism.name`, `paper.doi`), lists
of plain values stay lists, and per-plastic evidence becomes `plastics_positive`
/ `plastics_negative` / `plastics_inconclusive`. `records()` keeps the full JSON
nesting. Interactions take `level="interaction" | "experiment" | "observation"`.

### Sequences

`.to_fasta()` and `.to_biopython()` work on every collection that carries
sequences: protein and variant lists, a protein's variants, constructs. Protein
list items carry no sequence, so the SDK downloads `proteins.fasta` once with the
same filters and keeps the listed records, in list order. FASTA ids are the
accessions (`PlasticDB_<id>` for the few records without one), as in the
server's own files. Single records have `.to_fasta()` too.

### Merged and withdrawn records

Records are sometimes merged (two accessions for one molecule). `get()` on a
merged accession raises `plasticdb.MergedError` with `.merged_into`, the
survivor. Pass `follow_merges=True` to get the survivor instead, with a
`plasticdb.MergedRecordWarning`. A withdrawn record raises `GoneError`
(`MergedError` is a subclass, so `except GoneError` catches both). An engineered
variant's accession given to `proteins.get()` returns the `Variant`; the
per-protein collections (`interactions`, `kinetics`, …) raise
`EngineeredVariantError` for it. A past release never contains merged records,
so a pinned `get()` answers `NotFoundError` for them. `client.changes()` lists
merges with their survivors.

### Configuration

| Argument | Environment variable | Default |
|---|---|---|
| `api_key` | `PLASTICDB_API_KEY` | none (reads are anonymous; jobs need a key) |
| `release` | `PLASTICDB_RELEASE` | none (live database) |
| `base_url` | `PLASTICDB_BASE_URL` | `https://v2.plasticdb.org/api/v1` |
| `cache_dir` | | none |
| `timeout`, `max_retries`, `max_retry_wait` | | 30 s, 3, 60 s |
| `max_concurrency` | | 4 (parallel page requests in a bulk pull) |

Rate-limited requests (429) are retried after `Retry-After`, and idempotent
requests are retried with backoff on 5xx and connection errors.

### Errors

Every exception subclasses `plasticdb.PlasticDBError`. Examples are
`NotFoundError`, `GoneError`, `MergedError` (with `.merged_into`),
`EngineeredVariantError`, `AuthenticationError`, `PermissionDenied`,
`RateLimitError` and `ServiceUnavailableError` (with `.retry_after`),
`ValidationError`, `ServerError`, `ChecksumError`, and the release errors
`UnknownReleaseError`, `LiveOnlyError`, `UnsupportedFilterError` and
`ReleaseNotMountedError` (see `plasticdb.errors`).

## Citation and licence

The code is MIT-licensed. **The PlasticDB data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**: cite the dataset
release you used, plus the PlasticDB paper:

> Gambarini V, Pantos O, Kingsbury JM, Weaver L, Handley KM, Lear G (2022).
> PlasticDB: a database of microorganisms and proteins linked to plastic
> biodegradation. *Database*, 2022:baac008. https://doi.org/10.1093/database/baac008

`client.cite()` returns both, including the release version and DOI.

## Development

```bash
uv venv .venv && uv pip install -e . --group dev
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
make contract-refresh   # re-vendor the SDK contract from the live server
```
