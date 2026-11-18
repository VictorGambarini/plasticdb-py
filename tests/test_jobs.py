from __future__ import annotations

import gzip
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import plasticdb
from plasticdb import errors
from plasticdb.models import JobHit, JobInfo, PathwayStep
from tests.conftest import fixture, reply

JOB_ID = "3f2c9a1e-7b4d-4e8a-9c0f-1a2b3c4d5e6f"
ROOT = "https://v2.plasticdb.org"
FASTA = ">contig_1_0\nMNFPRASRLMQAAVLGGLMAVSAAATAQTNPYARGPNPTAASLEASAGPF\n"


@pytest.fixture
def keyed(api: respx.MockRouter) -> Iterator[plasticdb.Client]:
    with plasticdb.Client(api_key="pdb_test_key") as c:
        yield c


@pytest.fixture
def pinned(api: respx.MockRouter) -> Iterator[plasticdb.Client]:
    with plasticdb.Client(api_key="pdb_test_key", release="2026.30") as c:
        yield c


def parts(request: httpx.Request) -> list[tuple[str, str | None, bytes]]:
    """``(field, filename, body)`` for each part of a multipart request."""
    ctype = request.headers["content-type"]
    boundary = ctype.split("boundary=", 1)[1].encode()
    out = []
    for chunk in request.content.split(b"--" + boundary)[1:-1]:
        head, _, body = chunk[2:-2].partition(b"\r\n\r\n")
        disposition = head.decode().split("\r\n")[0]
        name = disposition.split('name="', 1)[1].split('"', 1)[0]
        filename = None
        if 'filename="' in disposition:
            filename = disposition.split('filename="', 1)[1].split('"', 1)[0]
        out.append((name, filename, body))
    return out


def fields(request: httpx.Request) -> dict[str, list[str]]:
    form: dict[str, list[str]] = {}
    for name, filename, body in parts(request):
        if filename is None:
            form.setdefault(name, []).append(body.decode())
    return form


def files(request: httpx.Request) -> list[tuple[str, str, bytes]]:
    return [(n, f, b) for n, f, b in parts(request) if f is not None]


def job_reply(name: str) -> httpx.Response:
    return reply(name)


# ── an API key is required ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.jobs.annotate_genome(FASTA),
        lambda c: c.jobs.submit("hmm_screen", FASTA),
        lambda c: c.jobs.compare_genomes([FASTA, FASTA], labels=["a", "b"]),
        lambda c: c.jobs.build_tree(plastic="PET"),
        lambda c: c.jobs.search_sequence("MNFPRASRLMQ"),
        lambda c: c.jobs.list(),
    ],
)
def test_jobs_need_an_api_key(api: respx.MockRouter, client: plasticdb.Client, call: Any) -> None:
    with pytest.raises(plasticdb.AuthenticationError, match="PLASTICDB_API_KEY"):
        call(client)
    assert not api.calls


def test_key_check_runs_before_reading_the_input(client: plasticdb.Client) -> None:
    with pytest.raises(plasticdb.AuthenticationError):
        client.jobs.annotate_genome("/no/such/genome.faa")


# ── submitting ───────────────────────────────────────────────────────────────


def test_submit_uploads_multipart_with_the_pinned_release(
    api: respx.MockRouter, pinned: plasticdb.Client, tmp_path: Path
) -> None:
    genome = tmp_path / "genome.faa.gz"
    genome.write_bytes(gzip.compress(FASTA.encode()))
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))

    job = pinned.jobs.annotate_genome(genome, evalue=1e-10, include_variants=False, min_scov=0)

    request = route.calls.last.request
    assert request.url.params["release"] == "2026.30"
    assert request.headers["authorization"] == "Bearer pdb_test_key"
    assert fields(request) == {
        "job_type": ["annotate_genome"],
        "evalue": ["1e-10"],
        "include_variants": ["false"],
        "min_scov": ["0"],
    }
    [(name, filename, body)] = files(request)
    assert (name, filename) == ("file", "genome.fasta.gz")
    assert gzip.decompress(body).decode() == FASTA
    assert isinstance(job, plasticdb.Job)
    assert job.id == JOB_ID
    assert job.status == "waiting"
    assert job.release == "2026.30"
    assert not job.finished


