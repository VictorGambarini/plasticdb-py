from __future__ import annotations

from typing import Any

import pytest
import respx

import plasticdb
from plasticdb import errors
from tests.conftest import PROTEIN, PROTEINS

RELEASE_CASES = [
    (404, "unknown", errors.UnknownReleaseError, (errors.NotFoundError,)),
    (404, "no_artefacts", errors.ReleaseNoArtefactsError, (errors.NotFoundError,)),
    (
        409,
        "live_only",
        errors.LiveOnlyError,
        (errors.ConflictError, errors.ReleaseUnsupportedError),
    ),
    (503, "not_mounted", errors.ReleaseNotMountedError, (errors.ServerError,)),
]


@pytest.mark.parametrize(("status", "reason", "cls", "also"), RELEASE_CASES)
def test_release_errors(
    api: respx.MockRouter,
    status: int,
    reason: str,
    cls: type[errors.PlasticDBError],
    also: tuple[type[Exception], ...],
) -> None:
    body = {
        "detail": "explained",
        "release": "2026.30",
        "current_release": "2026.35",
        "reason": reason,
    }
    api.get("/proteins").respond(status, json=body)
    with plasticdb.Client(release="2026.30", max_retries=0) as c, pytest.raises(cls) as info:
        c._request(PROTEINS)
    err = info.value
    assert isinstance(err, errors.ReleaseError)
    for parent in also:
        assert isinstance(err, parent)
    assert err.status_code == status
    assert err.reason == reason
    assert err.release == "2026.30"
    assert err.current_release == "2026.35"
    assert "explained" in str(err)


def test_unsupported_filter(api: respx.MockRouter) -> None:
    detail = [
        {
            "type": "release_unsupported",
            "loc": ["query", "q"],
            "msg": "`q` cannot be answered from the snapshot",
            "input": None,
        }
    ]
    api.get("/proteins").respond(422, json={"detail": detail})
    with (
        plasticdb.Client(release="2026.30") as c,
        pytest.raises(errors.UnsupportedFilterError) as info,
    ):
        c._request(PROTEINS, params={"q": "petase"})
    assert isinstance(info.value, errors.ValidationError)
    assert isinstance(info.value, errors.ReleaseUnsupportedError)
    assert info.value.parameters == ["q"]
    assert "query.q" in str(info.value)


SIMPLE_CASES: list[tuple[int, Any, type[errors.PlasticDBError]]] = [
    (400, {"detail": "bad"}, errors.BadRequestError),
    (401, {"detail": "Invalid credentials"}, errors.AuthenticationError),
    (403, {"detail": "Email verification required"}, errors.PermissionDenied),
    (404, {"detail": "Protein not found"}, errors.NotFoundError),
    (409, {"detail": "conflict"}, errors.ConflictError),
    (410, {"detail": "This accession has been deleted"}, errors.GoneError),
    (
        422,
        {"detail": [{"type": "int_parsing", "loc": ["query", "limit"], "msg": "bad"}]},
        errors.ValidationError,
    ),
    (500, {"detail": "Internal Server Error"}, errors.ServerError),
    (502, "Bad gateway", errors.ServerError),
    (503, "The dataset archive is temporarily unavailable", errors.ServiceUnavailableError),
    (418, {"detail": "teapot"}, errors.PlasticDBError),
]


@pytest.mark.parametrize(("status", "body", "cls"), SIMPLE_CASES)
def test_status_mapping(
    api: respx.MockRouter, status: int, body: Any, cls: type[errors.PlasticDBError]
) -> None:
    if isinstance(body, str):
        api.get("/proteins").respond(status, text=body)
    else:
        api.get("/proteins").respond(status, json=body)
    with plasticdb.Client(max_retries=0) as c, pytest.raises(cls) as info:
        c._request(PROTEINS)
    assert type(info.value) is cls
    assert info.value.status_code == status
    assert not isinstance(info.value, errors.ReleaseError)


