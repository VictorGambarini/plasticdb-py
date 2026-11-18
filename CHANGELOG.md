# Changelog

## Unreleased

- Server 2.86.0 and 2.87.0 (re-vendored SDK contract, fixtures re-captured live):
  - Job submissions, tree builds and share links send an `Idempotency-Key` (a
    new UUID per call, or `idempotency_key=` / `--idempotency-key`), the same on
    every retry, so they are now retried like reads on a timeout, a dropped
    connection or a 5xx. A replay is `job.replayed`; when retries run out the
    error names the key to resubmit with. `IdempotencyKeyReusedError` (422) for
    a key reused with a different request. Without an API key (which the server
    needs to honour the key) a `POST` is still never resent after it may have
    reached the server.
  - Polling and `jobs.get()` / `jobs.shared()` ask for `?include=none`;
    `Job.hits()` / `results()` page `GET /jobs/{id}/results` (and the share
    link's `/results`), `Job.iter_hits()` lazily. A tree job reads its Newick
    from `GET /jobs/{id}`; `/tree/{id}` is no longer called.
  - `Job.provenance` and `Job.release` fall back to the job read's
    `X-PlasticDB-Release` / `-Source` headers.
  - `pathway_analysis(target_plastic=)` (CLI `--target-plastic`);
    `annotate_list` documents the server's 7/8/`tab` defaults.
  - Record redirects are told apart by `reason` (`merged` or
    `engineered_variant`), not by comparing accessions; `MergedError.kind` can
    be `microorganism`, so `microorganisms.get(follow_merges=True)` follows
    organism merges. A merged sub-resource raises `MergedError` with the
    survivor's sub-resource `location`.
  - `EngineeredVariantError` is now a `NotFoundError` and carries
    `merged_into`; it is raised for the server's 404 `engineered_variant` on a
    variant's per-protein sub-resources. `proteins.fasta()` / `structure()` of a
    variant accession return the variant's.
  - A withdrawn record (also a variant whose parent was withdrawn, formerly a
    404) is a `GoneError` with `.accession` and `.reason == "withdrawn"`.
    Every error now carries the server's `.reason`.
  - `ResultsExpiredError` (a `GoneError`, `.results_expired_at`) for a job's
    deleted result files, before or from the server's 410 `expired`.
  - `variants.get()` and `variants.fasta()` are pinnable (answered from the
    release snapshot); `variants.structure()` stays live only.
  - `evidence_scope="enzyme_only"` on `microorganisms.get()` and
    `stats.summary()` (CLI `stats --evidence-scope`); `proteins.get()` takes a
    numeric id.
  - `search_sequence()`: a 503 (`index_unavailable` / `binary_unavailable`) is
    `ServiceUnavailableError` with `.retry_after`; a 504 `timeout` is a
    `ServerError` that is not retried (CLI exit 8); it is retried on other 5xx.
  - Downloads: a cached pinned table is revalidated with its `ETag`
    (`If-None-Match`; 304 uses the copy, offline too); an artefact is also
    checked against the server's `X-PlasticDB-SHA256`. `APIResponse.etag`,
    `.sha256`, `.source_sha256` and `.replayed`.
  - `dataset.zip`'s JSON 503 (`archive_unavailable`) keeps `.retry_after`.
- Every public error is exported from `plasticdb` (`UnknownReleaseError`,
  `ReleaseNotMountedError`, `ReleaseNoArtefactsError`, `BadRequestError`,
  `ConflictError`, `UnexpectedResponseError` were only in `plasticdb.errors`),
  and the README and errors page list them all.

- Documentation site (MkDocs Material, mkdocstrings API reference) published to
  GitHub Pages: quick start, reproducibility with pinned releases, data and jobs
  guides, a CLI reference generated from the Typer app, errors, Snakemake and
  Nextflow workflows and an example notebook. `make docs` builds it;
  `requirements-docs.txt` lists its dependencies.
- The `plasticdb` command-line tool (`pip install 'plasticdb[cli]'`; also
  `python -m plasticdb`). It is a thin layer over the SDK, with commands for
  proteins, variants, microorganisms, plastics, papers, evidence, search,
  pathways, stats, changes, releases, downloads, jobs, auth, cite, provenance
  and version.
  - Data goes to stdout as `--format tsv|csv|json|jsonl|fasta`, or to
    `-o FILE`, where the extension picks the format. Messages go to stderr.
  - There are no prompts unless `--interactive` is given.
  - `--release`, `--api-key`, `--base-url`, `--cache-dir`, `-q` and `-v` are
    accepted before or after the subcommand, with matching `PLASTICDB_*`
    environment variables.
  - Filter and keyword options are generated from the SDK's own filter types
    and method signatures.
  - `auth login|logout|status` keeps the key in a 0600 config file. The
    environment variable wins over the file.
  - `download dataset --release V -o DIR` fetches and verifies every artefact
    the release published.
  - `jobs submit TYPE INPUT --wait -o FILE` takes kebab-case job types.
  - `cite --format bibtex`.
  - Exit codes are documented and stable: 0 ok, 1 error, 2 usage, 3 not found,
    4 auth, 5 rate limited, 6 release, 7 job failed, 8 timeout,
    9 unavailable, 130 interrupted.
- `releases.get()` on an unknown version raises `UnknownReleaseError`, which is
  still a `NotFoundError`.
- Downloaded files get the usual permissions (0666 less the umask) instead of
  `mkstemp`'s 0600.

- Jobs (`client.jobs`): `submit(job_type, input, **params)` and a typed helper
  per server job type: `annotate_gene`, `annotate_genome`, `compare_genomes`,
  `annotate_list`, `pathway_analysis`, `hmm_screen`, `build_tree`, plus the
  synchronous `search_sequence`. Jobs need an API key (`AuthenticationError`
  before any request otherwise); a pinned client, or `release=`, runs them
  against that release's archived databases.
- `Job`: `refresh()`, `wait(timeout=, poll_interval=)` with backoff and
  `Retry-After`, `status`, `results()` (`hits()`, `steps()`, `taxa()`,
  `tree()`, typed, with `to_pandas()` / `to_polars()`), `comparison()`,
  streaming `download(path)` (TSV or the compare_genomes ZIP),
  `share()` / `shares()` / `unshare()`, and `provenance` (release,
  reference SHA-256, tool versions). `jobs.get(id)`, `jobs.list()`,
  `jobs.shared(link)`.
- Inputs: a path (plain or gzip), FASTA text, bytes, a file object,
  `Bio.SeqRecord`s, or table rows; uploads are named after their contents.
- Submissions are never resent once they may have reached the server; a 429 is
  waited out within `max_retry_wait`.
- New errors: `PayloadTooLargeError` (413), `JobError`, `JobFailedError`,
  `JobTimeoutError`. `plasticdb.ResultTable` for in-memory typed results.
- The contract test covers every job, share, tree and sequence-search operation.

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
