from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

import plasticdb
from plasticdb import errors
from plasticdb.models import (
    Microorganism,
    MicroorganismSummary,
    Paper,
    PaperSummary,
    Plastic,
    PlasticSummary,
    Protein,
    ProteinSummary,
    Variant,
)
from tests.conftest import fixture, reply


def test_proteins_list_sends_the_filters(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins").mock(return_value=reply("proteins"))
    proteins = client.proteins.list(
        plastic="PET",
        include_subtypes=True,
        polarity="negative",
        kind="wild_type",
        updated_since=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sort_by="name",
        sort_order="asc",
    )
    first = proteins.first()
    assert isinstance(first, ProteinSummary)
    params = route.calls.last.request.url.params
    assert params["plastic"] == "PET"
    assert params["include_subtypes"] == "true"
    assert params["polarity"] == "negative"
    assert params["kind"] == "wild_type"
    assert params["updated_since"] == "2026-11-01T00:00:00+00:00"
    assert params["sort_by"] == "name" and params["sort_order"] == "asc"
    assert "release" not in params


def test_unknown_filter_is_a_type_error(client: plasticdb.Client) -> None:
    with pytest.raises(TypeError, match="plastik"):
        client.proteins.list(plastik="PET")  # type: ignore[call-arg]


def test_proteins_list_items(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    items = client.proteins.list().all()
    assert items[0].accession == "PBDTZX"
    assert items[0].engineered and items[0].parent_accession == "PB6JCU"
    assert items[0].degradation_links[0].plastic.name == "PET"
    assert items[0].source_microorganism is not None
    assert items[0].seq_id == "PBDTZX"


def test_protein_get(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW").mock(return_value=reply("protein"))
    protein = client.proteins.get("PBFCTW", view="full")
    assert isinstance(protein, Protein)
    assert protein.name == "PETase"
    assert protein.sequence and protein.sequence.startswith("MNFPRASRLMQ")
    assert protein.interactions[0].experiments[0].observations
    assert route.calls.last.request.url.params["view"] == "full"
    assert protein.to_fasta().startswith(">PBFCTW PETase\nMNFPRASRLMQ")


def test_protein_get_summary_view(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW").mock(return_value=reply("protein"))
    client.proteins.get("PBFCTW", view="summary", include_sequenceless=True)
    params = route.calls.last.request.url.params
    assert params["view"] == "summary" and params["include_sequenceless"] == "true"


def test_protein_get_on_a_variant_returns_the_variant(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins/PBDTZX").mock(return_value=reply("protein_variant_redirect"))
    api.get("/variants/PBDTZX").mock(return_value=reply("variant"))
    record = client.proteins.get("PBDTZX")
    assert isinstance(record, Variant)
    assert record.variant_name == "Dm-TA-ΔLCC" and record.kind == "engineered"


def test_sub_collection_on_a_variant_raises(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins/PBDTZX/interactions").mock(return_value=reply("protein_variant_subresource"))
    with pytest.raises(errors.EngineeredVariantError) as info:
        client.proteins.interactions("PBDTZX").all()
    assert info.value.accession == "PBDTZX"
    assert info.value.location == "/api/v1/variants/PBDTZX"
    assert info.value.status_code == 404 and info.value.reason == "engineered_variant"
    assert isinstance(info.value, errors.NotFoundError)
    assert info.value.merged_into is None


def test_variant_redirect_is_told_apart_by_reason_not_accession(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    """A protein merged into a variant is a merge, even though kind is ``engineered``."""
    body = {
        "accession": "PBTNBA",
        "location": "/api/v1/variants/PBTNBA",
        "kind": "engineered",
        "reason": "merged",
        "merged_into": "PBTNBA",
    }
    api.get("/proteins/PBTNBA").respond(301, json=body, headers={"location": body["location"]})
    with pytest.raises(errors.MergedError) as info:
        client.proteins.get("PBTNBA")
    assert info.value.kind == "engineered"


def test_protein_fasta_of_a_variant_is_the_variants(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins/PBDTZX/fasta").respond(
        301,
        json={
            **fixture("protein_variant_redirect")["body"],
            "location": "/api/v1/variants/PBDTZX/fasta",
        },
        headers={"location": "/api/v1/variants/PBDTZX/fasta"},
    )
    api.get("/variants/PBDTZX/fasta").respond(text=">PBDTZX\nMKV\n")
    assert client.proteins.fasta("PBDTZX").startswith(">PBDTZX")


def test_protein_by_numeric_id(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/3").mock(return_value=reply("protein"))
    assert client.proteins.get(3).accession == "PBFCTW"
    assert route.called


def test_merged_protein_raises_by_default(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBGQPC").mock(return_value=reply("protein_merged"))
    with pytest.raises(errors.MergedError) as info:
        client.proteins.get("PBGQPC")
    assert info.value.merged_into == "PBMW9H"
    assert info.value.location == "/api/v1/proteins/PBMW9H"
    assert isinstance(info.value, errors.GoneError)


def test_follow_merges_returns_the_survivor_with_a_warning(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/proteins/PBGQPC").mock(return_value=reply("protein_merged"))
    body = {**fixture("protein")["body"], "accession": "PBMW9H"}
    survivor = api.get("/proteins/PBMW9H").respond(json=body)
    with pytest.warns(errors.MergedRecordWarning, match="PBGQPC was merged into PBMW9H"):
        protein = client.proteins.get("PBGQPC", view="summary", follow_merges=True)
    assert protein.accession == "PBMW9H"
    assert survivor.calls.last.request.url.params["view"] == "summary"


def test_follow_merges_into_a_variant(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBG3V3").respond(
        301,
        json={"accession": "PBTNBA", "kind": "engineered", "location": "/api/v1/variants/PBTNBA"},
        headers={"location": "/api/v1/variants/PBTNBA"},
    )
    api.get("/variants/PBTNBA").mock(return_value=reply("variant"))
    with pytest.warns(errors.MergedRecordWarning):
        record = client.proteins.get("PBG3V3", follow_merges=True)
    assert isinstance(record, Variant)


def _merged(accession: str, kind: str = "wild_type") -> dict[str, str]:
    path = "microorganisms" if kind == "microorganism" else "proteins"
    return {
        "accession": accession,
        "location": f"/api/v1/{path}/{accession}",
        "kind": kind,
        "reason": "merged",
        "merged_into": accession,
    }


def test_merge_chain_is_followed(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBAAAA").respond(301, json=_merged("PBBBBB"))
    api.get("/proteins/PBBBBB").respond(301, json=_merged("PBCCCC"))
    api.get("/proteins/PBCCCC").respond(json={**fixture("protein")["body"], "accession": "PBCCCC"})
    with pytest.warns(errors.MergedRecordWarning):
        assert client.proteins.get("PBAAAA", follow_merges=True).accession == "PBCCCC"


def test_merge_loop_stops(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBAAAA").respond(301, json=_merged("PBAAAA"))
    with pytest.warns(errors.MergedRecordWarning), pytest.raises(errors.UnexpectedResponseError):
        client.proteins.get("PBAAAA", follow_merges=True)


def test_withdrawn_protein_is_gone(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBDEAD").respond(
        410,
        json={
            "detail": "This accession has been deleted",
            "accession": "PBDEAD",
            "reason": "withdrawn",
        },
    )
    with pytest.raises(errors.GoneError) as info:
        client.proteins.get("PBDEAD", follow_merges=True)
    assert not isinstance(info.value, errors.MergedError)
    assert info.value.accession == "PBDEAD" and info.value.reason == "withdrawn"


def test_missing_protein(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins/PBZZZZ").mock(return_value=reply("not_found"))
    with pytest.raises(errors.NotFoundError, match="Protein not found"):
        client.proteins.get("PBZZZZ")


def test_variant_get_and_merge(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/variants/PBG3V3").mock(return_value=reply("variant_merged"))
    api.get("/variants/PBTNBA").mock(return_value=reply("variant"))
    with pytest.raises(errors.MergedError) as info:
        client.variants.get("PBG3V3")
    assert info.value.merged_into == "PBTNBA"
    with pytest.warns(errors.MergedRecordWarning):
        variant = client.variants.get("PBG3V3", follow_merges=True)
    assert variant.parent_protein is not None and variant.parent_protein.accession == "PB6JCU"


def test_removed_variant_is_gone(api: respx.MockRouter, client: plasticdb.Client) -> None:
    """A variant whose parent protein was withdrawn (the change feed's ``removed``)."""
    api.get("/variants/PB8B8P").mock(return_value=reply("variant_removed"))
    with pytest.raises(errors.GoneError) as info:
        client.variants.get("PB8B8P")
    assert not isinstance(info.value, errors.NotFoundError)
    assert info.value.accession == "PB8B8P" and info.value.reason == "withdrawn"


def test_variants_list_is_engineered_proteins(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    route = api.get("/proteins").mock(return_value=reply("variants_list"))
    client.variants.list(plastic="PET").first()
    assert route.calls.last.request.url.params["kind"] == "engineered"


def test_protein_variants_carry_sequences(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/proteins/PBFCTW/variants").mock(return_value=reply("protein_variants"))
    variants = client.variants.of("PBFCTW")
    fasta = variants.to_fasta()
    assert fasta.count(">") == 2
    assert fasta.startswith(">PBVPYT ")
    assert route.calls.last.request.url.params["limit"] == "500"


def test_protein_fasta_and_structure(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: object
) -> None:
    api.get("/proteins/PBFCTW/fasta").mock(return_value=reply("fasta_one"))
    assert client.proteins.fasta("PBFCTW").startswith(">PBFCTW | PETase")
    pdb = "HEADER    HYDROLASE\nATOM      1  N   MET A   1\nEND\n"
    api.get("/proteins/PBFCTW/structure").respond(
        text=pdb,
        headers={
            "content-type": "chemical/x-pdb",
            "content-disposition": 'inline; filename="5XJH.pdb"',
        },
    )
    assert client.proteins.structure("PBFCTW") == pdb
    from pathlib import Path

    written = client.proteins.structure("PBFCTW", Path(str(tmp_path)))
    assert isinstance(written, Path) and written.name == "5XJH.pdb"
    assert written.read_text() == pdb


def test_lineage(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/engineering/proteins").mock(return_value=reply("engineering"))
    parent = client.proteins.engineered_parents().first()
    assert parent is not None and parent.accession == "PBKFNU"
    api.get("/engineering/proteins/PBFCTW").respond(
        json={"id": 3, "name": "PETase", "accession": "PBFCTW", "plasticdb_id": "00001"}
    )
    assert client.proteins.lineage("PBFCTW").variants == []


def test_microorganisms(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/microorganisms").mock(return_value=reply("microorganisms"))
    item = client.microorganisms.list(genus="Pseudomonas", class_="Gammaproteobacteria").first()
    assert isinstance(item, MicroorganismSummary)
    params = route.calls.last.request.url.params
    assert params["class"] == "Gammaproteobacteria" and "class_" not in params
    api.get("/microorganisms/PBCZU2").mock(return_value=reply("microorganism"))
    organism = client.microorganisms.get("PBCZU2", evidence_scope="whole_organism")
    assert isinstance(organism, Microorganism)
    client.microorganisms.get("PBCZU2", evidence_scope="enzyme_only")
    assert api.calls.last.request.url.params["evidence_scope"] == "enzyme_only"
    assert organism.lineage_ranks()["kingdom"] == "Fungi"
    assert organism.lineage_ranks()["clade"] == "Opisthokonta"


def test_microorganism_taxonomy_follows_a_rename(
    api: respx.MockRouter, client: plasticdb.Client
) -> None:
    api.get("/microorganisms/taxonomy/genus/Oldname").respond(
        302, headers={"location": "/api/v1/microorganisms/taxonomy/genus/Pseudomonas"}
    )
    api.get("/microorganisms/taxonomy/genus/Pseudomonas").mock(return_value=reply("taxonomy"))
    group = client.microorganisms.taxonomy("genus", "Oldname")
    assert group.name == "Pseudomonas" and group.microbe_total == 69


def test_plastics(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/plastics").mock(return_value=reply("plastics"))
    assert isinstance(client.plastics.list(has_evidence=True).first(), PlasticSummary)
    route = api.get("/plastics/16").mock(return_value=reply("plastic"))
    plastic = client.plastics.get(16, protein_limit=5, microorganism_polarity="negative")
    assert isinstance(plastic, Plastic) and plastic.name == "PET"
    params = route.calls.last.request.url.params
    assert params["protein_limit"] == "5" and params["microorganism_polarity"] == "negative"
    api.get("/plastics/names").mock(return_value=reply("plastics_names"))
    assert client.plastics.names()[0].name == "(PCL)2-b-PEG-b-(PCL)2"


def test_papers(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/papers").mock(return_value=reply("papers"))
    assert isinstance(client.papers.list(protein="PBFCTW", min_year=2020).first(), PaperSummary)
    assert route.calls.last.request.url.params["protein"] == "PBFCTW"
    api.get("/papers/1262").mock(return_value=reply("paper"))
    assert isinstance(client.papers.get(1262), Paper)


def test_paper_export(api: respx.MockRouter, client: plasticdb.Client) -> None:
    bib = "@article{x, title={PETase}}\n"
    one = api.get("/papers/1262/export").respond(text=bib, headers={"content-type": "text/plain"})
    many = api.get("/papers/export").respond(text=bib, headers={"content-type": "text/plain"})
    assert client.papers.export(1262) == bib
    assert one.calls.last.request.url.params["format"] == "bibtex"
    client.papers.export(format="ris", plastic="PET")
    assert many.calls.last.request.url.params["plastic"] == "PET"
    with pytest.raises(TypeError):
        client.papers.export(1262, plastic="PET")


def test_paper_footprint(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/papers/1262/footprint").respond(
        json={"paper": fixture("papers")["body"]["items"][0], "counts": {"proteins": 1}}
    )
    assert client.papers.footprint(1262).counts == {"proteins": 1}


def test_unknown_fields_are_kept(api: respx.MockRouter, client: plasticdb.Client) -> None:
    body = fixture("proteins")["body"]
    body["items"][0]["brand_new_field"] = {"x": 1}
    api.get("/proteins").respond(json=body)
    item = client.proteins.list().first()
    assert item is not None and item.model_extra == {"brand_new_field": {"x": 1}}


def test_every_record_fixture_validates() -> None:
    """The models accept every captured live response without loss."""
    from plasticdb import models as m

    singles: dict[str, type[m.PlasticDBModel]] = {
        "protein": m.Protein,
        "protein_summary": m.Protein,
        "evidence_interaction": m.InteractionRecord,
        "variant": m.Variant,
        "microorganism": m.Microorganism,
        "plastic": m.Plastic,
        "paper": m.Paper,
        "pathway": m.Pathway,
        "pathway_graph": m.PathwayGraph,
        "stats": m.Stats,
        "stats_activity": m.ProteinActivityStats,
        "search_grouped": m.GroupedSearch,
        "taxonomy": m.TaxonomyGroup,
    }
    for name, model in singles.items():
        model.model_validate(fixture(name)["body"])
    lists: dict[str, type[m.PlasticDBModel]] = {
        "proteins": m.ProteinSummary,
        "variants_list": m.ProteinSummary,
        "engineering": m.ProteinSummary,
        "microorganisms": m.MicroorganismSummary,
        "plastics": m.PlasticSummary,
        "papers": m.PaperSummary,
        "pathways": m.PathwaySummary,
        "changes": m.Change,
        "search": m.SearchHit,
        "constructs": m.Construct,
        "preparations": m.Preparation,
        "protein_interactions": m.Interaction,
        "protein_kinetics": m.Kinetics,
        "protein_properties": m.Properties,
        "protein_variants": m.VariantSummary,
        "proteins_sequence": m.ProteinSummary,
        "evidence_interactions": m.InteractionRecord,
        "evidence_experiments": m.ExperimentRecord,
        "evidence_observations": m.ObservationRecord,
    }
    for name, model in lists.items():
        items = fixture(name)["body"]["items"]
        assert items, name
        for item in items:
            model.model_validate(item)


def test_redirect_on_a_non_merge_301(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/papers/1").respond(301, headers={"location": "/elsewhere"})
    with pytest.raises(errors.UnexpectedResponseError):
        client.papers.get(1)


def test_path_parameters_are_quoted(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get(url__regex=r".*/microorganisms/taxonomy/.*").mock(
        return_value=reply("taxonomy")
    )
    client.microorganisms.taxonomy("genus", "Candidatus A/B")
    assert route.calls.last.request.url.raw_path.endswith(b"/genus/Candidatus%20A%2FB")


def test_live_only_record_on_a_404_release(api: respx.MockRouter) -> None:
    api.get("/variants/PBDTZX").respond(
        404, json={"detail": "No release 2026.99", "release": "2026.99", "reason": "unknown"}
    )
    with plasticdb.Client(release="2026.99") as c, pytest.raises(errors.UnknownReleaseError):
        c.variants.get("PBDTZX")


def test_http_error_bodies_from_fixtures(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/proteins").mock(return_value=reply("bad_param"))
    with pytest.raises(errors.ValidationError) as info:
        client.proteins.list(sort_by="year").first()
    assert info.value.errors[0]["loc"] == ["query", "sort_by"]


def test_connection_error(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/plastics/1").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(errors.APIConnectionError):
        client.plastics.get(1)


def test_merged_microorganism(api: respx.MockRouter, client: plasticdb.Client) -> None:
    body = _merged("PBSURV", "microorganism")
    api.get("/microorganisms/PBOLD1").respond(
        301, json=body, headers={"location": body["location"]}
    )
    survivor = {**fixture("microorganism")["body"], "accession": "PBSURV"}
    route = api.get("/microorganisms/PBSURV").respond(json=survivor)
    with pytest.raises(errors.MergedError) as info:
        client.microorganisms.get("PBOLD1")
    assert info.value.kind == "microorganism" and info.value.merged_into == "PBSURV"
    with pytest.warns(errors.MergedRecordWarning):
        organism = client.microorganisms.get(
            "PBOLD1", evidence_scope="enzyme_only", follow_merges=True
        )
    assert organism.accession == "PBSURV"
    assert route.calls.last.request.url.params["evidence_scope"] == "enzyme_only"


def test_withdrawn_microorganism(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/microorganisms/PBGONE").respond(
        410, json={"detail": "withdrawn", "accession": "PBGONE", "reason": "withdrawn"}
    )
    with pytest.raises(errors.GoneError) as info:
        client.microorganisms.get("PBGONE", follow_merges=True)
    assert info.value.accession == "PBGONE"


def test_stats_enzyme_only(api: respx.MockRouter, client: plasticdb.Client) -> None:
    route = api.get("/stats").mock(return_value=reply("stats"))
    client.stats.summary(evidence_scope="enzyme_only")
    assert route.calls.last.request.url.params["evidence_scope"] == "enzyme_only"
