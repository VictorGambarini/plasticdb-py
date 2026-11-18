"""Exceptions raised by the PlasticDB client.

Every error is a :class:`PlasticDBError`. HTTP errors carry the status code,
the server's ``detail``, the parsed body and its machine-readable ``reason``
when it sent one (``withdrawn``, ``expired``, ``archive_unavailable``,
``timeout``…). Some classes inherit from two parents so that
``except NotFoundError`` also catches an unknown release, and
``except ValidationError`` also catches a filter a release cannot answer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ChecksumError",
    "ConflictError",
    "EngineeredVariantError",
    "GoneError",
    "IdempotencyKeyReusedError",
    "JobError",
    "JobFailedError",
    "JobTimeoutError",
    "LiveOnlyError",
    "MergedError",
    "MergedRecordWarning",
    "NotFoundError",
    "PayloadTooLargeError",
    "PermissionDenied",
    "PlasticDBError",
    "RateLimitError",
    "ReleaseError",
    "ReleaseNoArtefactsError",
    "ReleaseNotAvailableError",
    "ReleaseNotMountedError",
    "ReleaseUnsupportedError",
    "ResultsExpiredError",
    "ServerError",
    "ServiceUnavailableError",
    "UnexpectedResponseError",
    "UnknownReleaseError",
    "UnsupportedFilterError",
    "ValidationError",
]


class PlasticDBError(Exception):
    """Base class for every error this package raises."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: Any = None,
        body: Any = None,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.body = body
        self.response = response
        reason = body.get("reason") if isinstance(body, dict) else None
        #: The server's machine-readable reason, when the error body carried one.
        self.reason: str | None = reason if isinstance(reason, str) else None

    @property
    def url(self) -> str | None:
        if self.response is None:
            return None
        return str(self.response.request.url)


class APIConnectionError(PlasticDBError):
    """The server could not be reached, or the connection broke mid-request."""


class APITimeoutError(APIConnectionError):
    """The request timed out."""


class UnexpectedResponseError(PlasticDBError):
    """The server answered something this client does not understand."""


class BadRequestError(PlasticDBError):
    """400: the request was malformed."""


class AuthenticationError(PlasticDBError):
    """401: no credential, or an invalid or expired API key."""


class PermissionDenied(PlasticDBError):
    """403: the credential is valid but may not do this."""


class NotFoundError(PlasticDBError):
    """404: no such record (or no such release, see :class:`UnknownReleaseError`)."""


class GoneError(PlasticDBError):
    """410: the record was withdrawn, a job's result files expired, or a share link did.

    A withdrawn protein, variant or organism has ``reason == "withdrawn"`` and
    ``accession`` set (a variant is withdrawn with its parent protein, too).
    """

    @property
    def accession(self) -> str | None:
        value = self.body.get("accession") if isinstance(self.body, dict) else None
        return value if isinstance(value, str) else None


class ResultsExpiredError(GoneError):
    """410 ``expired``: the job's result files were deleted under the retention policy.

    ``results_expired_at`` is when (the job record, its hits and its summary remain).
    """

    def __init__(
        self, message: str, *, results_expired_at: datetime | None = None, **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)
        self.results_expired_at = results_expired_at


