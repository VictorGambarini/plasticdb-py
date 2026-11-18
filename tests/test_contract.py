"""Every operation the SDK calls must exist in the vendored SDK contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import plasticdb  # noqa: F401  (registers every endpoint)
from plasticdb._endpoints import REGISTRY, Endpoint

SPEC_PATH = Path(__file__).parent / "contract" / "sdk-v1.json"
PREFIX = "/api/v1"


def _spec() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return data


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _operations() -> set[tuple[str, str]]:
    ops = set()
    for path, item in _spec()["paths"].items():
        for method in item:
            if method in {"get", "post", "put", "patch", "delete", "head"}:
                ops.add((method.upper(), _normalise(path)))
    return ops


def _sdk_endpoints() -> list[Endpoint]:
    import plasticdb.resources  # noqa: F401

    return [e for e in REGISTRY if e.in_contract]


def test_spec_is_the_sdk_contract() -> None:
    spec = _spec()
    assert spec["openapi"].startswith("3.")
    assert "PlasticDBKey" in spec["components"]["securitySchemes"]


@pytest.mark.parametrize("endpoint", _sdk_endpoints(), ids=lambda e: f"{e.method} {e.path}")
def test_endpoint_in_contract(endpoint: Endpoint) -> None:
    assert (endpoint.method, _normalise(PREFIX + endpoint.path)) in _operations()


@pytest.mark.parametrize(
    "endpoint",
    [e for e in _sdk_endpoints() if e.pinnable and e.method == "GET"],
    ids=lambda e: f"{e.method} {e.path}",
)
def test_pinnable_endpoints_accept_release(endpoint: Endpoint) -> None:
    spec = _spec()["paths"]
    target = _normalise(PREFIX + endpoint.path)
    path = next(p for p in spec if _normalise(p) == target)
    names = {p["name"] for p in spec[path][endpoint.method.lower()].get("parameters", [])}
    assert "release" in names


def test_only_known_endpoints_are_outside_the_contract() -> None:
    outside = {(e.method, e.path) for e in REGISTRY if not e.in_contract}
    assert outside == {("GET", "/health")}
