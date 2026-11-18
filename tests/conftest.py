from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
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


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    """A captured live response: ``{"status", "headers", "body"}`` (lists trimmed)."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def reply(name: str, **headers: str) -> httpx.Response:
    """The captured response as an ``httpx.Response`` for a respx route."""
    data = fixture(name)
    merged = {k.lower(): v for k, v in {**data["headers"]}.items()}
    merged.update({k.lower(): v for k, v in headers.items()})
    body = data["body"]
    if isinstance(body, str):
        return httpx.Response(data["status"], text=body, headers=merged)
    return httpx.Response(data["status"], json=body, headers=merged)


def snapshot(release: str = "2026.30") -> dict[str, str]:
    return {"X-PlasticDB-Release": release, "X-PlasticDB-Release-Source": "snapshot"}
