# Jobs guide

Annotation, genome comparison, pathway analysis, HMM screens and taxonomy trees
run on the server as **jobs**. `client.jobs` submits them, waits, and reads the
typed results.

## API key

Jobs need an API key: create one under *Profile, API keys* on
[v2.plasticdb.org](https://v2.plasticdb.org). Without one, the call raises
`AuthenticationError` before anything is sent.

=== "Python"

    ```python
    client = plasticdb.Client(api_key="...")  # or export PLASTICDB_API_KEY
    ```

=== "Command line"

    ```bash
    pbpaste | plasticdb auth login     # stored in a 0600 config file; or export PLASTICDB_API_KEY
    ```

Reading data needs no key.

## Submit, wait, results, download

```python
client = plasticdb.Client(release="2026.50")  # run against that release's databases

job = client.jobs.annotate_genome("genome.faa.gz", evalue=1e-10)
job.id, job.status  # returned at once; status is "waiting" or "running" at first
job.wait(timeout=3600)  # polls with backoff; JobFailedError / JobTimeoutError
hits = job.results()  # every stored hit, typed (JobHit), paged; .to_pandas() / .to_polars()
job.download("results/")  # the server's TSV, with provenance lines
```

| Job type | Helper | Input | Read with |
|---|---|---|---|
| `annotate_gene`, `annotate_genome` | `annotate_gene()`, `annotate_genome()` | protein FASTA (nucleotide with `blast_type="blastx"`) | `hits()` |
| `compare_genomes` | `compare_genomes()` | 2 to 20 genomes: a list or `{label: genome}` | `hits()`, `comparison()`, ZIP download |
| `pathway_analysis` | `pathway_analysis()` | protein FASTA; `target_plastic` (default `PET`) | `steps()` |
| `hmm_screen` | `hmm_screen()` | protein FASTA | `hits()`, `info.result_metadata["polymer_hits"]` |
| `annotate_list` | `annotate_list()` | a taxa table (path, text or rows); `genus_column` 7, `species_column` 8 and `field_separator` `tab` unless given | `taxa()` |
| `build_tree` | `build_tree(**filters)` | organism filters or `microorganism_ids` | `tree()` (Newick and matrix) |

`client.jobs.submit("annotate_genome", "genome.faa", evalue=1e-10)` is the
generic form; the helpers add typed keyword arguments. Parameter names are the
server's (`evalue`, `pident`, `include_variants`, `min_qcov`, `min_scov`,
`min_bitscore`, `organism_type`, `target_polymers`, `threshold_mode`,
`min_step_identity`, `target_plastic`, `field_separator`, ...); an unknown one is
a `TypeError`. Every job goes through the server's multipart `POST /jobs`; the
deprecated `/bioinformatics/*` job routes are not used.

```python
genomes = client.jobs.compare_genomes({"K-12": "ecoli.faa", "201-F6": "isak.faa.gz"})
genomes.wait().comparison().to_pandas()  # genome x plastic matrix
genomes.download("compare.zip")  # per-genome hits, matrix, provenance.json

client.jobs.hmm_screen(records, target_polymers=["PET", "PLA"])  # list of Bio.SeqRecord
client.jobs.pathway_analysis("proteins.faa", target_plastic="PLA").wait().steps()
client.jobs.annotate_list("taxa.csv", genus_column=1, species_column=2, field_separator="comma")
client.jobs.build_tree(plastic="PET", genus="Pseudomonas").wait().tree().newick
client.jobs.search_sequence("MNFPRASRLMQ...")  # one sequence, synchronous DIAMOND, no job
```

**Inputs.** A FASTA input is a path (plain or gzip), FASTA text, bytes, a file
object, or `Bio.SeqRecord`s. Uploads are named after their contents (`.gz`
exactly when the bytes are gzip). A genome's label is the name its rows carry in
the results (`labels=`, the mapping key, else the file name).

**Hits.** Polling (`wait()`, `refresh()`, `jobs.get()`) asks for the job
without its hits (`?include=none`), so a status check stays small however many
hits a genome has. `hits()` / `results()` then page them from
`GET /jobs/{id}/results`, 500 at a time (`hits(limit=)` for the first few;
`job.iter_hits()` is the lazy, paged collection).

**Views.** `results()` returns every stored hit. The website and `download()`
start from the job's default view (hits covering at least 70 % of the
reference); `download(scov=0)` or `all_hits=True` turns it off, and `scov`,
`qcov` and `bitscore` set thresholds. `comparison()` takes the same arguments.

