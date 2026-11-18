"""Exceptions raised by the PlasticDB client.

Every error is a :class:`PlasticDBError`. HTTP errors carry the status code, the
server's ``detail`` and the parsed body; errors about a ``release`` also carry
the server's machine-readable ``reason``. Some classes inherit from two parents
so that ``except NotFoundError`` also catches an unknown release, and
``except ValidationError`` also catches a filter a release cannot answer.
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "GoneError",
    "LiveOnlyError",
    "MergedError",
    "NotFoundError",
    "PermissionDenied",
    "PlasticDBError",
    "RateLimitError",
    "ReleaseError",
    "ReleaseNoArtefactsError",
    "ReleaseNotAvailableError",
    "ReleaseNotMountedError",
    "ReleaseUnsupportedError",
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
    """410: the record was withdrawn (tombstoned), or a share link expired."""


class MergedError(GoneError):
    """The accession was merged into another record (the server answers 301).

    ``merged_into`` is the surviving accession and ``location`` the API path
    that serves it. ``kind`` is ``"engineered"`` when the survivor is a variant.
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


class ConflictError(PlasticDBError):
    """409."""


class ValidationError(PlasticDBError):
    """422: a parameter was invalid. ``errors`` is the server's list of problems."""

    @property
    def errors(self) -> list[dict[str, Any]]:
        if isinstance(self.detail, list):
            return [e for e in self.detail if isinstance(e, dict)]
        return []


class RateLimitError(PlasticDBError):
    """429: too many requests. ``retry_after`` is in seconds, when the server said."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(PlasticDBError):
    """5xx: the server failed."""


class ServiceUnavailableError(ServerError):
    """503: the server, or a service it depends on, is temporarily unavailable."""


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
