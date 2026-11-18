from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx

import plasticdb
from plasticdb._endpoints import Endpoint

BASE = "https://v2.plasticdb.org/api/v1"

# Real contract paths, used to exercise pinnable reads before P2 wraps them.
PROTEINS = Endpoint("GET", "/proteins")
PROTEIN = Endpoint("GET", "/proteins/{accession}")
JOBS_SUBMIT = Endpoint("POST", "/jobs", pinnable=True)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PLASTICDB_API_KEY", "PLASTICDB_RELEASE", "PLASTICDB_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(plasticdb.Client, "_sleep", staticmethod(recorded.append))
    return recorded


@pytest.fixture
def api(sleeps: list[float]) -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(api: respx.MockRouter) -> Iterator[plasticdb.Client]:
    with plasticdb.Client() as c:
        yield c
