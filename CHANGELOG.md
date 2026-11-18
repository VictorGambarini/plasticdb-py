# Changelog

## Unreleased

- Data namespaces on `Client`: `proteins`, `variants`, `microorganisms`,
  `plastics`, `papers`, `evidence` (interactions, kinetics, properties, binding
  assays, constructs, preparations), `search`, `pathways`, `stats`,
  `downloads`, and `changes(since=)`. Filters are the API's own parameter
  names; an unknown one is a `TypeError`.
- Typed models for every record and list item (unknown fields kept).
- Collections: `rows()`, and `to_pandas()` / `to_polars()` with nested objects
  flattened to `parent.child` columns; interactions at `interaction`,
  `experiment` or `observation` level; `to_fasta()` / `to_biopython()` on
  sequence-carrying collections (protein lists via the server's FASTA download).
- Bulk pulls (`all()` and the conversions) fetch 500-record pages, up to
  `max_concurrency` (default 4) at a time, and go sequential when the
  rate-limit allowance is low.
- Merged records: `get()` raises `MergedError` by default; `follow_merges=True`
  returns the survivor with a `MergedRecordWarning`. `proteins.get()` on a
  variant accession returns the `Variant`; sub-collections raise
  `EngineeredVariantError`.
- Streaming downloads to a file or directory, following redirects, with
  SHA-256 verification of release artefacts (`ChecksumError`). Pinned
  downloads and release artefacts are cached in `cache_dir`.
  `ServiceUnavailableError.retry_after`; `downloads.dataset(max_wait=)`.
- Live-only endpoints are marked in the registry and checked against the
  contract's 409s; the contract test also fails on an operation the SDK
  neither wraps nor lists as deliberately unwrapped.

## 0.0.1 (unreleased)

- First release: `Client` with API-key auth, release pinning (`?release=`),
  retries honouring `Retry-After`, a typed exception hierarchy, provenance,
  citation, an on-disk cache for pinned answers, and `client.releases`
  (`current`, `get`, `list`, `status`).
