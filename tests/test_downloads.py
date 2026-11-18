from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import plasticdb
from plasticdb import errors
from tests.conftest import snapshot

CSV = b"seq_id,name\nPBFCTW,PETase\n"
PARQUET = b"PAR1" + bytes(range(256)) * 64 + b"PAR1"
STRUCTURE = b"\x1f\x8b-fake-gzip-pdb"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv(**headers: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=CSV,
        headers={
            "content-type": "text/csv; charset=utf-8",
            "content-disposition": 'attachment; filename="plasticdb_proteins_2026.30.csv"',
            **headers,
        },
    )


def _manifest() -> bytes:
    return json.dumps(
        {
            "release": "2026.30",
            "files": [
                {"path": "parquet/proteins.parquet", "sha256": _sha(PARQUET), "size": len(PARQUET)},
                {"path": "structures/17.pdb.gz", "sha256": _sha(STRUCTURE), "size": 3},
            ],
        }
    ).encode()


def _release(parquet_sha: str | None = None) -> dict[str, Any]:
    return {
        "version": "2026.30",
        "promoted_proteins": 3,
        "promoted_microorganisms": 1,
        "artefacts": {
            "release": "2026.30",
            "manifest_url": "/api/v1/releases/2026.30/artefacts/MANIFEST.json",
            "manifest_sha256": _sha(_manifest()),
            "files": [
                {
                    "path": "parquet/proteins.parquet",
                    "kind": "table",
                    "table": "proteins",
                    "rows": 3,
                    "size": len(PARQUET),
                    "sha256": parquet_sha or _sha(PARQUET),
                    "download_url": "/api/v1/releases/2026.30/artefacts/parquet/proteins.parquet",
                }
            ],
        },
    }


def _artefacts(api: respx.MockRouter, parquet_sha: str | None = None) -> dict[str, respx.Route]:
    return {
        "release": api.get("/releases/2026.30").respond(json=_release(parquet_sha)),
        "parquet": api.get("/releases/2026.30/artefacts/parquet/proteins.parquet").respond(
            content=PARQUET, headers={"content-type": "application/octet-stream"}
        ),
        "manifest": api.get("/releases/2026.30/artefacts/MANIFEST.json").respond(
            content=_manifest(), headers={"content-type": "application/json"}
        ),
    }


# ── tables ───────────────────────────────────────────────────────────────────


