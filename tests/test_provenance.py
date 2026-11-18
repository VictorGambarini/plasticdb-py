from __future__ import annotations

import logging

import httpx
import pytest
import respx

import plasticdb
from tests.conftest import PROTEINS

LIVE = {"X-PlasticDB-Release": "2026.35", "X-PlasticDB-Release-Source": "live"}


def test_unpinned_client_logs_release_once(
    api: respx.MockRouter, client: plasticdb.Client, caplog: pytest.LogCaptureFixture
) -> None:
    api.get("/proteins").respond(json={"total": 0, "items": []}, headers=LIVE)
    with caplog.at_level(logging.INFO, logger="plasticdb"):
        client._request(PROTEINS)
        client._request(PROTEINS)
    messages = [r.getMessage() for r in caplog.records if "unpinned" in r.getMessage()]
    assert len(messages) == 1
    assert "2026.35" in messages[0]
    assert "release='2026.35'" in messages[0]


def test_unpinned_client_without_release_header(
    api: respx.MockRouter, client: plasticdb.Client, caplog: pytest.LogCaptureFixture
) -> None:
    api.get("/proteins").respond(json={"total": 0, "items": []})
    with caplog.at_level(logging.INFO, logger="plasticdb"):
        client._request(PROTEINS)
    assert "named no dataset release" in caplog.text


def test_pinned_client_does_not_log(
    api: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    api.get("/proteins").respond(json={"total": 0, "items": []}, headers=LIVE)
    with (
        caplog.at_level(logging.INFO, logger="plasticdb"),
        plasticdb.Client(release="2026.35") as c,
    ):
        c._request(PROTEINS)
    assert "unpinned" not in caplog.text


def test_provenance_unpinned(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/current").respond(json={"version": "2026.35"}, headers=LIVE)
    health = api.route(method="GET", url="https://v2.plasticdb.org/health").respond(
        json={"status": "ok", "version": "2.85.0"}
    )
    prov = client.provenance()
    assert prov.release == "2026.35"
    assert prov.release_source == "live"
    assert not prov.pinned
    assert prov.server_version == "2.85.0"
    assert prov.sdk_version == plasticdb.__version__
    assert prov.base_url == "https://v2.plasticdb.org/api/v1"
    assert prov.to_dict()["data_license"] == "CC BY 4.0"
    client.provenance()
    assert health.call_count == 1


def test_provenance_pinned_uses_last_read(api: respx.MockRouter) -> None:
    api.get("/proteins").respond(
        json={"total": 0, "items": []},
        headers={"X-PlasticDB-Release": "2026.30", "X-PlasticDB-Release-Source": "snapshot"},
    )
    api.route(method="GET", url="https://v2.plasticdb.org/health").respond(500)
    with plasticdb.Client(release="2026.30", max_retries=0) as c:
        c._request(PROTEINS)
        prov = c.provenance()
    assert prov.release == "2026.30"
    assert prov.release_source == "snapshot"
    assert prov.pinned
    assert prov.server_version is None


def test_cite_unpinned(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/current").respond(
        json={
            "version": "2026.35",
            "doi": "10.6084/m9.figshare.1",
            "citation": "PlasticDB dataset release 2026.35. University of Auckland. https://doi.org/10.6084/m9.figshare.1",
        }
    )
    citation = client.cite()
    assert citation.release == "2026.35"
    text = str(citation)
    assert "release 2026.35" in text
    assert "https://doi.org/10.6084/m9.figshare.1" in text
    assert "baac008" in text
    assert "CC BY 4.0" in text


def test_cite_pinned_builds_from_release(api: respx.MockRouter) -> None:
    api.get("/releases/2026.30").respond(
        json={
            "version": "2026.30",
            "doi": None,
            "promoted_proteins": 0,
            "promoted_microorganisms": 0,
        }
    )
    with plasticdb.Client(release="2026.30") as c:
        citation = c.cite()
    assert citation.dataset == "PlasticDB dataset release 2026.30. University of Auckland."
    assert "CC BY 4.0" in str(citation)


def test_cite_before_first_release(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/current").respond(json={"version": None})
    citation = client.cite()
    assert citation.release is None
    assert "no dataset release named" in citation.dataset
    assert "CC BY 4.0" in str(citation)


def test_cite_falls_back_when_offline(api: respx.MockRouter) -> None:
    api.get("/releases/current").side_effect = httpx.ConnectError("offline")
    with plasticdb.Client(max_retries=0) as c:
        citation = c.cite()
    assert "baac008" in str(citation)
    assert "CC BY 4.0" in str(citation)


def test_cite_pinned_unknown_release_raises(api: respx.MockRouter) -> None:
    api.get("/releases/1999.01").respond(404, json={"detail": "No release 1999.01"})
    with plasticdb.Client(release="1999.01") as c, pytest.raises(plasticdb.NotFoundError):
        c.cite()
