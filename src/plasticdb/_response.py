from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import errors

RELEASE_HEADER = "X-PlasticDB-Release"
RELEASE_SOURCE_HEADER = "X-PlasticDB-Release-Source"
UNAVAILABLE_HEADER = "X-PlasticDB-Snapshot-Unavailable"


@dataclass(frozen=True)
class APIResponse:
    """One decoded answer from the API, with the release that produced it."""

    status_code: int
    data: Any
    headers: httpx.Headers
    url: str
    release: str | None
    release_source: str | None
    unavailable_fields: tuple[str, ...] = field(default=())
    from_cache: bool = False

    @property
    def pinned(self) -> bool:
        """True when a past release's snapshot answered (not the live database)."""
        return self.release_source == "snapshot"


def release_headers(headers: httpx.Headers) -> tuple[str | None, str | None, tuple[str, ...]]:
    release = headers.get(RELEASE_HEADER) or None
    source = headers.get(RELEASE_SOURCE_HEADER) or None
    raw = headers.get(UNAVAILABLE_HEADER) or ""
    unavailable = tuple(p.strip() for p in raw.split(",") if p.strip())
    return release, source, unavailable


def parse_retry_after(value: str | None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0.0)


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text or None


def _message(response: httpx.Response, detail: Any) -> str:
    prefix = f"{response.status_code} {response.reason_phrase}".strip()
    if isinstance(detail, str) and detail:
        return f"{prefix}: {detail}"
    if isinstance(detail, list) and detail:
        parts = []
        for err in detail:
            if isinstance(err, dict):
                loc = ".".join(str(p) for p in err.get("loc") or [])
                msg = err.get("msg", "")
                parts.append(f"{loc}: {msg}" if loc else str(msg))
        if parts:
            return f"{prefix}: " + "; ".join(parts)
    return prefix


def merge_target(response: httpx.Response) -> dict[str, Any] | None:
    """The body of a merge redirect (``{"accession", "location", "kind"?}``), if it is one."""
    if response.status_code not in (301, 308):
        return None
    body = _body(response)
    if isinstance(body, dict) and isinstance(body.get("accession"), str):
        return body
    return None


def _requested(response: httpx.Response) -> str:
    """The accession segment of a ``/proteins/{accession}[/...]`` request."""
    parts = [p for p in response.request.url.path.split("/") if p]
    try:
        return parts[parts.index("proteins") + 1]
    except (ValueError, IndexError):
        return ""


def error_from_response(response: httpx.Response) -> errors.PlasticDBError:
    """Map a non-success response onto the exception hierarchy."""
    status = response.status_code
    body = _body(response)
    detail = body.get("detail") if isinstance(body, dict) else body
    message = _message(response, detail)
    common: dict[str, Any] = {
        "status_code": status,
        "detail": detail,
        "body": body,
        "response": response,
    }

    merged = merge_target(response)
    if merged is not None:
        target = merged["accession"]
        location = merged.get("location") or response.headers.get("location")
        if merged.get("kind") == "engineered" and _requested(response).upper() == target.upper():
            return errors.EngineeredVariantError(
                f"{target} is an engineered variant: use client.variants.get({target!r})",
                accession=target,
                location=location,
                **common,
            )
        return errors.MergedError(
            f"This accession was merged into {target}",
            merged_into=target,
            location=location,
            kind=merged.get("kind"),
            **common,
        )

    reason = body.get("reason") if isinstance(body, dict) else None
    if isinstance(reason, str):
        release_fields: dict[str, Any] = {
            "release": body.get("release"),
            "current_release": body.get("current_release"),
            "reason": reason,
            **common,
        }
        release_cls: type[errors.ReleaseError] | None = {
            (404, "unknown"): errors.UnknownReleaseError,
            (404, "no_artefacts"): errors.ReleaseNoArtefactsError,
            (409, "live_only"): errors.LiveOnlyError,
            (503, "not_mounted"): errors.ReleaseNotMountedError,
        }.get((status, reason))
        if release_cls is not None:
            return release_cls(message, **release_fields)

    if (
        status == 422
        and isinstance(detail, list)
        and any(isinstance(e, dict) and e.get("type") == "release_unsupported" for e in detail)
    ):
        return errors.UnsupportedFilterError(message, reason="release_unsupported", **common)

    if status == 429:
        return errors.RateLimitError(
            message, retry_after=parse_retry_after(response.headers.get("retry-after")), **common
        )

    simple: dict[int, type[errors.PlasticDBError]] = {
        400: errors.BadRequestError,
        401: errors.AuthenticationError,
        403: errors.PermissionDenied,
        404: errors.NotFoundError,
        409: errors.ConflictError,
        410: errors.GoneError,
        422: errors.ValidationError,
        503: errors.ServiceUnavailableError,
    }
    if status == 503:
        return errors.ServiceUnavailableError(
            message, retry_after=parse_retry_after(response.headers.get("retry-after")), **common
        )
    if status in simple:
        return simple[status](message, **common)
    if status >= 500:
        return errors.ServerError(message, **common)
    if 300 <= status < 400:
        location = response.headers.get("location")
        return errors.UnexpectedResponseError(
            f"Unexpected redirect ({status}) to {location}", **common
        )
    return errors.PlasticDBError(message, **common)
