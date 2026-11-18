"""A pinned client against the data endpoints: release sent, refusals typed, answers cached."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import respx

import plasticdb
from plasticdb import errors
from tests.conftest import reply, snapshot

LIVE_ONLY = {
    "detail": "/api/v1/search is computed live and cannot answer for past release 2026.30.",
    "release": "2026.30",
    "current_release": "2026.35",
    "reason": "live_only",
}


def _unsupported(name: str) -> dict[str, Any]:
    return {
        "detail": [
            {
                "type": "release_unsupported",
                "loc": ["query", name],
                "msg": f"Release 2026.30 cannot answer `{name}`",
                "input": "x",
            }
        ]
    }


@pytest.fixture
def pinned(api: respx.MockRouter) -> Any:
    with plasticdb.Client(release="2026.30") as c:
        yield c


PINNED_READS: list[tuple[str, str, Callable[[plasticdb.Client], Any]]] = [
    ("/proteins", "proteins", lambda c: c.proteins.list().first()),
    ("/proteins/PBFCTW", "protein", lambda c: c.proteins.get("PBFCTW")),
    (
        "/proteins/PBFCTW/interactions",
        "protein_interactions",
        lambda c: c.proteins.interactions("PBFCTW").first(),
    ),
    (
        "/proteins/PBFCTW/kinetics",
        "protein_kinetics",
        lambda c: c.proteins.kinetics("PBFCTW").all(),
    ),
    (
        "/proteins/PBFCTW/properties",
        "protein_properties",
        lambda c: c.proteins.properties("PBFCTW").all(),
    ),
    (
        "/proteins/PBFCTW/variants",
        "protein_variants",
        lambda c: c.proteins.variants("PBFCTW").all(),
    ),
    ("/microorganisms", "microorganisms", lambda c: c.microorganisms.list().all()),
    ("/microorganisms/PBCZU2", "microorganism", lambda c: c.microorganisms.get("PBCZU2")),
    ("/plastics", "plastics", lambda c: c.plastics.list().all()),
    ("/plastics/16", "plastic", lambda c: c.plastics.get(16)),
    ("/papers", "papers", lambda c: c.papers.list().all()),
    ("/papers/1262", "paper", lambda c: c.papers.get(1262)),
    ("/pathways", "pathways", lambda c: c.pathways.list().all()),
    ("/pathways/1", "pathway", lambda c: c.pathways.get(1)),
    ("/pathways/by-plastic/16", "pathways_by_plastic", lambda c: c.pathways.for_plastic(16)),
    ("/pathways/by-plastic/16/graph", "pathway_graph", lambda c: c.pathways.graph(16)),
    ("/binding-assays", "binding_assays", lambda c: c.evidence.binding_assays().all()),
    ("/constructs", "constructs", lambda c: c.evidence.constructs().all()),
    ("/preparations", "preparations", lambda c: c.evidence.preparations().all()),
    ("/variants/PBDTZX", "variant", lambda c: c.variants.get("PBDTZX")),
    ("/variants/PBDTZX/fasta", "fasta_one", lambda c: c.variants.fasta("PBDTZX")),
    ("/proteins/PBFCTW/fasta", "fasta_one", lambda c: c.proteins.fasta("PBFCTW")),
]


@pytest.mark.parametrize(("path", "name", "call"), PINNED_READS, ids=[p[0] for p in PINNED_READS])
def test_pinned_reads_send_the_release_and_are_cached(
    api: respx.MockRouter,
    tmp_path: Path,
    path: str,
    name: str,
    call: Callable[[plasticdb.Client], Any],
) -> None:
    route = api.get(path).mock(return_value=reply(name, **snapshot()))
    with plasticdb.Client(release="2026.30", cache_dir=tmp_path) as c:
        call(c)
        call(c)
        assert c.last_response is not None and c.last_response.from_cache
    assert route.call_count == 1
    assert route.calls.last.request.url.params["release"] == "2026.30"


LIVE_ONLY_CALLS: list[tuple[str, Callable[[plasticdb.Client], Any]]] = [
    ("/search", lambda c: c.search("PET").all()),
    ("/search/grouped", lambda c: c.search.grouped("PET")),
    ("/search/autocomplete", lambda c: c.search.autocomplete("PET")),
    ("/stats", lambda c: c.stats.summary()),
    ("/stats/protein-activity", lambda c: c.stats.protein_activity()),
    ("/changes", lambda c: c.changes("2026-01-01").all()),
    ("/variants/PBDTZX/structure", lambda c: c.variants.structure("PBDTZX")),
    ("/proteins/PBFCTW/structure", lambda c: c.proteins.structure("PBFCTW")),
    ("/plastics/names", lambda c: c.plastics.names()),
    ("/microorganisms/names", lambda c: c.microorganisms.names()),
    ("/papers/1/footprint", lambda c: c.papers.footprint(1)),
    ("/engineering/proteins/PBFCTW", lambda c: c.proteins.lineage("PBFCTW")),
]


@pytest.mark.parametrize(("path", "call"), LIVE_ONLY_CALLS, ids=[p[0] for p in LIVE_ONLY_CALLS])
def test_live_only_endpoints_refuse_a_past_release(
    api: respx.MockRouter, pinned: plasticdb.Client, path: str, call: Callable[..., Any]
) -> None:
    route = api.get(path).respond(409, json=LIVE_ONLY)
    with pytest.raises(errors.LiveOnlyError) as info:
        call(pinned)
    assert info.value.release == "2026.30"
    assert info.value.current_release == "2026.35"
    assert isinstance(info.value, errors.ConflictError)
    assert route.calls.last.request.url.params["release"] == "2026.30"


def test_pinned_variant_missing_from_the_release(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    api.get("/variants/PBNEW1").respond(404, json={"detail": "Variant not in release 2026.30"})
    with pytest.raises(errors.NotFoundError):
        pinned.variants.get("PBNEW1", follow_merges=True)


def test_pinned_variant_names_the_fields_it_cannot_fill(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    headers = {**snapshot(), "X-PlasticDB-Snapshot-Unavailable": "structures, identity_sources"}
    api.get("/variants/PBDTZX").mock(return_value=reply("variant", **headers))
    pinned.variants.get("PBDTZX")
    assert pinned.last_response is not None
    assert pinned.last_response.unavailable_fields == ("structures", "identity_sources")


def test_pinned_variant_rejects_sequenceless(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    api.get("/variants/PBDTZX").respond(422, json=_unsupported("include_sequenceless"))
    with pytest.raises(errors.UnsupportedFilterError) as info:
        pinned.variants.get("PBDTZX", include_sequenceless=True)
    assert info.value.parameters == ["include_sequenceless"]


def test_unsupported_filter_names_the_parameter(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    api.get("/proteins").respond(422, json=_unsupported("q"))
    with pytest.raises(errors.UnsupportedFilterError) as info:
        pinned.proteins.list(q="cutinase").all()
    assert info.value.parameters == ["q"]
    assert isinstance(info.value, errors.ValidationError)


def test_unknown_release(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("unknown_release"))
    with plasticdb.Client(release="2026.99") as c, pytest.raises(errors.UnknownReleaseError):
        c.proteins.list().first()


def test_unavailable_fields_are_reported(api: respx.MockRouter, pinned: plasticdb.Client) -> None:
    api.get("/proteins").mock(
        return_value=reply(
            "proteins",
            **snapshot(),
            **{"X-PlasticDB-Snapshot-Unavailable": "updated_at, source_microorganism.updated_at"},
        )
    )
    proteins = pinned.proteins.list()
    assert proteins.response.unavailable_fields == ("updated_at", "source_microorganism.updated_at")
    assert proteins.release == "2026.30"


def test_merged_record_on_a_snapshot_is_not_found(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    # A release never publishes a merged record, so a pinned read answers 404, not 301.
    api.get("/proteins/PBGQPC").respond(404, json={"detail": "Protein not found"})
    with pytest.raises(errors.NotFoundError):
        pinned.proteins.get("PBGQPC", follow_merges=True)


def test_pinned_variant_accession_on_proteins(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    detail = (
        "PBDTZX is an engineered variant; variant records are not answered from a past "
        "release. List it with /proteins?kind=engineered&release=…"
    )
    api.get("/proteins/PBDTZX").respond(404, json={"detail": detail})
    with pytest.raises(errors.NotFoundError, match="engineered variant"):
        pinned.proteins.get("PBDTZX")


def test_pinned_to_the_current_release_is_answered_live_and_not_cached(
    api: respx.MockRouter, tmp_path: Path
) -> None:
    live = {"X-PlasticDB-Release": "2026.35", "X-PlasticDB-Release-Source": "live"}
    route = api.get("/plastics/16").mock(return_value=reply("plastic", **live))
    with plasticdb.Client(release="2026.35", cache_dir=tmp_path) as c:
        c.plastics.get(16)
        c.plastics.get(16)
    assert route.call_count == 2
