"""Server 2.89.0: the flat evidence lists, protein summary reads, include_sequence."""

from __future__ import annotations

import warnings
from typing import Any

import httpx
import pytest
import respx

import plasticdb
from plasticdb._fasta import parse_fasta
from plasticdb.models import ExperimentRecord, InteractionRecord, ObservationRecord, Protein
from tests.conftest import reply

INTERACTION = {
    "id": 7,
    "paper_id": 3,
    "plastic_id": 16,
    "actor_type": "protein",
    "result": "positive",
    "experimental_system": "in_vitro",
    "protein_id": 1,
    "protein_accession": "PBFCTW",
    "credited_microorganism_id": None,
}
EXPERIMENT = {"id": 11, "interaction_id": 7, "paper_id": 3, "method": "hplc", "ph": 7.0}
OBSERVATION = {
    "id": 21,
    "experiment_id": 11,
    "interaction_id": 7,
    "paper_id": 3,
    "measurement_type": "weight_loss",
    "value": 12.5,
    "product_compound_ids": [4, 5],
}


def _envelope(item: dict[str, Any], total: int = 1) -> dict[str, Any]:
    return {"total": total, "items": [item] * total, "offset": 0, "limit": 100}


CASES = [
    ("interactions", "/interactions", INTERACTION, InteractionRecord),
    ("experiments", "/experiments", EXPERIMENT, ExperimentRecord),
    ("observations", "/observations", OBSERVATION, ObservationRecord),
]


@pytest.mark.parametrize(("name", "path", "item", "model"), CASES)
def test_list_and_get(
    api: respx.MockRouter,
    client: plasticdb.Client,
    name: str,
    path: str,
    item: dict[str, Any],
    model: type,
) -> None:
    route = api.get(path).respond(json=_envelope(item))
    namespace = getattr(client.evidence, name)
    records = namespace.list(plastic="PET", include_subtypes=True, has_quantitative=True)
    assert len(records) == 1
    first = records.first()
    assert isinstance(first, model)
    params = route.calls.last.request.url.params
    assert params["plastic"] == "PET" and params["include_subtypes"] == "true"
    assert params["has_quantitative"] == "true"
    assert params["limit"] == "1" or params["limit"] == "100"
    api.get(f"{path}/{item['id']}").respond(json=item)
    assert isinstance(namespace.get(item["id"]), model)


