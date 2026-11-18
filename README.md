# plasticdb

Python client for the [PlasticDB](https://v2.plasticdb.org) API: microorganisms
and proteins linked to plastic biodegradation, and the evidence behind them.

> **Pre-alpha.** This release covers the client core and dataset releases.
> Data endpoints, jobs and the command-line tool are coming in later versions.

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

with plasticdb.Client() as client:
    current = client.releases.current()
    print(current.version)  # None until the first dataset release

    for release in client.releases.list():
        print(release.version, release.doi)

    print(client.cite())
```

### Reproducible results: pin a release

```python
client = plasticdb.Client(release="2026.50")
client.provenance()  # release, source (snapshot/live), server and SDK versions
```

A pinned client sends `?release=` on every read, so the answers come from that
release's published data. An unpinned client reads the live database and logs
(once, at INFO on the `plasticdb` logger) which release answered. Pass
`cache_dir=` to keep answers from a past release on disk: past releases never
change, so they are cached indefinitely. Live answers are never cached.

### Configuration

| Argument | Environment variable | Default |
|---|---|---|
| `api_key` | `PLASTICDB_API_KEY` | none (reads are anonymous; jobs need a key) |
| `release` | `PLASTICDB_RELEASE` | none (live database) |
| `base_url` | `PLASTICDB_BASE_URL` | `https://v2.plasticdb.org/api/v1` |
| `cache_dir` | | none |
| `timeout`, `max_retries`, `max_retry_wait` | | 30 s, 3, 60 s |

Rate-limited requests (429) are retried after `Retry-After`, and idempotent
requests are retried with backoff on 5xx and connection errors.

### Errors

Every exception subclasses `plasticdb.PlasticDBError`. Examples are
`NotFoundError`, `GoneError`, `MergedError` (with `.merged_into`),
`AuthenticationError`, `PermissionDenied`, `RateLimitError` (with `.retry_after`),
`ValidationError`, `ServerError`, and the release errors
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
