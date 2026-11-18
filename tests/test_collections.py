from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import pytest
import respx

import plasticdb
from plasticdb import errors
from plasticdb._fasta import parse_fasta
from tests.conftest import fixture, reply, snapshot

FASTA = (
    ">PBFCTW | PETase | Pseudideonella sakaiensis | 2016 | PET | kind=wild_type\n"
    "MNFPRASRLMQAAVLGG\n"
    ">PB6695 | other | x | 2020 | PET | kind=wild_type\n"
    "MKKLLA\n"
    ">PBDTZX | Dm-TA-ΔLCC | uncultured bacterium | 2027 | PET | kind=engineered | engineered\n"
    "MSNPYQRGPNP\n"
)


def _protein(i: int) -> dict[str, Any]:
    return {"id": i, "name": f"P{i}", "accession": f"PB{i:04d}", "kind": "wild_type"}


def _pager(total: int, **headers: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        items = [_protein(i) for i in range(offset, min(offset + limit, total))]
        return httpx.Response(200, json={"total": total, "items": items}, headers=headers)

    return handler


# ── flattening ───────────────────────────────────────────────────────────────


def test_rows_flatten_nested_objects(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    rows = client.proteins.list().rows()
    first = rows[0]
    assert first["source_microorganism.name"] == "uncultured bacterium"
    assert first["source_microorganism.tax_id"] == 77133
    assert first["paper.doi"] == "10.1016/j.ibmb.2026.104683"
    assert first["plastics_positive"] == ["PET"]
    assert "degradation_links" not in first and "papers" not in first
    assert "source_microorganism.degradation_links" not in first
    assert list(rows[0]) == list(rows[1])


def test_null_nested_object_does_not_add_a_column(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    body = fixture("proteins")["body"]
    body["items"][1]["source_microorganism"] = None
    api.get("/proteins").respond(json=body)
    rows = client.proteins.list().rows()
    assert "source_microorganism" not in rows[1]
    assert rows[1]["source_microorganism.name"] is None


def test_to_pandas_and_polars(api: respx.MockRouter, client: plasticdb.Client) -> None:
    pd = pytest.importorskip("pandas")
    pl = pytest.importorskip("polars")
    api.get("/microorganisms").mock(return_value=reply("microorganisms"))
    frame = client.microorganisms.list().to_pandas()
    assert isinstance(frame, pd.DataFrame)
    assert {"name", "tax_id", "paper.doi", "plastics_positive"} <= set(frame.columns)
    assert frame.loc[0, "plastics_positive"] == ["PHB", "PLA Blend"]
    polars_frame = client.microorganisms.list().to_polars()
    assert isinstance(polars_frame, pl.DataFrame)
    assert polars_frame.height == len(frame)
    assert set(polars_frame.columns) == set(frame.columns)


def test_records_keep_full_nesting(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    record = client.proteins.list().records()[0]
    assert record["degradation_links"][0]["plastic"]["name"] == "PET"


def test_interaction_levels(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBFCTW/interactions").mock(return_value=reply("protein_interactions"))
    interactions = client.evidence.interactions("PBFCTW")
    base = interactions.rows()
    per_experiment = interactions.rows("experiment")
    per_observation = interactions.rows("observation")
    assert len(base) == 2
    assert len(per_experiment) >= len(base)
    assert len(per_observation) >= len(per_experiment)
    row = per_observation[0]
    assert row["plastic.name"] and row["experiment.method"]
    assert "observation.value" in row and "observation.unit" in row
    assert base[0]["experiment_count"] == len(interactions.all()[0].experiments)
    with pytest.raises(ValueError):
        interactions.rows("paper")


def test_interaction_without_experiments_still_gives_a_row(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    item = {**fixture("protein_interactions")["body"]["items"][0], "experiments": []}
    api.get("/proteins/PB1/interactions").respond(json={"total": 1, "items": [item]})
    rows = client.evidence.interactions("PB1").rows("observation")
    assert len(rows) == 1 and rows[0]["id"] == item["id"]


def test_interactions_to_pandas_levels(api: respx.MockRouter, client: plasticdb.Client) -> None:
    pytest.importorskip("pandas")
    api.get("/proteins/PBFCTW/interactions").mock(return_value=reply("protein_interactions"))
    frame = client.evidence.interactions("PBFCTW").to_pandas(level="observation")
    assert "observation.measurement_type" in frame.columns


# ── sequences ────────────────────────────────────────────────────────────────


def test_protein_fasta_comes_from_the_download(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    listing = api.get("/proteins").mock(return_value=reply("proteins"))
    download = api.get("/downloads/proteins.fasta").respond(
        text=FASTA, headers={"content-type": "text/plain; charset=utf-8"}
    )
    proteins = client.proteins.list(plastic="PET", sort_by="name", updated_since="2026-01-01")
    fasta = proteins.to_fasta()
    records = parse_fasta(fasta)
    # Only the listed records (PBDTZX, PB6695), in list order, with the list's names.
    assert [r[0] for r in records] == ["PBDTZX", "PB6695"]
    assert records[0][1] == "PBDTZX Dm-TA-ΔLCC"
    assert records[0][2] == "MSNPYQRGPNP"
    params = download.calls.last.request.url.params
    assert params["plastic"] == "PET"
    assert "sort_by" not in params and "updated_since" not in params and "limit" not in params
    assert listing.call_count == 1


def test_pinned_protein_fasta_downloads_the_release_unfiltered(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("proteins", **snapshot()))
    wrapped = ">PBDTZX Dm-TA-ΔLCC\nMSNPY\nQRGPNP\n>PB6695 other\nMKKLLA\n"
    download = api.get("/downloads/proteins.fasta").respond(
        text=wrapped, headers={"content-type": "text/plain", **snapshot()}
    )
    with plasticdb.Client(release="2026.30") as c:
        fasta = c.proteins.list(plastic="PET").to_fasta()
    assert dict(download.calls.last.request.url.params) == {"release": "2026.30"}
    assert parse_fasta(fasta)[0][2] == "MSNPYQRGPNP"


def test_to_biopython(api: respx.MockRouter, client: plasticdb.Client) -> None:
    pytest.importorskip("Bio")
    api.get("/proteins").mock(return_value=reply("proteins"))
    api.get("/downloads/proteins.fasta").respond(text=FASTA, headers={"content-type": "text/plain"})
    records = client.proteins.list().to_biopython()
    assert [r.id for r in records] == ["PBDTZX", "PB6695"]
    assert str(records[1].seq) == "MKKLLA"
    assert records[0].description == "Dm-TA-ΔLCC"
    assert records[0].annotations["molecule_type"] == "protein"


def test_to_fasta_writes_a_file(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Any
) -> None:
    api.get("/constructs").mock(return_value=reply("constructs"))
    target = tmp_path / "constructs.fasta"
    text = client.evidence.constructs(protein="PBFCTW").to_fasta(target)
    assert target.read_text() == text
    assert text.startswith(">construct_357 Original deposited parent reference (6EQE)\n")


def test_fasta_on_records_without_sequences(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/papers").mock(return_value=reply("papers"))
    with pytest.raises(TypeError, match="no sequences"):
        client.papers.list().to_fasta()


def test_parse_fasta_ids() -> None:
    text = ">A | x\nMK\n>B desc\nM\nK\n\n>C\n"
    assert parse_fasta(text) == [("A", "A | x", "MK"), ("B", "B desc", "MK"), ("C", "C", "")]


# ── bulk pulls ───────────────────────────────────────────────────────────────


def test_all_fetches_large_pages_in_parallel(api: respx.MockRouter) -> None:
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()
    inner = _pager(2100)

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        response: httpx.Response = inner(request)
        return response

    route = api.get("/proteins").mock(side_effect=handler)
    with plasticdb.Client(max_concurrency=3) as c:
        items = c.proteins.list().all()
    assert [p.id for p in items] == list(range(2100))
    limits = sorted(int(call.request.url.params["limit"]) for call in route.calls)
    assert limits == [100, 500, 500, 500, 500]
    assert route.call_count == 5
    assert 1 < active["peak"] <= 3


def test_all_respects_limit(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins").mock(side_effect=_pager(5000))
    items = client.proteins.list(limit=1234).all()
    assert len(items) == 1234 and items[-1].id == 1233
    assert sum(int(c.request.url.params["limit"]) for c in route.calls) == 1234


def test_all_goes_sequential_when_the_rate_limit_is_low(api: respx.MockRouter) -> None:
    concurrent = {"now": 0, "peak": 0}
    inner = _pager(1600, **{"X-RateLimit-Remaining": "3"})

    def handler(request: httpx.Request) -> httpx.Response:
        concurrent["now"] += 1
        concurrent["peak"] = max(concurrent["peak"], concurrent["now"])
        time.sleep(0.01)
        concurrent["now"] -= 1
        response: httpx.Response = inner(request)
        return response

    api.get("/proteins").mock(side_effect=handler)
    with plasticdb.Client(max_concurrency=4) as c:
        assert len(c.proteins.list().all()) == 1600
    assert concurrent["peak"] == 1


def test_bulk_pull_retries_after_429(api: respx.MockRouter, sleeps: list[float]) -> None:
    inner = _pager(700)
    state = {"limited": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["offset"] == "500" and not state["limited"]:
            state["limited"] = True
            return httpx.Response(429, json={"detail": "Rate limit"}, headers={"Retry-After": "7"})
        response: httpx.Response = inner(request)
        return response

    api.get("/proteins").mock(side_effect=handler)
    with plasticdb.Client() as c:
        assert len(c.proteins.list().all()) == 700
    assert 7.0 in sleeps


def test_all_after_iteration_reuses_the_first_page(api: respx.MockRouter) -> None:
    route = api.get("/proteins").mock(side_effect=_pager(150))
    with plasticdb.Client(max_concurrency=1) as c:
        proteins = c.proteins.list(page_size=50)
        assert len(proteins) == 150
        assert len(proteins.all()) == 150
    assert [c.request.url.params["limit"] for c in route.calls] == ["50", "100"]


def test_total_changing_mid_pull_finishes_sequentially(api: respx.MockRouter) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        total = 1000 if calls["n"] == 1 else 1100
        response: httpx.Response = _pager(total)(request)
        return response

    api.get("/proteins").mock(side_effect=handler)
    with plasticdb.Client(max_concurrency=1) as c:
        assert len(c.proteins.list().all()) == 1100


def test_max_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError):
        plasticdb.Client(max_concurrency=0)


# ── other collections ────────────────────────────────────────────────────────


def test_changes_feed(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/changes").mock(return_value=reply("changes"))
    feed = client.changes("2026-11-10T00:00:00Z", entity_type="protein_variant")
    changes = feed.all()
    assert changes[1].entity_type == "protein_variant" and changes[1].status == "updated"
    assert feed.as_of == "2026-11-18T12:26:26.380062+00:00"
    assert feed.since == "2026-11-10T00:00:00+00:00"
    params = route.calls.last.request.url.params
    assert params["since"] == "2026-11-10T00:00:00Z"
    assert params["entity_type"] == "protein_variant"
    assert params["limit"] == "5000"


def test_changes_page_size_bound(client: plasticdb.Client) -> None:
    client.changes("2026-01-01", page_size=5000)
    with pytest.raises(ValueError):
        client.changes("2026-01-01", page_size=5001)


def test_search(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/search").mock(return_value=reply("search"))
    hits = client.search("PETase", limit=2).all()
    assert hits[0].entity_type == "paper" and hits[0].route_key == "1199"
    assert route.calls.last.request.url.params["q"] == "PETase"
    api.get("/search/grouped").mock(return_value=reply("search_grouped"))
    grouped = client.search.grouped("PETase", limit=2)
    assert grouped.groups["protein"].items[0].route_key == "PBFCTW"
    api.get("/search/autocomplete").mock(return_value=reply("autocomplete"))
    suggestions = client.search.autocomplete("PETa")
    assert suggestions["papers"][0].route_key == "904"
    assert suggestions["proteins"][0].entity_type == "proteins"


def test_pathways(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/pathways").mock(return_value=reply("pathways"))
    assert client.pathways.list().first() is not None
    api.get("/pathways/1").mock(return_value=reply("pathway"))
    pathway = client.pathways.get(1)
    assert pathway.steps and pathway.steps[0].step_order == 1
    api.get("/pathways/by-plastic/16").mock(return_value=reply("pathways_by_plastic"))
    assert client.pathways.for_plastic(16)[0].plastic_id == 16
    route = api.get("/pathways/by-plastic/16/graph").mock(return_value=reply("pathway_graph"))
    graph = client.pathways.graph(16, pathway_id=1)
    assert graph.nodes and graph.edges
    assert route.calls.last.request.url.params["pathway_id"] == "1"


def test_stats(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/stats").mock(return_value=reply("stats"))
    assert client.stats.summary().proteins == 3835
    api.get("/stats/protein-activity").mock(return_value=reply("stats_activity"))
    assert client.stats.protein_activity().wild_type.degrader == 853
    api.get("/stats/charts").respond(json={"by_year": []})
    assert client.stats.charts() == {"by_year": []}
    api.get("/stats/by-country").respond(json={"microorganisms": []})
    assert client.stats.by_country() == {"microorganisms": []}


def test_evidence_tables(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/binding-assays").mock(return_value=reply("binding_assays"))
    assert client.evidence.binding_assays(protein="PBFCTW", plastic="PET").all() == []
    assert route.calls.last.request.url.params["plastic"] == "PET"
    api.get("/preparations").mock(return_value=reply("preparations"))
    prep = client.evidence.preparations(doi="10.1").first()
    assert prep is not None and prep.expression_host == "Escherichia coli T7 Express"
    assert prep.modifications[0]["kind"] == "fluorescent_label"
    api.get("/preparations/127").respond(json=fixture("preparations")["body"]["items"][0])
    assert client.evidence.preparation(127).id == 127
    api.get("/constructs/357").respond(json=fixture("constructs")["body"]["items"][0])
    assert client.evidence.construct(357).sequence_role == "reference"
    api.get("/constructs/357/structures").respond(json=[])
    assert client.evidence.construct_structures(357) == []
    api.get("/experiments/9/preparations").respond(json=[{"id": 1}])
    assert client.evidence.experiment_preparations(9)[0].id == 1
    measurement = {"id": 1, "binding_assay_id": 4, "kind": "kd", "qualifier": "=", "value": 2.0}
    assay = {"id": 4, "experiment_id": 1, "plastic_id": 16, "protein_id": 3, "measurements": []}
    api.get("/binding-assays/4").respond(json={**assay, "measurements": [measurement]})
    assert client.evidence.binding_assay(4).measurements[0].value == 2.0
    with pytest.raises(TypeError):
        client.evidence.constructs(plastic="PET")  # type: ignore[call-arg]


def test_kinetics_and_properties(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBFCTW/kinetics").mock(return_value=reply("protein_kinetics"))
    api.get("/proteins/PBFCTW/properties").mock(return_value=reply("protein_properties"))
    assert client.evidence.kinetics("PBFCTW").all()
    assert client.evidence.properties("PBFCTW").rows()


def test_collection_surfaces_errors_lazily(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBGQPC/kinetics").mock(return_value=reply("protein_merged_kinetics"))
    kinetics = client.proteins.kinetics("PBGQPC")
    with pytest.raises(errors.MergedError) as info:
        kinetics.all()
    assert info.value.merged_into == "PBMW9H"
    assert info.value.location == "/api/v1/proteins/PBMW9H/kinetics"
