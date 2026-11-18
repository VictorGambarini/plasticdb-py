from __future__ import annotations

import email.utils
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from . import errors

RELEASE_HEADER = "X-PlasticDB-Release"
RELEASE_SOURCE_HEADER = "X-PlasticDB-Release-Source"
UNAVAILABLE_HEADER = "X-PlasticDB-Snapshot-Unavailable"
SHA256_HEADER = "X-PlasticDB-SHA256"
SOURCE_SHA256_HEADER = "X-PlasticDB-Source-SHA256"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"


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

    @property
    def etag(self) -> str | None:
        """The ``ETag`` (weak on a pinned download, the file's SHA-256 on an artefact)."""
        return self.headers.get("etag") or None

    @property
    def sha256(self) -> str | None:
        """A release artefact's own SHA-256 (``X-PlasticDB-SHA256``)."""
        return self.headers.get(SHA256_HEADER) or None

    @property
    def source_sha256(self) -> tuple[str, ...]:
        """A pinned download's source: the SHA-256 of each release Parquet artefact
        it was rendered from, as in the release's ``MANIFEST.json``
        (``X-PlasticDB-Source-SHA256``; FASTA: proteins, then protein_variants)."""
        raw = self.headers.get(SOURCE_SHA256_HEADER) or ""
        return tuple(p.strip() for p in raw.split(",") if p.strip())

    @property
    def replayed(self) -> bool:
        """True when an ``Idempotency-Key`` submission returned what an earlier one created."""
        return (self.headers.get(REPLAYED_HEADER) or "").lower() == "true"


def new_idempotency_key() -> str:
    """A fresh ``Idempotency-Key``: one per logical submission, reused on every retry."""
    return str(uuid.uuid4())


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
    """The body of a record redirect (``{accession, location, kind, reason, merged_into?}``)."""
    if response.status_code not in (301, 308):
        return None
    body = _body(response)
    if isinstance(body, dict) and isinstance(body.get("accession"), str):
        return body
    return None


def _text(body: Any, key: str) -> str | None:
    value = body.get(key) if isinstance(body, dict) else None
    return value if isinstance(value, str) and value else None


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _variant_message(accession: str) -> str:
    return f"{accession} is an engineered variant: use client.variants.get({accession!r})"


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

    redirect = merge_target(response)
    if redirect is not None:
        target = redirect["accession"]
        location = _text(redirect, "location") or response.headers.get("location")
        if redirect.get("reason") == "engineered_variant":
            return errors.EngineeredVariantError(
                _variant_message(target), accession=target, location=location, **common
            )
        return errors.MergedError(
            f"This accession was merged into {target}",
            merged_into=_text(redirect, "merged_into") or target,
            location=location,
            kind=_text(redirect, "kind"),
            **common,
        )

    reason = _text(body, "reason")
    if status == 404 and reason == "engineered_variant":
        accession = _text(body, "accession") or ""
        return errors.EngineeredVariantError(
            _variant_message(accession) + " (variants have no per-protein sub-resources)",
            accession=accession,
            location=_text(body, "location"),
            merged_into=_text(body, "merged_into"),
            **common,
        )
    if status == 410 and reason == "expired":
        return errors.ResultsExpiredError(
            message, results_expired_at=_when(_text(body, "results_expired_at")), **common
        )
    if status == 422 and isinstance(detail, list):
        types = {e.get("type") for e in detail if isinstance(e, dict)}
        if "idempotency_key_reused" in types:
            return errors.IdempotencyKeyReusedError(message, **common)
    if reason is not None:
        release_fields: dict[str, Any] = {
            "release": _text(body, "release"),
            "current_release": _text(body, "current_release"),
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
        413: errors.PayloadTooLargeError,
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
