from __future__ import annotations

import builtins
from typing import Any

import httpx
import pytest
import respx

import plasticdb
from plasticdb import Collection
from plasticdb.models import Release
from tests.conftest import PROTEINS


def _release(n: int) -> dict[str, Any]:
    return {"version": f"2026.{n:02d}", "promoted_proteins": n, "promoted_microorganisms": 0}


def _pager(request: httpx.Request, total: int = 5) -> httpx.Response:
    offset = int(request.url.params["offset"])
    limit = int(request.url.params["limit"])
    items = [_release(i) for i in range(offset, min(offset + limit, total))]
    return httpx.Response(200, json={"total": total, "items": items})


def test_iterates_every_page(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/releases").mock(side_effect=_pager)
    releases = client.releases.list(page_size=2)
    assert route.call_count == 0
    assert [r.version for r in releases] == [f"2026.{i:02d}" for i in range(5)]
    assert route.call_count == 3
    assert len(releases) == 5
    assert releases.total == 5


def test_limit_caps_items_and_requests(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/releases").mock(side_effect=_pager)
    releases = client.releases.list(page_size=2, limit=3)
    assert [r.promoted_proteins for r in releases.all()] == [0, 1, 2]
    assert len(releases) == 3
    assert [c.request.url.params["limit"] for c in route.calls] == ["2", "1"]


def test_first(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/releases").mock(side_effect=_pager)
    first = client.releases.list().first()
    assert first is not None and first.version == "2026.00"
    assert route.calls.last.request.url.params["limit"] == "1"


def test_first_of_empty(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases").respond(json={"total": 0, "items": []})
    releases = client.releases.list()
    assert releases.first() is None
    assert releases.all() == []
    assert len(releases) == 0


def test_bad_envelope(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/releases").respond(json=[1, 2])
    with pytest.raises(plasticdb.errors.UnexpectedResponseError):
        client.releases.list().all()


def test_page_size_bounds(client: plasticdb.Client) -> None:
    with pytest.raises(ValueError):
        client.releases.list(page_size=501)
    with pytest.raises(ValueError):
        client.releases.list(limit=-1)


def test_pinned_release_sent_on_every_page(api: respx.MockRouter) -> None:
    route = api.get("/proteins").mock(side_effect=_pager)
    with plasticdb.Client(release="2026.35") as c:
        Collection(c, PROTEINS, Release, params={"plastic": "PET"}, page_size=2).all()
    for call in route.calls:
        assert call.request.url.params["release"] == "2026.35"
        assert call.request.url.params["plastic"] == "PET"


def test_release_change_mid_iteration_warns(
    api: respx.MockRouter, client: plasticdb.Client, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        response = _pager(request, total=4)
        response.headers["X-PlasticDB-Release"] = "2026.35" if calls["n"] == 1 else "2026.36"
        return response

    api.get("/releases").mock(side_effect=handler)
    client.releases.list(page_size=2).all()
    assert "release changed from 2026.35 to 2026.36" in caplog.text


def test_to_pandas(api: respx.MockRouter, client: plasticdb.Client) -> None:
    pd = pytest.importorskip("pandas")
    api.get("/releases").mock(side_effect=_pager)
    frame = client.releases.list().to_pandas()
    assert isinstance(frame, pd.DataFrame)
    assert list(frame["version"]) == [f"2026.{i:02d}" for i in range(5)]


def _block(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    real_import = builtins.__import__

    def fake_import(module: str, *args: Any, **kwargs: Any) -> Any:
        if module == name or module.startswith(name + "."):
            raise ImportError(f"No module named {module!r}")
        return real_import(module, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(__import__("sys").modules, name, raising=False)


@pytest.mark.parametrize(
    ("method", "module", "extra"),
    [
        ("to_pandas", "pandas", "pandas"),
        ("to_polars", "polars", "polars"),
        ("to_biopython", "Bio", "bio"),
    ],
)
def test_missing_extra_explains_install(
    client: plasticdb.Client, monkeypatch: pytest.MonkeyPatch, method: str, module: str, extra: str
) -> None:
    _block(monkeypatch, module)
    with pytest.raises(ImportError, match=rf"plasticdb\[{extra}\]"):
        getattr(client.releases.list(), method)()


def test_to_fasta_on_non_sequence_records(client: plasticdb.Client) -> None:
    with pytest.raises(TypeError):
        client.releases.list().to_fasta()


def test_repr_does_not_fetch(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/releases").mock(side_effect=_pager)
    assert "not fetched" in repr(client.releases.list())
    assert route.call_count == 0
