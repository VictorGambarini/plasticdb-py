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


def _operation(endpoint: Endpoint) -> dict[str, Any]:
    spec = _spec()["paths"]
    target = _normalise(PREFIX + endpoint.path)
    path = next(p for p in spec if _normalise(p) == target)
    op: dict[str, Any] = spec[path][endpoint.method.lower()]
    return op


@pytest.mark.parametrize(
    "endpoint",
    [e for e in _sdk_endpoints() if e.method == "GET"],
    ids=lambda e: f"{e.method} {e.path}",
)
def test_live_only_matches_the_contract(endpoint: Endpoint) -> None:
    """The server documents a 409 exactly on the operations that refuse a past release."""
    responses = _operation(endpoint).get("responses", {})
    assert endpoint.live_only == ("409" in responses)


@pytest.mark.parametrize(
    "endpoint",
    [e for e in _sdk_endpoints() if e.method == "GET" and not e.pinnable],
    ids=lambda e: f"{e.method} {e.path}",
)
def test_unpinnable_endpoints_take_no_release(endpoint: Endpoint) -> None:
    params = {p["name"]: p for p in _operation(endpoint).get("parameters", [])}
    assert "release" not in params


@pytest.mark.parametrize("endpoint", _sdk_endpoints(), ids=lambda e: f"{e.method} {e.path}")
def test_idempotency_key_exactly_where_the_server_honours_it(endpoint: Endpoint) -> None:
    """The client sends ``Idempotency-Key`` (and so retries the POST) only where it counts."""
    headers = {p["name"] for p in _operation(endpoint).get("parameters", []) if p["in"] == "header"}
    assert endpoint.idempotency == ("Idempotency-Key" in headers)


@pytest.mark.parametrize(
    "endpoint",
    [e for e in _sdk_endpoints() if e.method == "POST"],
    ids=lambda e: f"{e.method} {e.path}",
)
def test_every_post_is_retry_safe_or_idempotent(endpoint: Endpoint) -> None:
    assert endpoint.idempotency != endpoint.retry_safe


def test_wrapped_operations_are_not_deprecated() -> None:
    deprecated = [e.path for e in _sdk_endpoints() if _operation(e).get("deprecated")]
    assert deprecated == []


@pytest.mark.parametrize(
    "endpoint",
    [e for e in _sdk_endpoints() if e.method == "POST"],
    ids=lambda e: f"{e.method} {e.path}",
)
def test_job_submissions_pin_exactly_when_the_server_takes_release(endpoint: Endpoint) -> None:
    """A pinned client sends ``release`` to every submission that accepts one, and no other."""
    names = {p["name"] for p in _operation(endpoint).get("parameters", [])}
    assert endpoint.pinnable == ("release" in names)


# Contract operations the SDK deliberately does not wrap: site plumbing, or
# duplicates of a wrapped call. The base64 submit routes under /bioinformatics
# are deprecated (2.87.0) twins of POST /jobs, which uploads multipart and is
# what the SDK uses. GET /tree/{id} duplicates GET /jobs/{id}, which carries a
# tree job's result_newick since 2.87.0.
NOT_WRAPPED = {
    ("POST", "/api/v1/bioinformatics/annotate-gene"),
    ("POST", "/api/v1/bioinformatics/annotate-genome"),
    ("POST", "/api/v1/bioinformatics/annotate-list"),
    ("POST", "/api/v1/bioinformatics/compare-genomes"),
    ("POST", "/api/v1/bioinformatics/hmm-screen"),
    ("POST", "/api/v1/bioinformatics/pathway-analysis"),
    ("GET", "/api/v1/tree/{}"),
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/compounds"),
    ("GET", "/api/v1/compounds/{}"),
    ("GET", "/api/v1/microorganisms/accession/{}"),
    ("GET", "/api/v1/microorganisms/search"),
    ("GET", "/api/v1/proteins/accession/{}"),
    ("GET", "/api/v1/snapshots"),
    ("GET", "/api/v1/snapshots/latest"),
    ("GET", "/api/v1/snapshots/{}"),
    ("GET", "/api/v1/vocabularies"),
    ("GET", "/api/v1/vocabulary"),
    ("GET", "/api/v1/vocabulary/{}"),
}


def test_every_contract_operation_is_wrapped_or_listed() -> None:
    wrapped = {(e.method, _normalise(PREFIX + e.path)) for e in _sdk_endpoints()}
    missing = {op for op in _operations() if op not in wrapped}
    assert missing == NOT_WRAPPED
    assert not NOT_WRAPPED & wrapped


def test_every_job_operation_is_wrapped() -> None:
    """Jobs, share links, the tree and the sequence search: all wrapped (bar the duplicates)."""
    wrapped = {(e.method, _normalise(PREFIX + e.path)) for e in _sdk_endpoints()}
    jobs = {
        op
        for op in _operations()
        if op[1].startswith(("/api/v1/jobs", "/api/v1/tree", "/api/v1/bioinformatics"))
    }
    assert len(jobs) == 22
    assert jobs - wrapped == {
        op for op in NOT_WRAPPED if op[1].startswith(("/api/v1/bioinformatics/", "/api/v1/tree/"))
    }


def test_the_unwrapped_job_routes_are_the_deprecated_ones() -> None:
    spec = _spec()["paths"]
    for method, path in NOT_WRAPPED:
        if "/bioinformatics/" in path:
            assert spec[path][method.lower()].get("deprecated") is True


def test_only_known_endpoints_are_outside_the_contract() -> None:
    outside = {(e.method, e.path) for e in REGISTRY if not e.in_contract}
    assert outside == {("GET", "/health")}