def test_per_call_release_wins(api: respx.MockRouter, pinned: plasticdb.Client) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    pinned.jobs.annotate_gene(FASTA, release="2026.29")
    assert route.calls.last.request.url.params["release"] == "2026.29"


def test_unpinned_submission_sends_no_release(
    api: respx.MockRouter, keyed: plasticdb.Client
) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    keyed.jobs.submit("annotate-gene", FASTA, blast_type="blastp")
    request = route.calls.last.request
    assert "release" not in request.url.params
    assert fields(request)["job_type"] == ["annotate_gene"]
    [(_, filename, body)] = files(request)
    assert filename == "input.fasta"
    assert body.decode() == FASTA


def test_hmm_screen_repeats_target_polymers(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    keyed.jobs.hmm_screen(gzip.compress(FASTA.encode()), target_polymers=("PET", "PLA"))
    request = route.calls.last.request
    assert fields(request) == {"job_type": ["hmm_screen"], "target_polymers": ["PET", "PLA"]}
    # HMMER reads the suffix, so gzip content is always named .gz.
    assert files(request)[0][1] == "input.fasta.gz"


def test_compare_genomes_names_each_upload_after_its_label(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    a = tmp_path / "E. coli K-12.fna"
    a.write_text(FASTA)
    b = tmp_path / "sakaiensis.fa.gz"
    b.write_bytes(gzip.compress(FASTA.encode()))
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))

    keyed.jobs.compare_genomes([a, b], min_scov=50)

    request = route.calls.last.request
    assert fields(request) == {"job_type": ["compare_genomes"], "min_scov": ["50"]}
    assert [(n, f) for n, f, _ in files(request)] == [
        ("files", "E._coli_K-12.fasta"),
        ("files", "sakaiensis.fasta.gz"),
    ]


