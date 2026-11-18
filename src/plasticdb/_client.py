from __future__ import annotations

import logging
import os
import platform
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

import httpx

from . import errors
from ._cache import ResponseCache
from ._endpoints import Endpoint
from ._pagination import ChangeCollection
from ._response import (
    IDEMPOTENCY_HEADER,
    APIResponse,
    error_from_response,
    new_idempotency_key,
    parse_retry_after,
    release_headers,
)
from ._version import __version__
from .models import Change
from .models.releases import DATA_LICENSE, DATA_LICENSE_URL, PAPER_CITATION, PAPER_DOI
from .resources.catalogue import Microorganisms, Papers, Plastics
from .resources.downloads import Downloads
from .resources.evidence import Evidence, Pathways, Search, StatsResource
from .resources.jobs import Jobs
from .resources.proteins import Proteins, Variants
from .resources.releases import Releases

log = logging.getLogger("plasticdb")

DEFAULT_BASE_URL = "https://v2.plasticdb.org/api/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_WAIT = 60.0
DEFAULT_MAX_CONCURRENCY = 4

_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_RETRY_STATUS = frozenset({500, 502, 503, 504})

_HEALTH = Endpoint("GET", "/health", pinnable=False, in_contract=False, root=True)
_CHANGES = Endpoint("GET", "/changes", live_only=True)


def user_agent() -> str:
    return (
        f"plasticdb-py/{__version__} "
        f"(python/{platform.python_version()}; httpx/{httpx.__version__})"
    )


@dataclass(frozen=True)
class Provenance:
    """Where an analysis' data came from: record this alongside results."""

    release: str | None
    release_source: str | None
    pinned: bool
    base_url: str
    sdk_version: str
    server_version: str | None
    data_license: str = DATA_LICENSE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Citation:
    """How to cite the data. ``str(citation)`` is the full text to paste."""

    dataset: str
    release: str | None
    doi: str | None
    paper: str = PAPER_CITATION
    paper_doi: str = PAPER_DOI
    license: str = DATA_LICENSE
    license_url: str = DATA_LICENSE_URL

    def __str__(self) -> str:
        return "\n".join(
            [
                self.dataset,
                f"{self.paper} https://doi.org/{self.paper_doi}",
                f"Data licence: {self.license} ({self.license_url}).",
            ]
        )


