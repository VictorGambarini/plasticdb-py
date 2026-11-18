from __future__ import annotations

from .. import errors
from .._endpoints import Endpoint
from .._pagination import Collection
from ..models.releases import CurrentRelease, Release, ReleaseStatus
from ._base import Resource

# Release metadata describes every release, so it is never itself pinned.
_LIST = Endpoint("GET", "/releases", pinnable=False)
_CURRENT = Endpoint("GET", "/releases/current", pinnable=False)
_STATUS = Endpoint("GET", "/releases/status", pinnable=False)
_GET = Endpoint("GET", "/releases/{version}", pinnable=False)


class Releases(Resource):
    """Dataset releases: the named, citable versions of the data."""

    def current(self) -> CurrentRelease:
        """The release the server is serving now. ``version`` is ``None`` before the first."""
        return CurrentRelease.model_validate(self._client._request(_CURRENT).data)

    def get(self, version: str) -> Release:
        """One release with its artefacts.

        An unknown version raises :class:`~plasticdb.errors.UnknownReleaseError`
        (a :class:`~plasticdb.errors.NotFoundError`).
        """
        try:
            data = self._client._request(_GET, version=version).data
        except errors.UnknownReleaseError:
            raise
        except errors.NotFoundError as exc:
            raise errors.UnknownReleaseError(
                exc.message,
                release=version,
                reason="unknown",
                status_code=exc.status_code,
                detail=exc.detail,
                body=exc.body,
                response=exc.response,
            ) from exc
        return Release.model_validate(data)

    def list(self, *, page_size: int = 50, limit: int | None = None) -> Collection[Release]:
        """Every completed release, newest first."""
        return Collection(self._client, _LIST, Release, page_size=page_size, limit=limit)

    def status(self) -> ReleaseStatus:
        """Whether a release is being cut right now."""
        return ReleaseStatus.model_validate(self._client._request(_STATUS).data)