def test_compare_genomes_from_a_mapping(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    keyed.jobs.compare_genomes({"strain A": FASTA, "strain B": FASTA.encode()})
    assert [f for _, f, _ in files(route.calls.last.request)] == [
        "strain_A.fasta",
        "strain_B.fasta",
    ]


@pytest.mark.parametrize(
    ("genomes", "labels", "message"),
    [
        ([FASTA], None, "at least two"),
        ([FASTA, FASTA], ["x", "x"], "labelled 'x'"),
        ([FASTA, FASTA], ["x"], "1 labels for 2"),
    ],
)
def test_compare_genomes_checks_before_uploading(
    api: respx.MockRouter,
    keyed: plasticdb.Client,
    genomes: list[str],
    labels: list[str] | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        keyed.jobs.compare_genomes(genomes, labels=labels)
    assert not api.calls


def test_annotate_list_sends_rows_as_tsv(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    keyed.jobs.annotate_list(
        [["Genus", "Species"], ["Ideonella", "sakaiensis"]], genus_column=1, species_column=2
    )
    request = route.calls.last.request
    assert fields(request) == {
        "job_type": ["annotate_list"],
        "genus_column": ["1"],
        "species_column": ["2"],
    }
    [(_, filename, body)] = files(request)
    assert filename == "input.tsv"
    assert body == b"Genus\tSpecies\nIdeonella\tsakaiensis\n"


def test_pathway_analysis_thresholds(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    route = api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    keyed.jobs.pathway_analysis(FASTA, min_step_identity=50, max_step_evalue=1e-20)
    assert fields(route.calls.last.request) == {
        "job_type": ["pathway_analysis"],
        "min_step_identity": ["50"],
        "max_step_evalue": ["1e-20"],
    }


def test_submit_rejects_unknown_types_and_parameters(keyed: plasticdb.Client) -> None:
    with pytest.raises(ValueError, match="Unknown job type"):
        keyed.jobs.submit("blast_everything", FASTA)
    with pytest.raises(ValueError, match="synchronous"):
        keyed.jobs.submit("search_sequence", FASTA)
    with pytest.raises(TypeError, match="target_polymers"):
        keyed.jobs.submit("annotate_gene", FASTA, target_polymers=["PET"])
    with pytest.raises(TypeError, match="unexpected parameter"):
        keyed.jobs.build_tree(plastics="PET")


def test_release_unsupported_is_a_typed_error(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    api.post("/jobs").mock(return_value=reply("job_release_unsupported"))
    with pytest.raises(plasticdb.UnsupportedFilterError) as info:
        pinned.jobs.hmm_screen(FASTA)
    assert info.value.parameters == ["release"]
    assert "HMM profile library" in str(info.value)


def test_unknown_release_is_a_typed_error(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    api.post("/jobs").mock(return_value=reply("unknown_release"))
    with pytest.raises(errors.UnknownReleaseError):
        keyed.jobs.annotate_gene(FASTA, release="2026.99")


def test_upload_too_large(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    api.post("/jobs").mock(
        return_value=httpx.Response(
            413, json={"detail": "'input.fasta' is larger than the 1 GB per-file limit"}
        )
    )
    with pytest.raises(plasticdb.PayloadTooLargeError, match="per-file limit"):
        keyed.jobs.annotate_genome(FASTA)


# ── retries: a submission is never resent once it may have been processed ────


def test_submission_is_not_retried_on_5xx(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.post("/jobs").mock(return_value=httpx.Response(502, text="Bad Gateway"))
    with pytest.raises(plasticdb.ServerError):
        keyed.jobs.annotate_genome(FASTA)
    assert route.call_count == 1
    assert sleeps == []


def test_submission_read_timeout_is_not_retried_and_says_so(
    api: respx.MockRouter, keyed: plasticdb.Client
) -> None:
    route = api.post("/jobs").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(plasticdb.APITimeoutError, match=r"check client\.jobs\.list\(\)"):
        keyed.jobs.annotate_genome(FASTA)
    assert route.call_count == 1


def test_submission_rate_limit_waits_and_resends_the_whole_upload(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float], tmp_path: Path
) -> None:
    genome = tmp_path / "g.faa"
    genome.write_text(FASTA)
    route = api.post("/jobs")
    route.side_effect = [
        httpx.Response(429, json={"detail": "30 per 1 hour"}, headers={"Retry-After": "12"}),
        job_reply("job_submitted"),
    ]
    keyed.jobs.annotate_genome(genome)
    assert sleeps == [12.0]
    assert [files(c.request)[0][2] for c in route.calls] == [FASTA.encode()] * 2


def test_submission_rate_limit_longer_than_max_wait_raises(
    api: respx.MockRouter, keyed: plasticdb.Client
) -> None:
    api.post("/jobs").mock(
        return_value=httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "1800"})
    )
    with pytest.raises(plasticdb.RateLimitError) as info:
        keyed.jobs.annotate_genome(FASTA)
    assert info.value.retry_after == 1800


def test_connect_error_resends_from_fresh_handles(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    genome = tmp_path / "g.faa"
    genome.write_text(FASTA)
    route = api.post("/jobs")
    route.side_effect = [httpx.ConnectError("refused"), job_reply("job_submitted")]
    keyed.jobs.annotate_genome(genome)
    assert route.call_count == 2
    assert files(route.calls.last.request)[0][2] == FASTA.encode()


# ── waiting ──────────────────────────────────────────────────────────────────


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> list[float]:
    """``time.monotonic`` advanced by every client sleep."""
    now = [1000.0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(plasticdb.Client, "_sleep", staticmethod(sleep))
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


def _submitted(api: respx.MockRouter, client: plasticdb.Client) -> plasticdb.Job:
    api.post("/jobs").mock(return_value=job_reply("job_submitted"))
    return client.jobs.annotate_genome(FASTA)


def test_wait_backs_off_until_done(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float], clock: list[float]
) -> None:
    job = _submitted(api, keyed)
    route = api.get(f"/jobs/{JOB_ID}")
    route.side_effect = [job_reply("job_running")] * 3 + [job_reply("job_done")]

    assert job.wait() is job

    assert job.done and job.finished
    assert sleeps == [2.0, 3.0, 4.5]
    assert route.call_count == 4
    assert len(job.hits()) == 2
    assert route.call_count == 4  # the final poll already carried the hits


def test_wait_caps_the_interval(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float], clock: list[float]
) -> None:
    job = _submitted(api, keyed)
    api.get(f"/jobs/{JOB_ID}").side_effect = [job_reply("job_running")] * 4 + [
        job_reply("job_done")
    ]
    job.wait(poll_interval=10, max_poll_interval=12)
    assert sleeps == [10.0, 12.0, 12.0, 12.0]


def test_wait_timeout_leaves_the_job_running(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float], clock: list[float]
) -> None:
    job = _submitted(api, keyed)
    api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("job_running"))
    with pytest.raises(plasticdb.JobTimeoutError) as info:
        job.wait(timeout=5)
    assert isinstance(info.value, TimeoutError)
    assert info.value.job is job
    assert job.status == "running"
    assert sum(sleeps) == pytest.approx(5.0)


def test_wait_raises_on_failure(
    api: respx.MockRouter, keyed: plasticdb.Client, clock: list[float]
) -> None:
    job = _submitted(api, keyed)
    api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("job_error"))
    with pytest.raises(plasticdb.JobFailedError, match="blastx"):
        job.wait()
    assert job.failed
    assert job.wait(raise_on_error=False) is job
    with pytest.raises(plasticdb.JobFailedError):
        job.hits()


def test_wait_rides_out_a_long_rate_limit(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float], clock: list[float]
) -> None:
    job = _submitted(api, keyed)
    api.get(f"/jobs/{JOB_ID}").side_effect = [
        httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "90"}),
        job_reply("job_done"),
    ]
    job.wait()
    assert sleeps == [90.0]