def test_validation_errors_are_exposed(api: respx.MockRouter, client: plasticdb.Client) -> None:
    detail = [{"type": "int_parsing", "loc": ["query", "limit"], "msg": "not an int"}]
    api.get("/proteins").respond(422, json={"detail": detail})
    with pytest.raises(errors.ValidationError) as info:
        client._request(PROTEINS)
    assert info.value.errors == detail


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (
            {
                "accession": "PDB000123",
                "location": "/api/v1/proteins/PDB000123",
                "kind": "wild_type",
                "reason": "merged",
                "merged_into": "PDB000123",
            },
            "wild_type",
        ),
        (
            {
                "accession": "PB0042",
                "kind": "engineered",
                "location": "/api/v1/variants/PB0042",
                "reason": "merged",
                "merged_into": "PB0042",
            },
            "engineered",
        ),
    ],
)
def test_merged_record(
    api: respx.MockRouter, client: plasticdb.Client, body: dict[str, str], kind: str | None
) -> None:
    api.get("/proteins/PDB000001").respond(301, json=body, headers={"Location": body["location"]})
    with pytest.raises(errors.MergedError) as info:
        client._request(PROTEIN, accession="PDB000001")
    err = info.value
    assert isinstance(err, errors.GoneError)
    assert err.merged_into == body["accession"]
    assert err.location == body["location"]
    assert err.kind == kind
    assert err.status_code == 301


def test_other_redirect_is_unexpected(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").respond(302, headers={"Location": "/elsewhere"})
    with pytest.raises(errors.UnexpectedResponseError, match="/elsewhere"):
        client._request(PROTEINS)


def test_rate_limit_error_carries_retry_after(api: respx.MockRouter) -> None:
    api.get("/proteins").respond(
        429, json={"detail": "Rate limit exceeded: 200 per 1 hour"}, headers={"Retry-After": "1200"}
    )
    with plasticdb.Client() as c, pytest.raises(errors.RateLimitError) as info:
        c._request(PROTEINS)
    assert info.value.retry_after == 1200


def test_error_url(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").respond(404, json={"detail": "nope"})
    with pytest.raises(errors.NotFoundError) as info:
        client._request(PROTEINS)
    assert info.value.url == "https://v2.plasticdb.org/api/v1/proteins"


def test_engineered_variant_redirect(api: respx.MockRouter, client: plasticdb.Client) -> None:
    body = {
        "accession": "PB0042",
        "location": "/api/v1/variants/PB0042",
        "kind": "engineered",
        "reason": "engineered_variant",
    }
    api.get("/proteins/PB0042").respond(301, json=body, headers={"Location": body["location"]})
    with pytest.raises(errors.EngineeredVariantError) as info:
        client._request(PROTEIN, accession="PB0042")
    assert info.value.accession == "PB0042" and info.value.status_code == 301
    assert not isinstance(info.value, errors.MergedError)


REASON_CASES: list[tuple[int, dict[str, Any], type[errors.PlasticDBError], str]] = [
    (
        404,
        {
            "detail": "PB0042 is an engineered variant",
            "accession": "PB0042",
            "kind": "engineered",
            "reason": "engineered_variant",
            "location": "/api/v1/variants/PB0042",
            "merged_into": "PB0043",
        },
        errors.EngineeredVariantError,
        "engineered_variant",
    ),
    (
        410,
        {"detail": "deleted", "accession": "PB0001", "reason": "withdrawn"},
        errors.GoneError,
        "withdrawn",
    ),
    (
        410,
        {"detail": "deleted", "reason": "expired", "results_expired_at": "2027-01-01T00:00:00Z"},
        errors.ResultsExpiredError,
        "expired",
    ),
    (
        422,
        {
            "detail": [
                {"type": "idempotency_key_reused", "loc": ["header", "Idempotency-Key"], "msg": "x"}
            ]
        },
        errors.IdempotencyKeyReusedError,
        "",
    ),
    (503, {"detail": "busy", "reason": "archive_unavailable"}, errors.ServiceUnavailableError, ""),
    (504, {"detail": "slow", "reason": "timeout"}, errors.ServerError, "timeout"),
]


@pytest.mark.parametrize(("status", "body", "cls", "reason"), REASON_CASES)
def test_reason_mapping(
    api: respx.MockRouter,
    status: int,
    body: dict[str, Any],
    cls: type[errors.PlasticDBError],
    reason: str,
) -> None:
    api.get("/proteins").respond(status, json=body)
    with plasticdb.Client(max_retries=0) as c, pytest.raises(cls) as info:
        c._request(PROTEINS)
    assert type(info.value) is cls
    assert info.value.reason == (reason or body.get("reason"))
    if isinstance(info.value, errors.EngineeredVariantError):
        assert info.value.merged_into == "PB0043"
        assert info.value.location == "/api/v1/variants/PB0042"
    if isinstance(info.value, errors.ResultsExpiredError):
        assert info.value.results_expired_at is not None


def test_every_public_error_is_exported() -> None:
    assert set(errors.__all__) <= set(plasticdb.__all__)
    for name in errors.__all__:
        assert getattr(plasticdb, name) is getattr(errors, name)
