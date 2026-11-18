from __future__ import annotations

import httpx
import pytest
import respx

import plasticdb
from tests.conftest import BASE, PROTEINS


def test_anonymous_by_default(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/releases/current").respond(json={})
    client.releases.current()
    request = route.calls.last.request
    assert "authorization" not in request.headers
    assert request.headers["user-agent"].startswith(f"plasticdb-py/{plasticdb.__version__} ")
    assert request.headers["accept"] == "application/json"
    assert not client.has_api_key


def test_api_key_argument_is_a_bearer_token(api: respx.MockRouter) -> None:
    route = api.get("/releases/current").respond(json={})
    with plasticdb.Client("secret-key") as c:
        c.releases.current()
        assert "secret-key" not in repr(c)
    assert route.calls.last.request.headers["authorization"] == "Bearer secret-key"


def test_api_key_from_environment(api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLASTICDB_API_KEY", "env-key")
    route = api.get("/releases/current").respond(json={})
    with plasticdb.Client() as c:
        c.releases.current()
    assert route.calls.last.request.headers["authorization"] == "Bearer env-key"


def test_explicit_key_wins_over_environment(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLASTICDB_API_KEY", "env-key")
    route = api.get("/releases/current").respond(json={})
    with plasticdb.Client("arg-key") as c:
        c.releases.current()
    assert route.calls.last.request.headers["authorization"] == "Bearer arg-key"


def test_release_is_sent_on_pinnable_reads_only(api: respx.MockRouter) -> None:
    reads = api.get("/proteins").respond(json={"total": 0, "items": []})
    meta = api.get("/releases/current").respond(json={})
    with plasticdb.Client(release="2026.35") as c:
        assert c.release == "2026.35"
        c._request(PROTEINS)
        c.releases.current()
    assert reads.calls.last.request.url.params["release"] == "2026.35"
    assert "release" not in meta.calls.last.request.url.params


def test_release_from_environment(api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLASTICDB_RELEASE", "2026.40")
    with plasticdb.Client() as c:
        assert c.release == "2026.40"


def test_unpinned_client_sends_no_release(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins").respond(json={"total": 0, "items": []})
    client._request(PROTEINS, params={"plastic": "PET", "include_subtypes": True, "q": None})
    params = route.calls.last.request.url.params
    assert "release" not in params
    assert params["plastic"] == "PET"
    assert params["include_subtypes"] == "true"
    assert "q" not in params


def test_custom_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with respx.mock(base_url="http://localhost:8000/api/v1") as router:
        router.get("/releases/current").respond(json={"version": "2026.01"})
        with plasticdb.Client(base_url="http://localhost:8000/api/v1/") as c:
            assert c.base_url == "http://localhost:8000/api/v1"
            assert c.releases.current().version == "2026.01"


def test_response_headers_are_decoded(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").respond(
        json={"total": 0, "items": []},
        headers={
            "X-PlasticDB-Release": "2026.35",
            "X-PlasticDB-Release-Source": "snapshot",
            "X-PlasticDB-Snapshot-Unavailable": "updated_at, source_microorganism.updated_at",
        },
    )
    result = client._request(PROTEINS)
    assert result.release == "2026.35"
    assert result.pinned
    assert result.unavailable_fields == ("updated_at", "source_microorganism.updated_at")
    assert client.last_response is result


def test_path_parameters_are_quoted(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get(url__regex=r"/releases/.+").respond(
        json={"version": "a/b", "promoted_proteins": 0, "promoted_microorganisms": 0}
    )
    client.releases.get("a/b")
    assert route.calls.last.request.url.raw_path == b"/api/v1/releases/a%2Fb"


def test_empty_path_parameter_is_rejected(client: plasticdb.Client) -> None:
    with pytest.raises(ValueError):
        client.releases.get(" ")


def test_close_closes_transport() -> None:
    c = plasticdb.Client()
    c.close()
    with pytest.raises(RuntimeError):
        c._http.get(BASE)


def test_invalid_json_is_an_unexpected_response(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/releases/current").respond(
        content=b"{not json", headers={"content-type": "application/json"}
    )
    with pytest.raises(plasticdb.errors.UnexpectedResponseError):
        client.releases.current()


def test_negative_retries_rejected() -> None:
    with pytest.raises(ValueError):
        plasticdb.Client(max_retries=-1)


def test_httpx_timeout_object_accepted() -> None:
    with plasticdb.Client(timeout=httpx.Timeout(5.0, read=60.0)) as c:
        assert c._http.timeout.read == 60.0