class Client:
    """A synchronous client for the PlasticDB API.

    ``api_key`` defaults to ``$PLASTICDB_API_KEY``; reads need none. ``release``
    (default ``$PLASTICDB_RELEASE``) pins every read to a dataset release, sent
    as ``?release=``. With ``cache_dir``, answers from a past release's snapshot
    are kept on disk; live answers never are. ``max_concurrency`` caps the
    parallel page requests of a bulk pull (``Collection.all()``); 1 disables it.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        release: str | None = None,
        base_url: str | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
        backoff_factor: float = 0.5,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._api_key = _clean(api_key) or _clean(os.environ.get("PLASTICDB_API_KEY"))
        self._release = _clean(release) or _clean(os.environ.get("PLASTICDB_RELEASE"))
        url = _clean(base_url) or _clean(os.environ.get("PLASTICDB_BASE_URL")) or DEFAULT_BASE_URL
        self._base_url = url.rstrip("/")
        self._root_url = _root_of(self._base_url)
        self._cache = ResponseCache(cache_dir) if cache_dir is not None else None
        self.max_retries = max_retries
        self.max_retry_wait = max_retry_wait
        self.backoff_factor = backoff_factor
        self.max_concurrency = max_concurrency

        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        limits = httpx.Limits(max_connections=max(max_concurrency, 4) * 2)
        self._http = httpx.Client(
            headers=headers, timeout=timeout, follow_redirects=False, limits=limits
        )

        self._last: APIResponse | None = None
        self._last_read: APIResponse | None = None
        self._announced = False
        self._server_version: str | None = None

        self.releases = Releases(self)
        self.proteins = Proteins(self)
        self.variants = Variants(self)
        self.microorganisms = Microorganisms(self)
        self.plastics = Plastics(self)
        self.papers = Papers(self)
        self.evidence = Evidence(self)
        self.search = Search(self)
        self.pathways = Pathways(self)
        self.stats = StatsResource(self)
        self.downloads = Downloads(self)
        self.jobs = Jobs(self)

    # ── configuration ────────────────────────────────────────────────────────

    @property
    def release(self) -> str | None:
        """The pinned release, or ``None`` for the live database."""
        return self._release

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def require_api_key(self, what: str = "Jobs") -> None:
        """Raise :class:`~plasticdb.AuthenticationError` before a call that needs a key."""
        if not self._api_key:
            raise errors.AuthenticationError(
                f"{what} need an API key. Pass Client(api_key=...) or set PLASTICDB_API_KEY; "
                f"create a key at {self._root_url}/profile/api-keys"
            )

    @property
    def cache(self) -> ResponseCache | None:
        return self._cache

    @property
    def last_response(self) -> APIResponse | None:
        """The most recent answer, with its release and source."""
        return self._last

    def __repr__(self) -> str:
        key = "set" if self._api_key else "none"
        return f"<plasticdb.Client {self._base_url} release={self._release!r} api_key={key}>"

    # ── lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ── change feed ──────────────────────────────────────────────────────────

    def changes(
        self,
        since: datetime | str,
        *,
        entity_type: Literal["protein", "protein_variant", "microorganism", "plastic", "paper"]
        | None = None,
        page_size: int | None = None,
        limit: int | None = None,
    ) -> ChangeCollection[Change]:
        """Records updated, merged or removed at or after ``since`` (live only).

        To keep a local copy in sync, store ``feed.as_of`` and pass it as the next
        ``since``. A merge names its survivor in ``merged_into``.
        """
        return ChangeCollection(
            self,
            _CHANGES,
            Change,
            params={"since": since, "entity_type": entity_type},
            page_size=page_size or 500,
            limit=limit,
            max_page_size=5000,
        )

    # ── provenance and citation ──────────────────────────────────────────────

    def provenance(self) -> Provenance:
        """The release, its source, the server and this SDK's versions.

        Makes a request when nothing has been asked yet, so the release is known.
        """
        if self._last is None:
            if self._release:
                self.releases.get(self._release)
            else:
                self.releases.current()
        read = self._last_read or self._last
        release = self._release or (read.release if read else None)
        if self._last_read is not None:
            source = self._last_read.release_source
        else:
            source = None if self._release else "live"
        return Provenance(
            release=release,
            release_source=source,
            pinned=self._release is not None,
            base_url=self._base_url,
            sdk_version=__version__,
            server_version=self.server_version(),
        )

    def server_version(self) -> str | None:
        """The server's application version (``/health``), or ``None`` if unavailable."""
        if self._server_version is None:
            try:
                data = self._request(_HEALTH, record=False).data
            except errors.PlasticDBError:
                return None
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                self._server_version = data["version"]
        return self._server_version

    def cite(self) -> Citation:
        """How to cite the data this client reads, with the CC BY 4.0 licence.

        A pinned client cites its release; an unpinned one the current release.
        If the server cannot be reached the citation still names the paper.
        """
        try:
            if self._release:
                rel = self.releases.get(self._release)
                return Citation(
                    dataset=_dataset_citation(rel.version, rel.doi),
                    release=rel.version,
                    doi=rel.doi,
                )
            current = self.releases.current()
        except (errors.APIConnectionError, errors.ServerError) as exc:
            log.warning("PlasticDB: could not fetch the release citation (%s)", exc)
            return Citation(
                dataset=self._undated_citation(self._release), release=self._release, doi=None
            )
        if current.version:
            text = current.citation or _dataset_citation(current.version, current.doi)
            return Citation(
                dataset=text,
                release=current.version,
                doi=current.doi,
                paper=current.paper_citation,
                paper_doi=current.paper_doi,
                license=current.license,
                license_url=current.license_url,
            )
        return Citation(
            dataset=self._undated_citation(None),
            release=None,
            doi=None,
            paper=current.paper_citation,
            paper_doi=current.paper_doi,
            license=current.license,
            license_url=current.license_url,
        )

    def _undated_citation(self, release: str | None) -> str:
        if release:
            return _dataset_citation(release, None)
        return (
            "PlasticDB (live database; no dataset release named). University of Auckland. "
            f"{self._root_url}/ (accessed {date.today().isoformat()})"
        )

    # ── transport ────────────────────────────────────────────────────────────

    def _url(self, endpoint: Endpoint, path_params: Mapping[str, Any]) -> str:
        base = self._root_url if endpoint.root else self._base_url
        return base + endpoint.format(**path_params)

    def _request(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: FilesFactory | None = None,
        follow_redirects: bool = False,
        record: bool = True,
        idempotency_key: str | None = None,
        **path_params: Any,
    ) -> APIResponse:
        """Send one API call; raise a :class:`~plasticdb.errors.PlasticDBError` on failure.

        On an ``idempotency`` endpoint every attempt carries the same
        ``Idempotency-Key`` (``idempotency_key``, or a new UUID).
        """
        method, url, pinned_release = self._prepare(endpoint, params, path_params)
        send_headers: dict[str, str] | None = None
        if endpoint.idempotency:
            send_headers = {IDEMPOTENCY_HEADER: idempotency_key or new_idempotency_key()}
        cache_key = None
        if self._cache is not None and method == "GET" and pinned_release is not None:
            cache_key = ResponseCache.key(method, url)
            hit = self._cache.get(pinned_release, cache_key)
            if hit is not None:
                status, headers, body = hit
                cached = httpx.Response(
                    status, headers=headers, content=body, request=httpx.Request(method, url)
                )
                return self._decode(cached, endpoint, from_cache=True, record=record)

        response = self._send(
            method,
            url,
            json=json,
            data=data,
            files=files,
            follow_redirects=follow_redirects,
            headers=send_headers,
            retry_safe=self._retry_safe(endpoint),
        )
        if not response.is_success:
            raise error_from_response(response)
        result = self._decode(response, endpoint, record=record)
        if (
            cache_key is not None
            and self._cache is not None
            and pinned_release is not None
            and result.release_source == "snapshot"
            and result.release == pinned_release
        ):
            self._cache.put(pinned_release, cache_key, response)
        return result

    def _prepare(
        self,
        endpoint: Endpoint,
        params: Mapping[str, Any] | None,
        path_params: Mapping[str, Any],
    ) -> tuple[str, httpx.URL, str | None]:
        """The method, the URL and the release the request is pinned to (if any)."""
        query: dict[str, Any] = {}
        for k, v in (params or {}).items():
            if v is not None:
                query[k] = _param(v)
        if endpoint.pinnable and self._release and "release" not in query:
            query["release"] = self._release
        url = httpx.URL(self._url(endpoint, path_params), params=query)
        pinned = query.get("release") if endpoint.pinnable else None
        return endpoint.method.upper(), url, pinned if isinstance(pinned, str) else None

    def _download(
        self,
        endpoint: Endpoint,
        dest: str | os.PathLike[str],
        *,
        params: Mapping[str, Any] | None = None,
        sha256: str | None = None,
        immutable_release: str | None = None,
        default_name: str = "download",
        **path_params: Any,
    ) -> Path:
        """Stream a file to ``dest`` (a file, or a directory to name it in).

        Redirects are followed. With ``sha256`` the file is verified before it is
        moved into place. A pinned snapshot answer (or any answer for
        ``immutable_release``, a release-scoped artefact) is kept in the cache.
        """
        from ._download import stream_to_file

        return stream_to_file(
            self,
            endpoint,
            dest,
            params=params,
            path_params=path_params,
            sha256=sha256,
            immutable_release=immutable_release,
            default_name=default_name,
        )

    def _decode(
        self,
        response: httpx.Response,
        endpoint: Endpoint,
        *,
        from_cache: bool = False,
        record: bool = True,
    ) -> APIResponse:
        release, source, unavailable = release_headers(response.headers)
        data: Any
        if response.status_code == 204 or not response.content:
            data = None
        elif "json" in response.headers.get("content-type", ""):
            try:
                data = response.json()
            except ValueError as exc:
                raise errors.UnexpectedResponseError(
                    "The server sent invalid JSON", response=response
                ) from exc
        else:
            data = response.content
        result = APIResponse(
            status_code=response.status_code,
            data=data,
            headers=response.headers,
            url=str(response.request.url),
            release=release,
            release_source=source,
            unavailable_fields=unavailable,
            from_cache=from_cache,
        )
        if record:
            self._remember(result, endpoint)
        return result

    def _remember(self, result: APIResponse, endpoint: Endpoint) -> None:
        self._last = result
        if endpoint.pinnable:
            self._last_read = result
        self._announce(result)

    def _announce(self, result: APIResponse) -> None:
        if self._announced or self._release is not None:
            return
        self._announced = True
        if result.release:
            log.info(
                "PlasticDB: unpinned client, answered by the live database (release %s). "
                "Pass release=%r to Client() to make these results reproducible.",
                result.release,
                result.release,
            )
        else:
            log.info("PlasticDB: unpinned client; the server named no dataset release.")

    def _retry_safe(self, endpoint: Endpoint) -> bool:
        """Whether a ``POST`` to ``endpoint`` may be resent after it reached the server.

        True for a search that creates nothing, and for a create operation that
        honours ``Idempotency-Key`` when the client has a credential (the server
        scopes keys per user and ignores them on anonymous requests).
        """
        return endpoint.retry_safe or (endpoint.idempotency and bool(self._api_key))

    def _send(
        self,
        method: str,
        url: httpx.URL,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: FilesFactory | None = None,
        follow_redirects: bool = False,
        stream: bool = False,
        headers: Mapping[str, str] | None = None,
        retry_safe: bool = False,
    ) -> httpx.Response:
        """Send with retries. With ``stream``, a successful body is left unread.

        ``GET`` (and any ``retry_safe`` request: a search, or a submission whose
        ``Idempotency-Key`` makes a repeat return the first result) is resent on
        a transport error, a 5xx and a 429. Any other ``POST`` is resent only when
        it never reached the server (a connection error) or the server refused it
        unprocessed (429). ``files`` is a factory so every attempt uploads from
        fresh handles.
        """
        idempotent = method in _IDEMPOTENT or retry_safe
        attempt = 0
        while True:
            uploads = files() if files is not None else None
            try:
                request = self._http.build_request(
                    method,
                    url,
                    json=json,
                    data=dict(data) if data else None,
                    files=uploads,
                    headers=dict(headers) if headers else None,
                )
                response = self._http.send(
                    request, stream=stream, follow_redirects=follow_redirects
                )
                if stream and not response.is_success:
                    response.read()
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the server, so even a POST is safe to resend.
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise _transport_error(exc) from exc
            except httpx.TransportError as exc:
                if idempotent and attempt < self.max_retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise _transport_error(exc) from exc
            finally:
                _close_uploads(uploads)

            if attempt >= self.max_retries:
                return response
            status = response.status_code
            retryable = status in _RETRY_STATUS and idempotent and not _is_permanent(response)
            if status == 429 or retryable:
                wait = parse_retry_after(response.headers.get("retry-after"))
                wait = self._backoff(attempt) if wait is None else wait
            else:
                return response
            if wait > self.max_retry_wait:
                return response
            response.close()
            log.debug("PlasticDB: %s %s answered %s; retrying in %.1fs", method, url, status, wait)
            self._sleep(wait)
            attempt += 1

    def _backoff(self, attempt: int) -> float:
        delay = self.backoff_factor * (2**attempt)
        return float(min(delay + random.uniform(0, delay / 4), self.max_retry_wait))

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)