def test_every_filter_is_sent(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/observations").respond(json=_envelope(OBSERVATION, 0))
    client.evidence.observations.list(
        plastic="PET",
        plastic_id=16,
        microorganism="Ideonella sakaiensis",
        protein="PBFCTW",
        evidence_level="direct",
        paper_id=3,
        doi="10.1/x",
        polarity="positive",
        evidence_method="hplc",
        substrate_form="film",
        measurement_type="weight_loss",
        interaction_id=7,
        experiment_id=11,
    ).all()
    params = route.calls.last.request.url.params
    assert params["evidence_level"] == "direct"
    assert params["interaction_id"] == "7" and params["experiment_id"] == "11"
    assert params["measurement_type"] == "weight_loss" and params["doi"] == "10.1/x"


def test_unknown_or_misplaced_filter_is_a_type_error(client: plasticdb.Client) -> None:
    with pytest.raises(TypeError, match="experiment_id"):
        client.evidence.experiments.list(experiment_id=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="plastik"):
        client.evidence.interactions.list(plastik="PET")  # type: ignore[call-arg]


def test_paging_uses_offset_limit_and_the_envelope(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        items = [{**OBSERVATION, "id": i} for i in range(offset, min(offset + limit, 1200))]
        body = {"total": 1200, "items": items, "offset": offset, "limit": limit}
        return httpx.Response(200, json=body)

    route = api.get("/observations").mock(side_effect=handler)
    records = client.evidence.observations.list()
    assert records.total == 1200
    rows = records.all()
    assert [r.id for r in rows] == list(range(1200))
    assert max(int(c.request.url.params["limit"]) for c in route.calls) == 500
    streamed = list(client.evidence.observations.list(page_size=500, limit=600))
    assert len(streamed) == 600


def test_tables(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/observations").respond(json=_envelope(OBSERVATION, 2))
    records = client.evidence.observations.list()
    rows = records.rows()
    assert rows[0]["product_compound_ids"] == [4, 5]
    assert records.records()[0]["measurement_type"] == "weight_loss"
    pd = pytest.importorskip("pandas")
    assert isinstance(records.to_pandas(), pd.DataFrame)


def test_pinned_lists_send_the_release(api: respx.MockRouter) -> None:
    route = api.get("/interactions").respond(json=_envelope(INTERACTION))
    with plasticdb.Client(release="2026.30") as pinned:
        pinned.evidence.interactions.list(plastic="PET").all()
    assert route.calls.last.request.url.params["release"] == "2026.30"


def test_the_per_protein_call_still_works(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW/interactions").mock(
        return_value=reply("protein_interactions")
    )
    assert client.evidence.interactions("PBFCTW", evidence_level="direct").first() is not None
    assert route.calls.last.request.url.params["evidence_level"] == "direct"
    assert "kind" not in route.calls.last.request.url.params


# ── evidence_level / kind ────────────────────────────────────────────────────


def test_kind_is_a_deprecated_alias_and_never_sent(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    route = api.get("/proteins/PBFCTW/interactions").mock(
        return_value=reply("protein_interactions")
    )
    with pytest.warns(DeprecationWarning, match="evidence_level"):
        client.proteins.interactions("PBFCTW", kind="indirect").first()
    params = route.calls.last.request.url.params
    assert params["evidence_level"] == "indirect" and "kind" not in params
    with pytest.warns(DeprecationWarning):
        client.proteins.interactions("PBFCTW", evidence_level="direct", kind="direct").first()
    assert route.calls.last.request.url.params["evidence_level"] == "direct"
    with pytest.raises(ValueError, match="disagree"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client.proteins.interactions("PBFCTW", evidence_level="direct", kind="indirect")


def test_evidence_level_defaults_to_all(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW/interactions").mock(
        return_value=reply("protein_interactions")
    )
    client.proteins.interactions("PBFCTW").first()
    assert route.calls.last.request.url.params["evidence_level"] == "all"


# ── protein reads ────────────────────────────────────────────────────────────

SUMMARY = {
    "id": 1,
    "name": "PETase",
    "accession": "PBFCTW",
    "kind": "wild_type",
    "sequence": "MNFPRASRLMQ",
    "view": "summary",
    "counts": {
        "interactions": 12,
        "indirect_interactions": 3,
        "variants": 40,
        "kinetics": 5,
        "properties": 2,
    },
}


def test_protein_get_defaults_to_the_summary(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    route = api.get("/proteins/PBFCTW").respond(json=SUMMARY)
    protein = client.proteins.get("PBFCTW")
    assert isinstance(protein, Protein)
    assert route.calls.last.request.url.params["view"] == "summary"
    assert protein.view == "summary"
    assert protein.counts["variants"] == 40
    assert protein.interactions == [] and protein.variants == []
    assert protein.to_fasta().startswith(">PBFCTW PETase\nMNFPRASRLMQ")


def test_live_summary_fixture(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBFCTW").mock(return_value=reply("protein_summary"))
    protein = client.proteins.get("PBFCTW")
    assert isinstance(protein, Protein)
    assert protein.view == "summary" and protein.counts["interactions"] > 0
    assert protein.interactions == [] and protein.kinetics == []
    assert not protein.model_extra or "counts" not in protein.model_extra


def test_live_record_fixtures(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/observations").mock(return_value=reply("evidence_observations"))
    rows = client.evidence.observations.list(has_quantitative=True)
    assert rows.total > 3 and rows.first() is not None
    assert all(isinstance(o.product_compound_ids, list) for o in rows.all())
    api.get("/proteins").mock(return_value=reply("proteins_sequence"))
    fasta = client.proteins.list(plastic="PET", kind="wild_type", limit=2).to_fasta()
    assert fasta.startswith(">") and fasta.count(">") == 2


def test_protein_get_full_embeds_the_lists(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW").mock(return_value=reply("protein"))
    protein = client.proteins.get("PBFCTW", view="full")
    assert route.calls.last.request.url.params["view"] == "full"
    assert isinstance(protein, Protein)
    assert protein.interactions and protein.counts == {}


# ── include_sequence ─────────────────────────────────────────────────────────


def _with_sequence(request: httpx.Request) -> httpx.Response:
    assert request.url.params["include_sequence"] == "true"
    body = {
        "total": 2,
        "items": [
            {"id": 1, "name": "PETase", "accession": "PBFCTW", "sequence": "MNFPRASRLMQ"},
            {"id": 2, "name": "other", "accession": "PB6695", "sequence": None},
        ],
    }
    return httpx.Response(200, json=body)


def test_include_sequence_gives_items_a_sequence(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins").mock(side_effect=_with_sequence)
    items = client.proteins.list(include_sequence=True).all()
    assert items[0].sequence == "MNFPRASRLMQ" and items[1].sequence is None
    rows = client.proteins.list(include_sequence=True).rows()
    assert "sequence" in rows[0]


def test_plain_lists_have_no_sequence_column(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    assert "sequence" not in client.proteins.list().rows()[0]


def test_to_fasta_is_one_request_series(api: respx.MockRouter, client: plasticdb.Client) -> None:
    listing = api.get("/proteins").mock(side_effect=_with_sequence)
    download = api.get("/downloads/proteins.fasta")
    fasta = client.proteins.list(plastic="PET").to_fasta()
    assert parse_fasta(fasta) == [("PBFCTW", "PBFCTW PETase", "MNFPRASRLMQ")]
    assert listing.call_count == 1 and not download.called
    assert listing.calls.last.request.url.params["plastic"] == "PET"


def test_pinned_to_fasta_sends_release_and_include_sequence(api: respx.MockRouter) -> None:
    listing = api.get("/proteins").mock(side_effect=_with_sequence)
    download = api.get("/downloads/proteins.fasta")
    with plasticdb.Client(release="2026.30") as pinned:
        pinned.proteins.list().to_fasta()
    assert listing.calls.last.request.url.params["release"] == "2026.30"
    assert not download.called


def test_list_with_sequences_needs_no_second_request(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    listing = api.get("/proteins").mock(side_effect=_with_sequence)
    coll = client.proteins.list(include_sequence=True)
    coll.all()
    assert coll.to_fasta().count(">") == 1
    assert listing.call_count == 1


def test_older_server_without_include_sequence_falls_back_to_the_download(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    api.get("/downloads/proteins.fasta").respond(
        text=">PBDTZX x\nMSNPY\n", headers={"content-type": "text/plain"}
    )
    assert client.proteins.list().to_fasta().startswith(">PBDTZX")