def test_results_before_done_say_to_wait(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _submitted(api, keyed)
    api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("job_running"))
    with pytest.raises(plasticdb.JobError, match=r"wait\(\)"):
        job.results()


# ── results ──────────────────────────────────────────────────────────────────


def _done(api: respx.MockRouter, client: plasticdb.Client, name: str) -> plasticdb.Job:
    api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply(name))
    return client.jobs.get(JOB_ID)


def test_hits_are_typed_rows(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_done")
    hits = job.results()
    assert isinstance(hits, plasticdb.ResultTable)
    first = hits[0]
    assert isinstance(first, JobHit)
    assert first.protein is not None and first.protein.source_microorganism is not None
    assert first.protein.source_microorganism.tax_id == 1547922
    assert first.protein_plastics["plastics_positive"] == ["PET"]
    row = hits.rows()[0]
    assert row["protein.name"] == "PETase"
    assert row["protein.source_microorganism.name"] == "Ideonella sakaiensis"
    assert row["plastics_positive"] == ["PET"]
    assert row["plastics_negative"] == ["PE"]
    assert "evidence" not in row and "protein.degradation_links" not in row


def test_hits_to_pandas(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    pd = pytest.importorskip("pandas")
    df = _done(api, keyed, "job_done").hits().to_pandas()
    assert isinstance(df, pd.DataFrame)
    assert list(df["subject_accession"]) == ["PBFCTW", "PBFCTW"]
    assert df["bitscore"].iloc[0] == pytest.approx(540.2)


def test_hits_to_polars(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    pytest.importorskip("polars")
    df = _done(api, keyed, "job_compare").hits().to_polars()
    assert df["source_label"].to_list() == ["E_coli_K12", "I_sakaiensis"]


def test_provenance(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    prov = _done(api, keyed, "job_done").provenance
    assert prov.release == "2026.30"
    assert prov.release_source == "snapshot"
    assert prov.reference_sha256 == "9d5ed678fe57bcca610140957afab571" * 2
    assert prov.tool_versions == {"diamond": "2.1.9"}
    assert prov.app_version == "2.85.0"
    assert prov.record["input_filename"] == "genome.faa.gz"
    assert prov.to_dict()["job_type"] == "annotate_genome"


def test_hmm_screen_hits(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_hmm")
    [hit] = job.hits()
    assert hit.subject_seq_id == "PET_hydrolase"
    assert hit.calibration_status == "separable"
    assert job.info.result_metadata is not None
    assert job.info.result_metadata["polymer_hits"] == {"PET_hydrolase": 1}
    assert job.provenance.tool_versions == {"hmmer": "3.4"}


def test_pathway_steps(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    steps = _done(api, keyed, "job_pathway").results()
    assert isinstance(steps, plasticdb.ResultTable)
    assert [type(s) for s in steps] == [PathwayStep, PathwayStep]
    assert [(s.enzyme, s.present) for s in steps] == [
        ("PET hydrolase", True),
        ("MHET hydrolase", False),
    ]
    row = steps.rows()[0]
    assert row["best_hit.subject_label"] == "IsPETase"


TAXA_TSV = (
    "# PlasticDB taxa table annotation\n"
    f"# job_id: {JOB_ID}\n"
    "# data_release: 2026.30\n"
    "Genus\tSpecies\tPlasticDB match\tMatch quality\tPlastics (positive)\n"
    "Ideonella\tsakaiensis\tIdeonella sakaiensis\texact\tPET\n"
    "Nonexistus\tfakeus\tNo match\t\t\n"
)


def test_annotate_list_taxa_from_the_results_file(
    api: respx.MockRouter, keyed: plasticdb.Client
) -> None:
    job = _done(api, keyed, "job_annotate_list")
    api.get(f"/jobs/{JOB_ID}/download").mock(
        return_value=httpx.Response(
            200, text=TAXA_TSV, headers={"content-type": "text/tab-separated-values"}
        )
    )
    taxa = job.results()
    assert isinstance(taxa, plasticdb.ResultTable)
    assert len(taxa) == 2
    assert taxa[0]["PlasticDB match"] == "Ideonella sakaiensis"
    assert taxa.rows()[1]["PlasticDB match"] == "No match"


def test_tree_reads_the_tree_route(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    detail = api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("tree_detail"))
    tree_route = api.get(f"/tree/{JOB_ID}").mock(return_value=job_reply("tree_done"))
    job = keyed.jobs.get(JOB_ID)
    assert detail.call_count == 1 and tree_route.call_count == 1
    tree = job.results()
    assert isinstance(tree, plasticdb.models.TaxonomyTree)
    assert tree.newick is not None and tree.newick.endswith("Pseudomonadota);")
    assert tree.columns == ["PE", "PET"]
    assert tree.rows()[0] == {"organism": "Ideonella sakaiensis", "PE": False, "PET": True}
    assert job.release == "2026.30"
    job.refresh()
    assert tree_route.call_count == 2


def test_tree_to_pandas_and_biopython(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("tree_detail"))
    api.get(f"/tree/{JOB_ID}").mock(return_value=job_reply("tree_done"))
    tree = keyed.jobs.get(JOB_ID).tree()
    pytest.importorskip("pandas")
    assert list(tree.to_pandas().columns) == ["organism", "PE", "PET"]
    pytest.importorskip("Bio")
    phylo = tree.to_biopython()
    assert sorted(t.name for t in phylo.get_terminals()) == [
        "Ideonella_sakaiensis",
        "Pseudomonas_putida",
    ]


def test_build_tree_posts_filters(api: respx.MockRouter, pinned: plasticdb.Client) -> None:
    route = api.post("/tree/build").mock(return_value=job_reply("tree_done"))
    job = pinned.jobs.build_tree(plastic="PET", class_="Gammaproteobacteria", polarity="positive")
    request = route.calls.last.request
    assert request.url.params["release"] == "2026.30"
    assert json.loads(request.content) == {
        "plastic": "PET",
        "class": "Gammaproteobacteria",
        "polarity": "positive",
    }
    assert job.job_type == "build_tree"


def test_build_tree_by_ids_via_submit(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    route = api.post("/tree/build").mock(return_value=job_reply("tree_done"))
    keyed.jobs.submit("taxonomy-tree", microorganism_ids=["3", 5])
    assert json.loads(route.calls.last.request.content) == {"microorganism_ids": [3, 5]}


def test_comparison(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_compare")
    body = fixture("job_compare")["body"]
    route = api.get(f"/jobs/{JOB_ID}/comparison").mock(
        return_value=httpx.Response(200, json={"comparison": body["comparison"], "view": {}})
    )
    matrix = job.comparison(scov=0, all_hits=True)
    assert route.calls.last.request.url.params.multi_items() == [
        ("format", "json"),
        ("scov", "0"),
        ("all", "true"),
    ]
    assert [g.label for g in matrix.genomes] == ["E_coli_K12", "I_sakaiensis"]
    assert matrix.normalisation is not None and matrix.normalisation.key == "per_1000_proteins"
    rows = matrix.table().rows()
    assert rows[1]["counts.PET"] == 1
    assert rows[1]["normalised.PET"] == pytest.approx(0.31)


# ── downloads ────────────────────────────────────────────────────────────────

TSV_NAME = "plasticdb_annotate_genome_3f2c9a1e.tsv"
HITS_TSV = b"# PlasticDB annotate_genome results\n# release: 2026.30\nqseqid\tsseqid\n"


def test_download_streams_the_results_file(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    job = _done(api, keyed, "job_done")
    route = api.get(f"/jobs/{JOB_ID}/download").mock(
        return_value=httpx.Response(
            200,
            content=HITS_TSV,
            headers={
                "content-type": "text/tab-separated-values; charset=utf-8",
                "content-disposition": f'attachment; filename="{TSV_NAME}"',
            },
        )
    )
    path = job.download(tmp_path, qcov=50)
    assert path == tmp_path / TSV_NAME
    assert path.read_bytes() == HITS_TSV
    assert dict(route.calls.last.request.url.params) == {"qcov": "50"}
    assert route.calls.last.request.headers["authorization"] == "Bearer pdb_test_key"


def test_download_compare_genomes_zip(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("comparison_matrix.tsv", "genome\tPET\n")
        zf.writestr("provenance.json", "{}")
        zf.writestr("E_coli_K12_hits.tsv", "b0001\tprotein|PBFCTW\n")
    job = _done(api, keyed, "job_compare")
    api.get(f"/jobs/{JOB_ID}/download").mock(
        return_value=httpx.Response(
            200,
            content=buf.getvalue(),
            headers={
                "content-type": "application/zip",
                "content-disposition": 'attachment; filename="cmp_3f2c9a1e.zip"',
            },
        )
    )
    path = job.download(tmp_path / "out" / "cmp.zip", all_hits=True)
    assert path == tmp_path / "out" / "cmp.zip"
    with zipfile.ZipFile(path) as zf:
        assert "comparison_matrix.tsv" in zf.namelist()


def test_download_of_expired_results(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    body = fixture("job_done")["body"]
    body["results_expired_at"] = "2027-02-16T01:00:00Z"
    api.get(f"/jobs/{JOB_ID}").mock(return_value=httpx.Response(200, json=body))
    job = keyed.jobs.get(JOB_ID)
    with pytest.raises(plasticdb.GoneError, match="retention"):
        job.download(tmp_path)


def test_download_of_someone_elses_job(
    api: respx.MockRouter, keyed: plasticdb.Client, tmp_path: Path
) -> None:
    job = _done(api, keyed, "job_done")
    api.get(f"/jobs/{JOB_ID}/download").mock(
        return_value=httpx.Response(403, json={"detail": "Access denied"})
    )
    with pytest.raises(plasticdb.PermissionDenied):
        job.download(tmp_path)


# ── sharing ──────────────────────────────────────────────────────────────────


def test_share_lifecycle(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_done")
    create = api.post(f"/jobs/{JOB_ID}/shares").mock(return_value=job_reply("share_created"))
    listed = api.get(f"/jobs/{JOB_ID}/shares").mock(return_value=job_reply("shares"))
    revoke = api.delete(f"/jobs/{JOB_ID}/shares/7").mock(return_value=httpx.Response(204))

    link = job.share(expires_in_days=30)
    assert json.loads(create.calls.last.request.content) == {"expires_in_days": 30}
    assert link.token.startswith("q1w2")
    assert link.url == f"{ROOT}/jobs/shared/{link.token}"

    shares = job.shares()
    assert [(s.id, s.active) for s in shares] == [(7, True), (3, False)]
    assert listed.called

    job.unshare(shares[0])
    assert revoke.called


def test_share_without_expiry_sends_no_body(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_done")
    create = api.post(f"/jobs/{JOB_ID}/shares").mock(return_value=job_reply("share_created"))
    job.share()
    assert create.calls.last.request.content == b""
    with pytest.raises(ValueError):
        job.share(expires_in_days=400)


def test_shared_job_is_read_only_and_needs_no_key(
    api: respx.MockRouter, client: plasticdb.Client, tmp_path: Path
) -> None:
    token = fixture("share_created")["body"]["token"]
    detail = api.get(f"/jobs/shared/{token}").mock(return_value=job_reply("job_done"))
    download = api.get(f"/jobs/shared/{token}/download").mock(
        return_value=httpx.Response(200, content=HITS_TSV)
    )
    comparison = api.get(f"/jobs/shared/{token}/comparison").mock(
        return_value=httpx.Response(200, json={"comparison": {"plastics": ["PET"]}, "view": {}})
    )

    job = client.jobs.shared(f"{ROOT}/jobs/shared/{token}")
    assert job.shared and job.done
    assert "authorization" not in detail.calls.last.request.headers
    assert len(job.hits()) == 2
    job.download(tmp_path / "hits.tsv")
    assert download.called
    assert job.comparison().plastics == ["PET"]
    assert comparison.called
    job.refresh()
    assert detail.call_count == 2
    with pytest.raises(plasticdb.PermissionDenied, match="read-only"):
        job.share()


def test_expired_share_link(api: respx.MockRouter, client: plasticdb.Client) -> None:
    api.get("/jobs/shared/old").mock(
        return_value=httpx.Response(410, json={"detail": "This share link has expired"})
    )
    with pytest.raises(plasticdb.GoneError, match="expired"):
        client.jobs.shared("old")


# ── listing, getting, searching ──────────────────────────────────────────────


def test_list_is_a_collection(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    api.get("/jobs").mock(return_value=job_reply("jobs_list"))
    jobs = keyed.jobs.list()
    assert len(jobs) == 2
    items = jobs.all()
    assert all(isinstance(i, JobInfo) for i in items)
    assert [i.status for i in items] == ["waiting", "done"]
    rows = jobs.rows()
    assert "results" not in rows[0] and "result_metadata.provenance.job_id" not in rows[0]
    assert rows[1]["tool_versions.diamond"] == "2.1.9"


def test_list_never_sends_the_pinned_release(
    api: respx.MockRouter, pinned: plasticdb.Client
) -> None:
    route = api.get("/jobs").mock(return_value=job_reply("jobs_list"))
    pinned.jobs.list().all()
    get = api.get(f"/jobs/{JOB_ID}").mock(return_value=job_reply("job_done"))
    pinned.jobs.get(JOB_ID)
    assert "release" not in route.calls.last.request.url.params
    assert "release" not in get.calls.last.request.url.params


def test_get_unknown_job(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    api.get("/jobs/nope").mock(return_value=httpx.Response(404, json={"detail": "Job not found"}))
    with pytest.raises(plasticdb.NotFoundError):
        keyed.jobs.get("nope")


def test_search_sequence(api: respx.MockRouter, pinned: plasticdb.Client) -> None:
    route = api.post("/bioinformatics/search-sequence").mock(return_value=reply("search_sequence"))
    result = pinned.jobs.search_sequence(">PETase test\nMNFPRASRLM\nQAAVLGGLMA\n", evalue=1e-20)
    params = route.calls.last.request.url.params
    assert params["sequence"] == "MNFPRASRLMQAAVLGGLMA"
    assert params["release"] == "2026.30"
    assert params["evalue"] == "1e-20"
    assert route.calls.last.request.content == b""
    [hit] = result.hits
    assert (hit.accession, hit.pident) == ("PBFCTW", 100.0)
    assert result.reference_sha256 is not None and result.diamond_version == "2.1.9"
    assert result.release == "2026.30"


def test_search_sequence_takes_one_sequence(keyed: plasticdb.Client) -> None:
    with pytest.raises(ValueError, match="one sequence"):
        keyed.jobs.search_sequence(">a\nMKV\n>b\nMKV\n")


def test_search_sequence_is_not_retried(
    api: respx.MockRouter, keyed: plasticdb.Client, sleeps: list[float]
) -> None:
    route = api.post("/bioinformatics/search-sequence").mock(
        return_value=httpx.Response(
            502, json={"detail": "DIAMOND search timed out after 15 seconds"}
        )
    )
    with pytest.raises(plasticdb.ServerError, match="15 seconds"):
        keyed.jobs.search_sequence("MNFPRASRLM")
    assert route.call_count == 1


def test_job_repr(api: respx.MockRouter, keyed: plasticdb.Client) -> None:
    job = _done(api, keyed, "job_done")
    assert repr(job) == f"<plasticdb.Job {JOB_ID} annotate_genome done release=2026.30>"