class MergedError(GoneError):
    """The accession was merged into another record (the server answers 301).

    ``merged_into`` is the surviving accession and ``location`` the API path
    that serves it. ``kind`` is what the survivor is: ``"wild_type"``,
    ``"engineered"`` (a variant) or ``"microorganism"``.
    """

    def __init__(
        self,
        message: str,
        *,
        merged_into: str,
        location: str | None = None,
        kind: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.merged_into = merged_into
        self.location = location
        self.kind = kind


class EngineeredVariantError(NotFoundError):
    """The accession names an engineered variant, not a wild-type protein.

    The server answers ``/proteins/{accession}`` for a variant with a 301
    (``reason: engineered_variant``) to ``/variants/{accession}``;
    :meth:`Proteins.get` follows it and returns the :class:`~plasticdb.models.Variant`.
    The per-protein sub-collections (interactions, kinetics, properties, variants)
    answer a 404 with the same reason, raised as this error, because variants
    have none. ``accession`` is the variant, ``location`` its path and
    ``merged_into`` the surviving variant when the accession was also merged.
    """

    def __init__(
        self,
        message: str,
        *,
        accession: str,
        location: str | None = None,
        merged_into: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.accession = accession
        self.location = location
        self.merged_into = merged_into


class MergedRecordWarning(UserWarning):
    """Issued when ``get(..., follow_merges=True)`` returns a merge survivor."""


class ConflictError(PlasticDBError):
    """409."""


class PayloadTooLargeError(PlasticDBError):
    """413: an upload is over the server's per-file or per-job size limit."""


class ValidationError(PlasticDBError):
    """422: a parameter was invalid. ``errors`` is the server's list of problems."""

    @property
    def errors(self) -> list[dict[str, Any]]:
        if isinstance(self.detail, list):
            return [e for e in self.detail if isinstance(e, dict)]
        return []


class IdempotencyKeyReusedError(ValidationError):
    """422 ``idempotency_key_reused``: the key was already used for a different request.

    A job, tree or share-link submission sent with the ``Idempotency-Key`` of an
    earlier one (within 24 hours) must repeat that request exactly; use a new key
    for a new submission.
    """


class RateLimitError(PlasticDBError):
    """429: too many requests. ``retry_after`` is in seconds, when the server said."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(PlasticDBError):
    """5xx: the server failed.

    A synchronous sequence search past the server's 15 seconds is a 504 with
    ``reason == "timeout"`` (submit an ``annotate_gene`` job instead); a DIAMOND
    failure is a 502.
    """


class ServiceUnavailableError(ServerError):
    """503: the server, or a service it depends on, is temporarily unavailable.

    ``retry_after`` is the server's ``Retry-After`` in seconds, when it sent one,
    and ``reason`` what is unavailable: ``archive_unavailable`` (``dataset.zip``
    is being built), ``index_unavailable`` / ``binary_unavailable`` (the sequence
    search's reference database or DIAMOND).
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ChecksumError(PlasticDBError):
    """A downloaded file did not match the SHA-256 the server published for it."""

    def __init__(self, message: str, *, expected: str, actual: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.expected = expected
        self.actual = actual


class ReleaseError(PlasticDBError):
    """A problem with the dataset release a request asked for.

    ``release`` is the version asked for, ``current_release`` the one the server
    is serving (when it said), and ``reason`` the server's machine-readable code.
    """

    def __init__(
        self,
        message: str,
        *,
        release: str | None = None,
        current_release: str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.release = release
        self.current_release = current_release
        self.reason = reason


class ReleaseNotAvailableError(ReleaseError):
    """The release cannot answer at all: unknown, without artefacts, or not mounted."""


class UnknownReleaseError(ReleaseNotAvailableError, NotFoundError):
    """404 ``unknown``: no completed release has that version."""


class ReleaseNoArtefactsError(ReleaseNotAvailableError, NotFoundError):
    """404 ``no_artefacts``: the release predates its queryable artefacts."""


class ReleaseNotMountedError(ReleaseNotAvailableError, ServiceUnavailableError):
    """503 ``not_mounted``: the release's files are not readable on this server."""


class ReleaseUnsupportedError(ReleaseError):
    """The release exists, but cannot answer this endpoint or parameter."""


class LiveOnlyError(ReleaseUnsupportedError, ConflictError):
    """409 ``live_only``: the endpoint is computed live and cannot be pinned."""


class UnsupportedFilterError(ReleaseUnsupportedError, ValidationError):
    """422 ``release_unsupported``: a filter or option a past release cannot answer.

    ``parameters`` names them.
    """

    @property
    def parameters(self) -> list[str]:
        names: list[str] = []
        for err in self.errors:
            if err.get("type") != "release_unsupported":
                continue
            loc = err.get("loc") or []
            if loc:
                names.append(str(loc[-1]))
        return names


class JobError(PlasticDBError):
    """A problem with a submitted job. ``job`` is the :class:`~plasticdb.Job`."""

    def __init__(self, message: str, *, job: Any, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.job = job


class JobFailedError(JobError):
    """The job finished with status ``error``; ``message`` is the server's reason."""


class JobTimeoutError(JobError, TimeoutError):
    """``Job.wait(timeout=)`` ran out before the job finished. The job keeps running."""