#: Opens the multipart files of one attempt: ``[(field, (filename, handle, type)), ...]``.
FilesFactory = Callable[[], list[tuple[str, tuple[str, Any, str]]]]


def _close_uploads(uploads: list[tuple[str, tuple[str, Any, str]]] | None) -> None:
    for _, (_, handle, _) in uploads or []:
        close = getattr(handle, "close", None)
        if callable(close):
            close()


_PERMANENT = frozenset({(503, "not_mounted"), (504, "timeout")})


def _is_permanent(response: httpx.Response) -> bool:
    """A 503 ``not_mounted`` (or a search's 504 ``timeout``) will not fix itself by retrying."""
    if response.status_code not in (503, 504):
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (response.status_code, body.get("reason")) in _PERMANENT


def _transport_error(exc: httpx.TransportError) -> errors.APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return errors.APITimeoutError(f"Request timed out: {exc}")
    return errors.APIConnectionError(f"Could not reach PlasticDB: {exc}")


def _dataset_citation(version: str, doi: str | None) -> str:
    text = f"PlasticDB dataset release {version}. University of Auckland."
    return f"{text} https://doi.org/{doi}" if doi else text


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _root_of(base_url: str) -> str:
    for suffix in ("/api/v1",):
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)]
    url = httpx.URL(base_url)
    return str(url.copy_with(path="/", query=None)).rstrip("/")


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_param(v) for v in value]
    return value
