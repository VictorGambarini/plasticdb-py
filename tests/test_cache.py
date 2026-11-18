from __future__ import annotations

from pathlib import Path

import pytest
import respx

import plasticdb
from tests.conftest import PROTEINS

BODY = {"total": 1, "items": [{"accession": "PDB000001"}]}


def _snapshot(release: str = "2026.30") -> dict[str, str]:
    return {"X-PlasticDB-Release": release, "X-PlasticDB-Release-Source": "snapshot"}


def test_pinned_snapshot_answers_are_cached(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        first = c._request(PROTEINS, params={"plastic": "PET", "limit": 1})
        second = c._request(PROTEINS, params={"limit": 1, "plastic": "PET"})
    assert route.call_count == 1
    assert not first.from_cache
    assert second.from_cache
    assert second.data == BODY
    assert second.release == "2026.30" and second.pinned


def test_different_queries_are_different_entries(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        c._request(PROTEINS, params={"plastic": "PET"})
        c._request(PROTEINS, params={"plastic": "PE"})
    assert route.call_count == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"X-PlasticDB-Release": "2026.35", "X-PlasticDB-Release-Source": "live"},
        {},
        _snapshot("2026.31"),
    ],
)
def test_live_or_mismatched_answers_are_not_cached(
    api: respx.MockRouter, tmp_path: Path, headers: dict[str, str]
) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=headers)
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        c._request(PROTEINS)
        c._request(PROTEINS)
    assert route.call_count == 2


def test_unpinned_client_never_caches(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client(cache_dir=tmp_path) as c:
        c._request(PROTEINS)
        c._request(PROTEINS)
    assert route.call_count == 2
    assert not any(tmp_path.iterdir())


def test_errors_are_not_cached(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(404, json={"detail": "x"}, headers=_snapshot())
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        for _ in range(2):
            with pytest.raises(plasticdb.NotFoundError):
                c._request(PROTEINS)
    assert route.call_count == 2


def test_corrupt_entry_is_refetched(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        c._request(PROTEINS)
        for body in tmp_path.rglob("*.body"):
            body.write_bytes(b'{"total": 0, "items": []}')
        assert c._request(PROTEINS).data == BODY
    assert route.call_count == 2


def test_clear(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        c._request(PROTEINS)
        assert c.cache is not None
        c.cache.clear("2026.30")
        c._request(PROTEINS)
    assert route.call_count == 2


def test_cached_answer_skips_api_key_check(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/proteins").respond(json=BODY, headers=_snapshot())
    with plasticdb.Client("key-a", release="2026.30", cache_dir=tmp_path) as c:
        c._request(PROTEINS)
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        assert c._request(PROTEINS).from_cache
