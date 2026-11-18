# Reproducibility with pinned releases

PlasticDB publishes numbered **dataset releases** (e.g. `2026.50`), each with a
DOI, a frozen copy of the data and the reference databases the analyses searched.
The live database moves on; a release does not.

## Pin one

=== "Python"

    ```python
    import plasticdb

    client = plasticdb.Client(release="2026.50")  # or PLASTICDB_RELEASE=2026.50
    proteins = client.proteins.list(plastic="PET")  # answered from that release
    ```

=== "Command line"

    ```bash
    plasticdb proteins list --plastic PET --release 2026.50 -o pet.tsv
    # or: export PLASTICDB_RELEASE=2026.50
    ```

`release=` is sent as `?release=` on every read. The server answers from the
release's snapshot and says so in the `X-PlasticDB-Release` and
`X-PlasticDB-Release-Source` headers. An unknown version raises
`UnknownReleaseError` (a `NotFoundError`). List what exists with
`client.releases.list()` or `plasticdb releases list`.

A past release holds only published records: merged and withdrawn ones are not
in it, so a pinned `get()` on such an accession is a `NotFoundError`.

## Check what answered

```python
client.proteins.list(plastic="PET").total
resp = client.last_response  # the most recent answer
resp.release, resp.release_source, resp.pinned  # "2026.50", "snapshot", True
resp.unavailable_fields  # fields this release does not carry, if any
```

`release_source` is `snapshot` for a past release and `live` for the live
database (which also serves the current release).

## Record it with your results

```python
prov = client.provenance()
prov.to_dict()
# release, release_source, pinned, base_url, sdk_version, server_version, data_license
```

`plasticdb provenance` prints the same as JSON. Jobs carry their own
[provenance](jobs.md#provenance): the release, the SHA-256 of the reference
database searched and the tool versions.

## Cite it

```python
print(client.cite())  # a pinned client cites its release; an unpinned one the current release
```

`cite()` returns a `Citation` (`dataset`, `release`, `doi`, `paper`, `paper_doi`,
`license`, `license_url`); `str()` of it is the text to paste. If the server
cannot be reached the citation still names the paper. On the command line:
`plasticdb cite --release 2026.50 --format text|bibtex|json`. The data is
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Cache

```python
client = plasticdb.Client(release="2026.50", cache_dir="~/.cache/plasticdb")
```

With `cache_dir`, answers from a past release's snapshot are kept on disk
indefinitely (a release is immutable) and reused without a request. Live
answers are never cached, and neither are answers of an unpinned client.
Release artefacts (below) are verified and always cacheable. On the command line
use `--cache-dir` or `PLASTICDB_CACHE_DIR`.

## Download the release itself

```python
client.downloads.artefacts()  # files the release published
client.downloads.release_file("parquet/proteins.parquet", "data/")  # verified against SHA-256
client.downloads.manifest()  # MANIFEST.json: every file, hash, size, licence
client.downloads.structure(1234, "structures/")  # one structure of the release (gzipped PDB)
```

A checksum mismatch raises `ChecksumError` and keeps nothing. On the command
line, `plasticdb download dataset --release 2026.50 -o data/` fetches and
verifies every artefact.

## What cannot be pinned

Some endpoints are computed from the live database. A pinned client gets a
`LiveOnlyError` (HTTP 409) from them rather than a silently live answer:

- **Search**: `client.search(...)`, `.grouped()`, `.autocomplete()`
- **Statistics**: `client.stats.*`
- **The change feed**: `client.changes()`
- **Structures and FASTA of single records**: `proteins.fasta/structure`,
  `variants.get/fasta/structure`. For a pinned structure use
  `downloads.structure()`; for pinned sequences, a protein list's `to_fasta()` (below).
- **Lineages**: `proteins.lineage()`, `proteins.engineered_parents()`
- **Name lists and taxonomy**: `microorganisms.names/lineage_levels/taxonomy`, `plastics.names`
- **Paper footprints and citation exports**: `papers.footprint()`, `papers.export()`
- **Live bulk files**: `downloads.dataset()` (ZIP/JSON) and `downloads.search()`; for a
  release use `downloads.release_file()`

Use a second, unpinned `plasticdb.Client()` for these, and say so in your methods.

A pinned protein list's `to_fasta()` does work: it downloads that release's
whole `proteins.fasta` (kept in `cache_dir`) and keeps the listed records.

Filters and options a release cannot answer (for example any filter on a past
release's table downloads) raise `UnsupportedFilterError` (HTTP 422); it is also
a `ValidationError`.

## Jobs on a release

A pinned client, or `release=` on the call, runs analyses against that
release's archived DIAMOND databases and HMM library. See [Jobs](jobs.md).
