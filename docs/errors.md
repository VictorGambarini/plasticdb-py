# Errors

Every exception subclasses `plasticdb.PlasticDBError`, which carries `message`,
`status_code`, the server's `detail`, the parsed `body` and the `response`.
Some classes have two parents on purpose, so `except NotFoundError` also catches
an unknown release and `except ValidationError` also catches a filter a release
cannot answer.

```python
try:
    client.proteins.get("PB0000")
except plasticdb.MergedError as exc:  # check subclasses before their parents
    print("use", exc.merged_into)
except plasticdb.NotFoundError:
    ...
except plasticdb.PlasticDBError as exc:
    print(exc.status_code, exc.message)
```

## Hierarchy

```text
PlasticDBError
├─ APIConnectionError                    server unreachable, or the connection broke
│  └─ APITimeoutError                    the request timed out
├─ UnexpectedResponseError               the server answered something not understood
├─ BadRequestError                       400
├─ AuthenticationError                   401   no key, or an invalid or expired one
├─ PermissionDenied                      403   valid key, not allowed
├─ NotFoundError                         404
│  ├─ UnknownReleaseError                404   no completed release has that version *
│  └─ ReleaseNoArtefactsError            404   the release predates its artefacts *
├─ GoneError                             410   withdrawn record, or an expired share link
│  └─ MergedError                        301   merged; .merged_into is the survivor
├─ EngineeredVariantError                the accession is a variant, not a wild type
├─ ConflictError                         409
│  └─ LiveOnlyError                      409   endpoint cannot be pinned to a release *
├─ PayloadTooLargeError                  413   upload over the size limit
├─ ValidationError                       422   .errors lists the problems
│  └─ UnsupportedFilterError             422   a past release cannot answer this filter *
├─ RateLimitError                        429   .retry_after (seconds)
├─ ServerError                           5xx
│  └─ ServiceUnavailableError            503   .retry_after
│     └─ ReleaseNotMountedError          503   the release's files are not readable there *
├─ ChecksumError                         a download did not match its published SHA-256
├─ ReleaseError                          * every starred class is also a ReleaseError
│  ├─ ReleaseNotAvailableError           unknown, no artefacts, or not mounted
│  └─ ReleaseUnsupportedError            exists, but cannot answer this endpoint or parameter
└─ JobError                              .job
   ├─ JobFailedError                     the job finished with status "error"
   └─ JobTimeoutError                    wait(timeout=) ran out (also a TimeoutError)
```

Starred classes have two parents (their HTTP class and a `ReleaseError`
branch); `ReleaseError` carries `.release`, `.current_release` and `.reason`.
`MergedRecordWarning` (a `UserWarning`) is issued when `follow_merges=True`
returns a survivor.

## HTTP status to exception

| Status | Exception |
|---|---|
| 301 (merged accession) | `MergedError` (a `GoneError`) |
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` (also raised locally before a job is sent without a key) |
| 403 | `PermissionDenied` |
| 404 | `NotFoundError`; `UnknownReleaseError` or `ReleaseNoArtefactsError` when a release is the cause |
| 409 | `ConflictError`; `LiveOnlyError` for `live_only` |
| 410 | `GoneError` |
| 413 | `PayloadTooLargeError` |
| 422 | `ValidationError`; `UnsupportedFilterError` for `release_unsupported` |
| 429 | `RateLimitError` (after the retry budget) |
| 5xx | `ServerError`; `ServiceUnavailableError` for 503; `ReleaseNotMountedError` for `not_mounted` |
| no response | `APIConnectionError` / `APITimeoutError` |

Retries: 429s are retried after `Retry-After` (up to `max_retry_wait`) and
idempotent requests are retried with backoff on 5xx and connection errors
(`max_retries`, default 3). Job submissions are never resent once they may have
reached the server.

## Exit codes on the command line

The CLI maps exceptions to [exit codes](cli.md#exit-codes): releases to 6, a
failed job to 7, timeouts to 8, authentication to 4, rate limits to 5, missing or
withdrawn records to 3, bad input (including 422, 400, 413 and
`EngineeredVariantError`) to 2, server trouble to 9, anything else to 1.
