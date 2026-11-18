"""Write the job fixtures in tests/fixtures/ (run once; the output is checked in).

Jobs cannot be run against the live server without an API key, so these are
built from the server's own schemas and worker code (``JobRead`` / ``JobDetail``
in ``app/schemas``, ``app/api/v1/jobs.py``'s serialisers, the result metadata
``app/workers/pipelines.py`` and ``tree_builder.py`` write), not captured.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

JOB_ID = "3f2c9a1e-7b4d-4e8a-9c0f-1a2b3c4d5e6f"
RELEASE = "2026.30"
SHA = "9d5ed678fe57bcca610140957afab571" * 2
JSON = {"content-type": "application/json"}
PINNED = {**JSON, "x-plasticdb-release": RELEASE, "x-plasticdb-release-source": "snapshot"}

PROVENANCE = {
    "job_id": JOB_ID,
    "data_release": {
        "version": RELEASE,
        "snapshot_id": None,
        "reference_fingerprint": None,
        "pinned": True,
    },
    "release": RELEASE,
    "release_source": "snapshot",
    "release_data_version": "2026-07-27T03:00:00+00:00",
    "release_manifest_sha256": "ab" * 32,
    "release_tools": {"diamond": "2.1.9"},
    "input_filename": "genome.faa.gz",
    "app_version": "2.85.0",
    "recorded_at": "2026-11-18T01:02:03+00:00",
    "diamond_version": "2.1.9",
    "reference_db": {"name": "PlasticDB.dmnd", "sha256": SHA, "records": 4211},
    "reference_sha256": SHA,
}

BASE: dict[str, Any] = {
    "id": JOB_ID,
    "job_type": "annotate_genome",
    "status": "waiting",
    "evalue": 1e-05,
    "pident": 30.0,
    "organism_type": "no_signalp",
    "blast_type": "blastp",
    "include_variants": False,
    "error_message": None,
    "created_at": "2026-11-18T01:00:00Z",
    "completed_at": None,
    "has_result_file": False,
    "result_count": 0,
    "result_metadata": {
        "release_pin": {
            "version": RELEASE,
            "pinned": True,
            "data_version": "2026-07-27T03:00:00+00:00",
            "manifest_sha256": "ab" * 32,
            "files": {"reference": {"path": "reference/PlasticDB.dmnd", "sha256": SHA}},
            "release_tools": {"diamond": "2.1.9"},
        },
        "view_defaults": {"min_scov": 70.0, "min_qcov": None, "min_bitscore": None},
        "input_filename": "genome.faa.gz",
    },
    "view_defaults": {"min_scov": 70.0, "min_qcov": None, "min_bitscore": None},
    "runtime_seconds": None,
    "input_filename": "genome.faa.gz",
    "parameters_summary": "BLASTP; e-value 1e-05; identity >= 30%",
    "result_summary": None,
    "results_expire_at": "2027-02-16T01:00:00Z",
    "results_expired_at": None,
    "release": RELEASE,
    "release_source": "snapshot",
    "reference_sha256": None,
    "tool_versions": None,
    "result_newick": None,
}


def _hit(i: int, **extra: Any) -> dict[str, Any]:
    hit = {
        "id": 100 + i,
        "evidence": [
            {"plastic": "PET", "positive": 3, "negative": 0, "inconclusive": 1},
            {"plastic": "PE", "positive": 0, "negative": 2, "inconclusive": 0},
        ],
        "evidence_polarity": "positive",
        "source_label": None,
        "query_seq_id": f"contig_1_{i}",
        "subject_seq_id": "protein|PBFCTW",
        "pident": 91.5 - i,
        "length": 290,
        "evalue": 1e-150,
        "bitscore": 540.2 - i,
        "qcov": 98.0,
        "scov": 99.3,
        "subject_accession": "PBFCTW",
        "protein": {
            "id": 17,
            "plasticdb_id": "PlasticDB_17",
            "accession": "PBFCTW",
            "name": "PETase",
            "genbank_id": "GAP38373.1",
            "uniprot_accession": "A0A0K8P6T7",
            "source_microorganism": {
                "id": 5,
                "accession": "PB1ABC",
                "name": "Ideonella sakaiensis",
                "tax_id": 1547922,
            },
            "degradation_links": [
                {
                    "plastic": {"id": 16, "name": "PET", "abbreviation": "PET"},
                    "result": "positive",
                    "positive_count": 12,
                    "negative_count": 0,
                    "inconclusive_count": 0,
                }
            ],
            "paper_count": 14,
        },
        "variant": None,
        "calibration_status": None,
        "calibration_status_reason": None,
    }
    hit.update(extra)
    return hit


def _done(job_type: str, results: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    body = copy.deepcopy(BASE)
    meta = body["result_metadata"]
    meta["provenance"] = dict(PROVENANCE)
    body.update(
        job_type=job_type,
        status="done",
        completed_at="2026-11-18T01:07:30Z",
        has_result_file=True,
        result_count=len(results),
        results=results,
        comparison=None,
        runtime_seconds=450.0,
        result_summary=f"{len(results)} hits",
        reference_sha256=SHA,
        tool_versions={"diamond": "2.1.9"},
    )
    body.update(fields)
    return body


def write(name: str, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
    doc = {"status": status, "headers": headers or PINNED, "body": body}
    (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    submitted = copy.deepcopy(BASE)
    write("job_submitted", 201, submitted)

    running = copy.deepcopy(BASE)
    running.update(status="running", results=[], comparison=None)
    write("job_running", 200, running, JSON)

    write("job_done", 200, _done("annotate_genome", [_hit(0), _hit(1)]), JSON)

    failed = copy.deepcopy(BASE)
    failed.update(
        status="error",
        completed_at="2026-11-18T01:01:00Z",
        results=[],
        comparison=None,
        error_message="Input looks like nucleotide sequence; use blast_type=blastx",
    )
    write("job_error", 200, failed, JSON)

    compare_hits = [
        _hit(0, source_label="E_coli_K12", query_seq_id="b0001"),
        _hit(1, source_label="I_sakaiensis", query_seq_id="ISF6_4831"),
    ]
    comparison = {
        "blast_type": "blastp",
        "counted": "distinct query proteins with a hit",
        "normalisation": {
            "key": "per_1000_proteins",
            "label": "per 1,000 proteins",
            "denominator": "proteins in the genome",
        },
        "normalised": True,
        "plastics": ["PE", "PET"],
        "genomes": [
            {
                "label": "E_coli_K12",
                "hits": 1,
                "positive": 1,
                "not_positive": 0,
                "counts": {"PE": 0, "PET": 1},
                "normalised": {"PE": 0.0, "PET": 0.23},
                "positive_normalised": 0.23,
                "residues": 1324000,
                "sequences": 4300,
            },
            {
                "label": "I_sakaiensis",
                "hits": 1,
                "positive": 1,
                "not_positive": 0,
                "counts": {"PE": 0, "PET": 1},
                "normalised": {"PE": 0.0, "PET": 0.31},
                "positive_normalised": 0.31,
                "residues": 980000,
                "sequences": 3200,
            },
        ],
    }
    compare = _done("compare_genomes", compare_hits, comparison=comparison)
    compare["result_metadata"]["genomes"] = {
        "E_coli_K12": {"sequences": 4300, "residues": 1324000},
        "I_sakaiensis": {"sequences": 3200, "residues": 980000},
    }
    write("job_compare", 200, compare, JSON)

    hmm_hits = [
        _hit(
            0,
            subject_seq_id="PET_hydrolase",
            subject_accession="PET_hydrolase",
            pident=None,
            length=None,
            qcov=None,
            scov=None,
            protein=None,
            evidence=[],
            evidence_polarity="none",
            calibration_status="separable",
            calibration_status_reason=None,
        )
    ]
    hmm = _done(
        "hmm_screen",
        hmm_hits,
        evalue=None,
        pident=None,
        view_defaults=None,
        tool_versions={"hmmer": "3.4"},
    )
    hmm["result_metadata"].update(
        polymer_hits={"PET_hydrolase": 1},
        total_hits=1,
        threshold_mode="gathering",
        threshold_mode_applied="gathering",
        threshold_fallback=None,
        profile_ga={"PET_hydrolase": 25.0},
    )
    write("job_hmm", 200, hmm, JSON)

    pathway = _done("pathway_analysis", [], view_defaults=None)
    pathway["result_metadata"].update(
        pathway_id=1,
        plastic_id=16,
        pathway_name="PET degradation",
        hit_accessions=["PBFCTW"],
        hit_step_ids=[11],
        step_table=[
            {
                "order": 1,
                "step_id": 11,
                "substrate": "PET",
                "product": "MHET",
                "substrate_compartment": "extracellular",
                "product_compartment": "extracellular",
                "step_type": "conversion",
                "enzyme": "PET hydrolase",
                "ec": "3.1.1.101",
                "present": True,
                "best_hit": {
                    "query": "contig_1_0",
                    "subject_label": "IsPETase",
                    "pident": 88.0,
                    "evalue": 1e-120,
                    "bitscore": 480.0,
                    "passes": True,
                },
                "hits_considered": 3,
                "subunits_total": None,
                "subunits_matched": None,
                "references": 4,
                "reference_organisms": 3,
                "single_organism": False,
            },
            {
                "order": 2,
                "step_id": 12,
                "substrate": "MHET",
                "product": "TPA",
                "substrate_compartment": "extracellular",
                "product_compartment": "periplasm",
                "step_type": "conversion",
                "enzyme": "MHET hydrolase",
                "ec": "3.1.1.102",
                "present": False,
                "best_hit": None,
                "hits_considered": 0,
                "subunits_total": None,
                "subunits_matched": None,
                "references": 2,
                "reference_organisms": 2,
                "single_organism": False,
            },
        ],
        missing_steps=[{"order": 2, "step": "MHET → TPA", "enzyme": "MHET hydrolase"}],
        steps_total=2,
        steps_present=1,
    )
    write("job_pathway", 200, pathway, JSON)

    taxa = _done(
        "annotate_list",
        [],
        evalue=None,
        pident=None,
        genus_column=1,
        species_column=2,
        field_separator="tab",
        view_defaults=None,
        reference_sha256=None,
        tool_versions=None,
    )
    taxa["result_metadata"]["taxa"] = {
        "rows": 2,
        "matched": 1,
        "positive": 1,
        "not_positive": 0,
        "genus_level": 0,
        "placeholder": 0,
        "synonym": 0,
        "layout": "columns",
        "genera": [],
    }
    write("job_annotate_list", 200, taxa, JSON)

    tree = {
        k: v
        for k, v in BASE.items()
        if k
        in {
            "id",
            "job_type",
            "status",
            "evalue",
            "pident",
            "organism_type",
            "blast_type",
            "error_message",
            "created_at",
            "completed_at",
            "result_count",
            "release",
            "release_source",
            "reference_sha256",
            "tool_versions",
        }
    }
    tree.update(
        job_type="build_tree",
        status="done",
        evalue=None,
        pident=None,
        organism_type=None,
        blast_type=None,
        completed_at="2026-11-18T01:00:20Z",
        result_newick="((Ideonella_sakaiensis,Pseudomonas_putida)Pseudomonadota);",
        result_metadata={
            "columns": ["PE", "PET"],
            "data": {
                "Ideonella sakaiensis": {"PE": "no", "PET": "yes"},
                "Pseudomonas putida": {"PE": "yes", "PET": "no"},
            },
            "organism_count": 2,
            "leaf_count": 2,
            "skipped_no_lineage": 0,
            "excluded_non_organism": 0,
            "excluded_names": [],
            "merged_into_ancestor": 0,
            "provenance": {"cache_hit": False},
            "release_pin": BASE["result_metadata"]["release_pin"],
        },
    )
    write("tree_done", 200, tree)
    tree_detail = {**tree, "result_newick": None, "results": [], "comparison": None}
    write("tree_detail", 200, tree_detail, JSON)

    listed = [copy.deepcopy(BASE), _done("annotate_genome", [])]
    listed[1].pop("results")
    listed[1].pop("comparison")
    listed[1]["id"] = "0a1b2c3d-0000-4000-8000-000000000001"
    write("jobs_list", 200, {"total": 2, "items": listed}, JSON)

    token = "q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h6j7k8l9z0x1c"
    write(
        "share_created",
        201,
        {
            "id": 7,
            "created_at": "2026-11-18T02:00:00Z",
            "expires_at": "2026-12-18T02:00:00Z",
            "revoked_at": None,
            "active": True,
            "token": token,
            "path": f"/jobs/shared/{token}",
        },
        JSON,
    )
    write(
        "shares",
        200,
        [
            {
                "id": 7,
                "created_at": "2026-11-18T02:00:00Z",
                "expires_at": "2026-12-18T02:00:00Z",
                "revoked_at": None,
                "active": True,
            },
            {
                "id": 3,
                "created_at": "2026-11-10T02:00:00Z",
                "expires_at": None,
                "revoked_at": "2026-11-11T00:00:00Z",
                "active": False,
            },
        ],
        JSON,
    )

    write(
        "search_sequence",
        200,
        {
            "hits": [
                {
                    "query_seq_id": "query",
                    "subject_seq_id": "protein|PBFCTW",
                    "pident": 100.0,
                    "length": 290,
                    "mismatch": 0,
                    "gapopen": 0,
                    "qstart": 1,
                    "qend": 290,
                    "sstart": 1,
                    "send": 290,
                    "evalue": 1.2e-180,
                    "bitscore": 590.1,
                    "plasticdb_id": "PlasticDB_17",
                    "protein_name": "PETase",
                    "entity_type": "protein",
                    "entity_id": 17,
                    "accession": "PBFCTW",
                    "detail_url": "/proteins/PBFCTW",
                    "variant_type": None,
                }
            ],
            "release": RELEASE,
            "release_source": "snapshot",
            "release_data_version": "2026-07-27T03:00:00+00:00",
            "release_manifest_sha256": "ab" * 32,
            "release_tools": {"diamond": "2.1.9"},
            "reference_sha256": SHA,
            "diamond_version": "2.1.9",
        },
    )

    write(
        "job_release_unsupported",
        422,
        {
            "detail": [
                {
                    "type": "release_unsupported",
                    "loc": ["query", "release"],
                    "msg": (
                        "Release 2026.30 did not archive an HMM profile library (it predates "
                        "HMM archiving), so hmm_screen cannot run against it."
                    ),
                    "input": RELEASE,
                }
            ]
        },
        {**JSON, "x-plasticdb-release": RELEASE, "x-plasticdb-release-source": "snapshot"},
    )


if __name__ == "__main__":
    main()