**Waiting.** `wait(timeout=)` raises `JobTimeoutError` but leaves the job
running: call `wait()` again, or `client.jobs.get(job_id)` later (resume by id).
A failed job raises `JobFailedError` (`.job`, and the server's reason as its
message); `wait(raise_on_error=False)` returns it instead.

**Rate limits.** Submissions are limited (30 an hour). A 429 is waited out when
`Retry-After` is within `max_retry_wait` (60 s by default), otherwise it raises
`RateLimitError`.

**Retries and idempotency.** Every submission (`submit()` and the helpers,
`build_tree()`, `share()`) is sent with an `Idempotency-Key` header: a new UUID
for each call, or the `idempotency_key=` you pass, and the **same** key on every
retry of that call. The server keeps a key for 24 hours per user and answers a
repeat with what the first request created (`Idempotent-Replayed: true`), so the
client retries a submission like a read: on a timeout, a dropped connection or a
5xx (`max_retries`, backoff). If an attempt did reach the server, you get that
job back and `job.replayed` is `True`; nothing runs twice.

```python
job = client.jobs.annotate_genome("genome.faa", idempotency_key="genome-42-run-1")
```

Pass your own key when a script may be re-run after a crash: the re-run gets the
first job back instead of a duplicate. When the retries run out, the error says
which key the submission used, so you can resubmit with it. The same key with a
different request (other parameters, release or file) raises
`IdempotencyKeyReusedError` (a 422); use a new key for a new submission.

**Errors.** A size or count limit (a file over the per-file limit, more than 20
genomes) is a `PayloadTooLargeError` (413); a 422 is a `ValidationError` whose
`.errors` list names each parameter (`loc`, e.g. `["body", "field_separator"]`).
`search_sequence()` raises `ServiceUnavailableError` (503, `.reason`
`index_unavailable` or `binary_unavailable`, `.retry_after`) while its reference
is unavailable, and `ServerError` with `.reason == "timeout"` (504) past the
server's 15 seconds; submit an `annotate_gene` job for that sequence instead.

## Your jobs

```python
client.jobs.list().to_pandas()  # newest first; items are JobInfo
job = client.jobs.get("JOB-ID")  # resume one, then job.wait() / job.results()
```

Result files are deleted under the server's retention policy; `download()` then
raises `ResultsExpiredError` (a `GoneError`, with `.results_expired_at`). The job
record and its hits stay readable.

## Share

```python
link = job.share(expires_in_days=30)  # 1 to 365; default lasts as long as the job
link.url  # the token is shown ONCE: keep this
job.shares()  # your links, newest first (never their tokens)
job.unshare(link)  # revoke at once

shared = client.jobs.shared(link.url)  # anyone with the link; no API key needed
shared.results()  # paged from /jobs/shared/{token}/results
```

`share()` sends an `Idempotency-Key` too: a retried request returns the same
link (the same token), not a second one.

A job opened from a link is read-only: `share()`, `shares()` and `unshare()`
raise `PermissionDenied`.

## Provenance

```python
prov = job.provenance
prov.release, prov.release_source  # "2026.50", "snapshot" ("live" for the live database)
prov.reference_sha256  # SHA-256 of the DIAMOND database or HMM library searched
prov.tool_versions  # {"diamond": "2.1.9"}
prov.record  # the server's full provenance record
```

`release` and `release_source` are the job's own (its record, and the
`X-PlasticDB-Release` / `-Release-Source` headers every job read carries), not
the client's: a job submitted against 2026.30 reports 2026.30 whichever client
reads it.

The downloaded TSV carries the same as `#` header lines, and the compare-genomes
ZIP as `provenance.json`. Record it with your results.

## Jobs on a release

A pinned client (or `release=` on the call) runs the job against that release's
archived DIAMOND databases, HMM library or tables. What a release cannot
answer raises `UnsupportedFilterError` (HTTP 422); an unknown release
`UnknownReleaseError`. A tree is pinned when it is built
(`POST /tree/build?release=`); reading a job never sends a release.

## Command line

```bash
plasticdb jobs submit annotate-genome genome.faa --wait -o hits.tsv --release 2026.50
plasticdb jobs submit annotate-genome genome.faa      # prints the job id
plasticdb jobs submit pathway-analysis prot.faa --target-plastic PLA --idempotency-key run-1
plasticdb jobs submit annotate-list taxa.csv --field-separator comma --genus-column 1 --species-column 2
plasticdb jobs wait JOB-ID --timeout 3600
plasticdb jobs download JOB-ID -o results/
plasticdb jobs share JOB-ID --expires-in-days 30
```

See [Command line](cli.md).
