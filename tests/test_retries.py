from __future__ import annotations

import httpx
import pytest
import respx

import plasticdb
from plasticdb import errors
from plasticdb._response import parse_retry_after
from plasticdb.resources.jobs import _SUBMIT as JOBS_SUBMIT
from tests.conftest import PROTEINS

OK = {"total": 0, "items": []}


def test_429_honours_retry_after(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.get("/proteins")
    route.side_effect = [
        httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "7"}),
        httpx.Response(200, json=OK),
    ]
    assert client._request(PROTEINS).data == OK
    assert sleeps == [7.0]
    assert route.call_count == 2


def test_429_longer_than_max_wait_raises_immediately(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.get("/proteins").respond(429, json={"detail": "x"}, headers={"Retry-After": "3600"})
    with pytest.raises(errors.RateLimitError):
        client._request(PROTEINS)
    assert sleeps == []
    assert route.call_count == 1


def test_429_on_post_is_retried(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.post("/jobs")
    route.side_effect = [
        httpx.Response(429, json={"detail": "x"}, headers={"Retry-After": "1"}),
        httpx.Response(202, json={"id": 1}),
    ]
    assert client._request(JOBS_SUBMIT, json={}).data == {"id": 1}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_get_retried_on_5xx(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float], status: int
) -> None:
    route = api.get("/proteins")
    route.side_effect = [
        httpx.Response(status),
        httpx.Response(status),
        httpx.Response(200, json=OK),
    ]
    assert client._request(PROTEINS).data == OK
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]


def test_retries_exhausted(api: respx.MockRouter, sleeps: list[float]) -> None:
    route = api.get("/proteins").respond(502)
    with plasticdb.Client(max_retries=2) as c, pytest.raises(errors.ServerError):
        c._request(PROTEINS)
    assert route.call_count == 3
    assert len(sleeps) == 2


def test_anonymous_post_not_retried_on_5xx(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    """Without a credential the server ignores Idempotency-Key, so a 5xx is final."""
    route = api.post("/jobs").respond(500)
    with pytest.raises(errors.ServerError):
        client._request(JOBS_SUBMIT, json={})
    assert route.call_count == 1


def test_keyed_post_retried_on_5xx_with_one_idempotency_key(
    api: respx.MockRouter, sleeps: list[float]
) -> None:
    route = api.post("/jobs")
    route.side_effect = [httpx.Response(503), httpx.Response(500), httpx.Response(201, json={})]
    with plasticdb.Client(api_key="k") as c:
        c._request(JOBS_SUBMIT, json={}, idempotency_key="abc")
    assert [r.request.headers["idempotency-key"] for r in route.calls] == ["abc"] * 3


def test_keyed_post_read_timeout_is_retried(api: respx.MockRouter, sleeps: list[float]) -> None:
    route = api.post("/jobs")
    route.side_effect = [httpx.ReadTimeout("slow"), httpx.Response(201, json={"id": 3})]
    with plasticdb.Client(api_key="k") as c:
        assert c._request(JOBS_SUBMIT, json={}).data == {"id": 3}
    keys = {r.request.headers["idempotency-key"] for r in route.calls}
    assert len(keys) == 1


def test_get_sends_no_idempotency_key(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins").respond(json=OK)
    client._request(PROTEINS)
    assert "idempotency-key" not in route.calls.last.request.headers


def test_not_mounted_is_not_retried(api: respx.MockRouter, sleeps: list[float]) -> None:
    route = api.get("/proteins").respond(
        503, json={"detail": "x", "release": "2026.30", "reason": "not_mounted"}
    )
    with plasticdb.Client(release="2026.30") as c, pytest.raises(errors.ReleaseNotMountedError):
        c._request(PROTEINS)
    assert route.call_count == 1


def test_connect_error_retried_even_for_post(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.post("/jobs")
    route.side_effect = [httpx.ConnectError("refused"), httpx.Response(202, json={"id": 2})]
    assert client._request(JOBS_SUBMIT, json={}).data == {"id": 2}


def test_read_timeout_retried_for_get_only(
    api: respx.MockRouter, client: plasticdb.Client, sleeps: list[float]
) -> None:
    get = api.get("/proteins")
    get.side_effect = [httpx.ReadTimeout("slow"), httpx.Response(200, json=OK)]
    assert client._request(PROTEINS).data == OK

    post = api.post("/jobs")
    post.side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(errors.APITimeoutError):
        client._request(JOBS_SUBMIT, json={})
    assert post.call_count == 1


def test_connection_failure_after_retries(api: respx.MockRouter, sleeps: list[float]) -> None:
    api.get("/proteins").side_effect = httpx.ConnectError("refused")
    with plasticdb.Client(max_retries=1) as c, pytest.raises(errors.APIConnectionError) as info:
        c._request(PROTEINS)
    assert not isinstance(info.value, errors.APITimeoutError)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("5", 5.0), ("-3", 0.0), ("garbage", None)],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date() -> None:
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