def test_table_into_a_directory_uses_the_server_name(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    route = api.get("/downloads/proteins.csv").mock(return_value=_csv())
    path = client.downloads.proteins(tmp_path, plastic="PET", kind="wild_type")
    assert path == tmp_path / "plasticdb_proteins_2026.30.csv"
    assert path.read_bytes() == CSV
    params = route.calls.last.request.url.params
    assert params["plastic"] == "PET" and params["kind"] == "wild_type"
    assert list(tmp_path.iterdir()) == [path]


def test_table_to_a_file_path(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    api.get("/downloads/microorganisms.json").respond(json=[{"id": 1}])
    target = tmp_path / "out" / "orgs.json"
    assert client.downloads.microorganisms(target, format="json", genus="Bacillus") == target
    assert json.loads(target.read_text()) == [{"id": 1}]


@pytest.mark.parametrize(
    ("method", "path", "fmt"),
    [
        ("plastics", "/downloads/plastics.csv", "csv"),
        ("papers", "/downloads/papers.json", "json"),
        ("protein_variants", "/downloads/protein-variants.csv", "csv"),
        ("proteins", "/downloads/proteins.fasta", "fasta"),
    ],
)
def test_every_table(
    api: respx.MockRouter,
    client: plasticdb.Client,
    tmp_path: Path,
    method: str,
    path: str,
    fmt: str,
) -> None:
    route = api.get(path).respond(content=b"x")
    getattr(client.downloads, method)(tmp_path / f"t.{fmt}", format=fmt)
    assert route.called


def test_wrong_format_or_filter(client: plasticdb.Client, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        client.downloads.plastics(tmp_path, format="fasta")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        client.downloads.plastics(tmp_path, genus="x")  # type: ignore[call-arg]


def test_pinned_download_is_cached(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/downloads/proteins.csv").mock(return_value=_csv(**snapshot()))
    cache = tmp_path / "cache"
    (tmp_path / "b").mkdir()
    with plasticdb.Client(release="2026.30", cache_dir=cache) as c:
        first = c.downloads.proteins(tmp_path / "a.csv")
        second = c.downloads.proteins(tmp_path / "b")
        assert c.last_response is not None and c.last_response.from_cache
    assert route.call_count == 1
    assert route.calls.last.request.url.params["release"] == "2026.30"
    assert first.read_bytes() == second.read_bytes() == CSV
    assert second.name == "plasticdb_proteins_2026.30.csv"


def test_live_download_is_not_cached(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/downloads/proteins.csv").mock(
        return_value=_csv(**{"X-PlasticDB-Release-Source": "live"})
    )
    with plasticdb.Client(cache_dir=tmp_path / "cache") as c:
        c.downloads.proteins(tmp_path / "a.csv")
        c.downloads.proteins(tmp_path / "a.csv")
    assert route.call_count == 2


def test_pinned_download_with_a_filter_is_refused(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/downloads/proteins.csv").respond(
        422,
        json={"detail": [{"type": "release_unsupported", "loc": ["query", "plastic"], "msg": "x"}]},
    )
    with plasticdb.Client(release="2026.30") as c, pytest.raises(errors.UnsupportedFilterError):
        c.downloads.proteins(tmp_path / "p.csv", plastic="PET")
    assert list(tmp_path.iterdir()) == []


def test_interrupted_download_leaves_nothing(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"partial"
            raise httpx.ReadError("connection reset")

    api.get("/downloads/papers.csv").mock(return_value=httpx.Response(200, stream=Broken()))
    with pytest.raises(errors.APIConnectionError):
        client.downloads.papers(tmp_path / "papers.csv")
    assert list(tmp_path.iterdir()) == []


# ── whole dataset ────────────────────────────────────────────────────────────


def test_dataset_zip_being_built(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    api.get("/downloads/dataset.zip").respond(
        503,
        text="The dataset archive is temporarily unavailable: building. Try again in a few "
        "minutes, or use /api/v1/downloads/dataset.json.",
        headers={"Retry-After": "120", "content-type": "text/plain; charset=utf-8"},
    )
    with pytest.raises(errors.ServiceUnavailableError) as info:
        client.downloads.dataset(tmp_path)
    assert info.value.retry_after == 120.0
    assert "temporarily unavailable" in str(info.value)


def test_dataset_zip_waits_when_asked(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path, sleeps: list[float]
) -> None:
    busy = httpx.Response(503, text="building", headers={"Retry-After": "120"})
    ready = httpx.Response(
        200,
        content=b"PK\x03\x04zip",
        headers={"content-disposition": 'attachment; filename="plasticdb_dataset.zip"'},
    )
    api.get("/downloads/dataset.zip").mock(side_effect=[busy, busy, ready])
    path = client.downloads.dataset(tmp_path, max_wait=600)
    assert path.name == "plasticdb_dataset.zip"
    assert sleeps.count(120.0) == 2


def test_dataset_pinned_is_live_only(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/downloads/dataset.json").respond(
        409, json={"detail": "live", "release": "2026.30", "reason": "live_only"}
    )
    with plasticdb.Client(release="2026.30") as c, pytest.raises(errors.LiveOnlyError):
        c.downloads.dataset(tmp_path, format="json", max_wait=600)


def test_search_download(api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path) -> None:
    route = api.get("/downloads/search.json").respond(json={"proteins": []})
    client.downloads.search(tmp_path / "s.json", "PETase")
    assert route.calls.last.request.url.params["q"] == "PETase"


# ── release artefacts ────────────────────────────────────────────────────────


def test_release_file_is_verified(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    routes = _artefacts(api)
    path = client.downloads.release_file("parquet/proteins.parquet", tmp_path, release="2026.30")
    assert path == tmp_path / "proteins.parquet"
    assert path.read_bytes() == PARQUET
    assert "release" not in routes["parquet"].calls.last.request.url.params


def test_checksum_mismatch_keeps_nothing(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    _artefacts(api, parquet_sha="0" * 64)
    with pytest.raises(errors.ChecksumError) as info:
        client.downloads.release_file("parquet/proteins.parquet", tmp_path, release="2026.30")
    assert info.value.expected == "0" * 64
    assert info.value.actual == _sha(PARQUET)
    assert list(tmp_path.iterdir()) == []


def test_artefacts_are_cached_by_release(api: respx.MockRouter, tmp_path: Path) -> None:
    routes = _artefacts(api)
    with plasticdb.Client(cache_dir=tmp_path / "cache") as c:
        for name in ("a", "b"):
            c.downloads.release_file("parquet/proteins.parquet", tmp_path / name, release="2026.30")
    assert routes["parquet"].call_count == 1
    assert (tmp_path / "b").read_bytes() == PARQUET


def test_pinned_client_defaults_to_its_release(api: respx.MockRouter, tmp_path: Path) -> None:
    routes = _artefacts(api)
    with plasticdb.Client(release="2026.30") as c:
        files = c.downloads.artefacts()
        assert files[0].table == "proteins"
        c.downloads.release_file("parquet/proteins.parquet", tmp_path)
    assert routes["release"].called


def test_manifest_is_verified(api: respx.MockRouter, client: plasticdb.Client) -> None:
    _artefacts(api)
    manifest = client.downloads.manifest("2026.30")
    assert manifest["files"][1]["path"] == "structures/17.pdb.gz"


def test_structure_follows_the_redirect_and_is_verified(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    _artefacts(api)
    redirect = api.get("/releases/2026.30/structures/17").respond(
        307, headers={"location": "/api/v1/releases/2026.30/artefacts/structures/17.pdb.gz"}
    )
    api.get("/releases/2026.30/artefacts/structures/17.pdb.gz").respond(
        content=STRUCTURE, headers={"content-type": "application/gzip"}
    )
    path = client.downloads.structure(17, tmp_path, release="2026.30")
    assert redirect.called
    assert path.read_bytes() == STRUCTURE
    assert path.name == "17.pdb.gz"


def test_structure_not_in_the_release(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    _artefacts(api)
    with pytest.raises(errors.NotFoundError):
        client.downloads.structure(99, tmp_path, release="2026.30")


def test_no_release_yet(api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path) -> None:
    api.get("/releases/current").respond(json={"version": None})
    with pytest.raises(errors.ReleaseError, match="no dataset release"):
        client.downloads.release_file("MANIFEST.json", tmp_path)


def test_release_without_artefacts(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    api.get("/releases/2026.01").respond(
        json={"version": "2026.01", "promoted_proteins": 0, "promoted_microorganisms": 0}
    )
    with pytest.raises(errors.ReleaseNoArtefactsError):
        client.downloads.release_file("MANIFEST.json", tmp_path, release="2026.01")
