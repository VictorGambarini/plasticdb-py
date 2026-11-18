from __future__ import annotations

import respx

import plasticdb
from plasticdb.models import CurrentRelease

RELEASE = {
    "version": "2026.35",
    "released_at": "2026-08-27T06:23:11Z",
    "doi": "10.6084/m9.figshare.1",
    "promoted_proteins": 3,
    "promoted_microorganisms": 1,
    "entity_counts": {"proteins": 10},
    "artefacts": {
        "release": "2026.35",
        "manifest_url": "/api/v1/releases/2026.35/artefacts/MANIFEST.json",
        "files": [
            {
                "path": "tables/proteins.parquet",
                "kind": "table",
                "sha256": "ab" * 32,
                "size": 10,
                "download_url": "/api/v1/releases/2026.35/artefacts/tables/proteins.parquet",
                "table": "proteins",
                "rows": 10,
            }
        ],
        "tools": {"diamond": {"pinned": "2.1.9", "reported": "2.1.9", "status": "ok"}},
        "something_new": True,
    },
}


def test_current_before_first_release(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/current").respond(json={"version": None, "citation": None})
    current = client.releases.current()
    assert current.version is None
    assert current.license == "CC BY 4.0"


def test_current(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/current").respond(
        json={
            "version": "2026.35",
            "doi": "10.1/x",
            "citation": "PlasticDB dataset release 2026.35.",
        }
    )
    assert client.releases.current() == CurrentRelease(
        version="2026.35", doi="10.1/x", citation="PlasticDB dataset release 2026.35."
    )


def test_get_with_artefacts_and_unknown_fields(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/releases/2026.35").respond(json=RELEASE)
    release = client.releases.get("2026.35")
    assert release.artefacts is not None
    assert release.artefacts.files[0].table == "proteins"
    assert release.artefacts.tools["diamond"].pinned == "2.1.9"
    assert release.artefacts.model_extra == {"something_new": True}


def test_get_unknown(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/1999.01").respond(404, json={"detail": "No release 1999.01"})
    try:
        client.releases.get("1999.01")
    except plasticdb.NotFoundError as exc:
        assert "No release 1999.01" in str(exc)
    else:
        raise AssertionError("expected NotFoundError")


def test_status(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases/status").respond(json={"running": False})
    assert client.releases.status().running is False
