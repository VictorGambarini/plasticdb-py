"""The ``plasticdb`` command line: output, options, config file and exit codes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

pytest.importorskip("typer")

import typer.main
from typer.testing import CliRunner

from plasticdb.cli import _config, main
from plasticdb.cli._app import app
from plasticdb.cli._core import EXIT_CODES, ExitCode
from plasticdb.cli._output import delimited
from plasticdb.resources.proteins import Proteins
from tests.conftest import reply, snapshot

JOB_ID = "3f2c9a1e-7b4d-4e8a-9c0f-1a2b3c4d5e6f"
FASTA = ">PBDTZX Dm-TA-ΔLCC\nMSNPYQRGPNP\n>PB6695 NI\nMKKLLA\n"
KEY = "pdb_secret_key_1234567890"


class Result:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]  # click < 8.2
    except TypeError:
        return CliRunner()


@pytest.fixture(autouse=True)
def _config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "config"
    monkeypatch.setenv("PLASTICDB_CONFIG_DIR", str(folder))
    monkeypatch.delenv("PLASTICDB_CACHE_DIR", raising=False)
    return folder


def cli(*args: str, input: str | bytes | None = None) -> Result:
    result = _runner().invoke(app, list(args), input=input, catch_exceptions=False)
    return Result(result.exit_code, result.stdout, result.stderr)


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLASTICDB_API_KEY", KEY)


# ── help and version ─────────────────────────────────────────────────────────


def test_help_documents_every_exit_code() -> None:
    result = cli("--help")
    assert result.exit_code == 0
    for code, _ in EXIT_CODES:
        assert f" {int(code)} " in result.stdout


def test_version() -> None:
    from plasticdb import __version__

    assert cli("--version").stdout.strip() == f"plasticdb {__version__}"
    assert cli("version", "--format", "json").stdout.count(__version__) == 1


def test_python_dash_m_without_typer_says_which_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "typer", None)
    assert main(["--help"]) == 1
    err = capsys.readouterr().err
    assert "pip install 'plasticdb[cli]'" in err


def test_main_returns_the_exit_code(api: respx.MockRouter) -> None:
    api.get("/proteins/PBNOPE").respond(404, json={"detail": "Protein not found"})
    assert main(["proteins", "get", "PBNOPE", "-q"]) == ExitCode.NOT_FOUND


# ── output ───────────────────────────────────────────────────────────────────


def test_plan_example_proteins_as_fasta(api: respx.MockRouter) -> None:
    # plasticdb proteins list --plastic PET --format fasta > pet.fasta
    listing = api.get("/proteins").mock(return_value=reply("proteins"))
    api.get("/downloads/proteins.fasta").respond(
        text=FASTA, headers={"content-type": "text/plain; charset=utf-8"}
    )
    result = cli("proteins", "list", "--plastic", "PET", "--format", "fasta")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith(">PBDTZX Dm-TA-ΔLCC\nMSNPYQRGPNP\n")
    assert result.stderr == ""
    assert listing.calls.last.request.url.params["plastic"] == "PET"


def test_lists_default_to_tsv_on_stdout_and_nothing_on_stderr(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    result = cli("proteins", "list")
    assert result.exit_code == 0
    header, first, *_ = result.stdout.splitlines()
    assert header.split("\t")[:3] == ["id", "name", "kind"]
    assert first.split("\t")[1] == "Dm-TA-ΔLCC"
    assert result.stderr == ""


@pytest.mark.parametrize("fmt", ["json", "jsonl", "csv"])
def test_list_formats(api: respx.MockRouter, fmt: str) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    out = cli("proteins", "list", "--format", fmt).stdout
    if fmt == "json":
        assert json.loads(out)[0]["accession"] == "PBDTZX"
    elif fmt == "jsonl":
        lines = out.splitlines()
        assert [json.loads(line)["accession"] for line in lines][:2] == ["PBDTZX", "PB6695"]
    else:
        assert out.splitlines()[0].startswith("id,name,kind")


def test_records_default_to_json(api: respx.MockRouter) -> None:
    api.get("/proteins/PBFCTW").mock(return_value=reply("protein"))
    result = cli("proteins", "get", "PBFCTW")
    assert json.loads(result.stdout)["accession"] == "PBFCTW"


def test_output_file_takes_its_format_from_the_extension(
    api: respx.MockRouter, tmp_path: Path
) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    target = tmp_path / "out" / "pet.csv"
    result = cli("proteins", "list", "-o", str(target))
    assert result.exit_code == 0
    assert result.stdout == ""
    assert f"Wrote {target}" in result.stderr
    assert target.read_text(encoding="utf-8").startswith("id,name,kind")


def test_quiet_silences_messages_but_not_data(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    result = cli("proteins", "list", "-o", str(tmp_path / "p.tsv"), "-q")
    assert result.stderr == "" and result.stdout == ""


def test_output_files_get_the_usual_permissions(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/proteins").mock(return_value=reply("proteins"))
    old = os.umask(0o022)
    try:
        cli("proteins", "list", "-o", str(tmp_path / "p.tsv"))
    finally:
        os.umask(old)
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "p.tsv").stat().st_mode) == 0o644


def test_fasta_is_refused_where_there_are_no_sequences() -> None:
    result = cli("microorganisms", "list", "--format", "fasta")
    assert result.exit_code == ExitCode.USAGE


def test_tsv_cells_stay_on_one_line() -> None:
    text = delimited(
        [{"a": ["x", "y"], "b": "one\ttwo\nthree", "c": None, "d": True, "e": {"k": 1}}], "\t"
    )
    assert text == 'a\tb\tc\td\te\nx;y\tone two three\t\ttrue\t{"k":1}\n'


# ── options ──────────────────────────────────────────────────────────────────


def test_filters_use_the_sdk_names(api: respx.MockRouter) -> None:
    route = api.get("/microorganisms").mock(return_value=reply("microorganisms"))
    result = cli(
        "microorganisms",
        "list",
        "--class",
        "Bacilli",
        "--include-negative",
        "--polarity",
        "negative",
        "--min-year",
        "2020",
        "--query",
        "bacillus",
    )
    assert result.exit_code == 0, result.stderr
    params = route.calls.last.request.url.params
    assert params["class"] == "Bacilli"
    assert params["include_negative"] == "true"
    assert params["polarity"] == "negative"
    assert params["min_year"] == "2020"
    assert params["q"] == "bacillus"


def test_every_filter_of_the_sdk_is_an_option() -> None:
    from plasticdb.resources._filters import ProteinListFilters, keys_of

    group: Any = typer.main.get_command(app)
    command = group.commands["proteins"].commands["list"]
    options = {opt for param in command.params for opt in param.opts}
    for key in keys_of(ProteinListFilters):
        assert "--" + key.rstrip("_").replace("_", "-") in options


def test_release_is_accepted_before_or_after_the_subcommand(api: respx.MockRouter) -> None:
    route = api.get("/proteins").mock(return_value=reply("proteins", **snapshot()))
    cli("--release", "2026.30", "proteins", "list", "-n", "1")
    assert route.calls.last.request.url.params["release"] == "2026.30"
    cli("proteins", "list", "-n", "1", "--release", "2026.31")
    assert route.calls.last.request.url.params["release"] == "2026.31"


def test_environment_variables(api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    route = api.get("/proteins").mock(return_value=reply("proteins", **snapshot()))
    monkeypatch.setenv("PLASTICDB_RELEASE", "2026.30")
    cli("proteins", "list", "-n", "1")
    assert route.calls.last.request.url.params["release"] == "2026.30"


def test_verbose_logs_the_unpinned_notice(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("proteins", **{"X-PlasticDB-Release": "2026.30"}))
    assert "unpinned client" in cli("proteins", "list", "-n", "1", "-v").stderr
    assert "unpinned client" not in cli("proteins", "list", "-n", "1").stderr


def test_merged_warning_goes_to_stderr(api: respx.MockRouter) -> None:
    api.get("/proteins/PBGQPC").mock(return_value=reply("protein_merged"))
    api.get("/proteins/PBMW9H").mock(return_value=reply("protein"))
    result = cli("proteins", "get", "PBGQPC", "--follow-merges")
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["accession"] == "PBFCTW"
    assert "warning: PlasticDB: PBGQPC was merged into PBMW9H" in result.stderr


def test_merged_without_follow_names_the_survivor(api: respx.MockRouter) -> None:
    api.get("/proteins/PBGQPC").mock(return_value=reply("protein_merged"))
    result = cli("proteins", "get", "PBGQPC")
    assert result.exit_code == ExitCode.NOT_FOUND
    assert "PBMW9H" in result.stderr and "--follow-merges" in result.stderr


# ── jobs ─────────────────────────────────────────────────────────────────────


def test_plan_example_annotate_genome_and_wait(
    api: respx.MockRouter, keyed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # plasticdb jobs submit annotate-genome genome.fna --wait -o hits.tsv
    monkeypatch.chdir(tmp_path)
    Path("genome.fna").write_text(">c1\nMNFPRASRLMQ\n", encoding="utf-8")
    submit = api.post("/jobs").mock(return_value=reply("job_submitted"))
    api.get(f"/jobs/{JOB_ID}").mock(side_effect=[reply("job_running"), reply("job_done")])
    api.get(f"/jobs/{JOB_ID}/download").respond(
        content=b"# release: 2026.30\nquery\tsubject\nc1\tPBFCTW\n",
        headers={"content-type": "text/tab-separated-values"},
    )
    result = cli("jobs", "submit", "annotate-genome", "genome.fna", "--wait", "-o", "hits.tsv")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == ""
    assert Path("hits.tsv").read_text(encoding="utf-8").startswith("# release: 2026.30")
    assert JOB_ID in result.stderr
    request = submit.calls.last.request
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert b'name="job_type"\r\n\r\nannotate_genome' in request.content


def test_submit_without_wait_prints_the_id(
    api: respx.MockRouter, keyed: None, tmp_path: Path
) -> None:
    api.post("/jobs").mock(return_value=reply("job_submitted"))
    genome = tmp_path / "g.faa"
    genome.write_text(">a\nMKK\n", encoding="utf-8")
    result = cli("jobs", "submit", "hmm-screen", str(genome), "--target-polymers", "PET")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"{JOB_ID}\n"


def test_submit_reads_stdin(api: respx.MockRouter, keyed: None) -> None:
    route = api.post("/jobs").mock(return_value=reply("job_submitted"))
    result = cli("jobs", "submit", "annotate-gene", "-", "--evalue", "1e-10", input=">a\nMKK\n")
    assert result.exit_code == 0, result.stderr
    content = route.calls.last.request.content
    assert b">a\nMKK\n" in content and b"1e-10" in content


def test_submit_rejects_a_parameter_the_job_type_does_not_take(keyed: None) -> None:
    result = cli("jobs", "submit", "hmm-screen", "-", "--genus-column", "3", input="x")
    assert result.exit_code == ExitCode.USAGE
    assert "--genus-column" in result.stderr


def test_job_results_as_a_table(api: respx.MockRouter, keyed: None) -> None:
    api.get(f"/jobs/{JOB_ID}").mock(return_value=reply("job_done"))
    result = cli("jobs", "results", JOB_ID)
    assert result.exit_code == 0, result.stderr
    header = result.stdout.splitlines()[0].split("\t")
    assert header[:3] == ["id", "query_seq_id", "subject_seq_id"]


# ── exit codes: one test per documented code ─────────────────────────────────


def test_exit_0_ok(api: respx.MockRouter) -> None:
    api.get("/releases/status").respond(json={"running": False})
    assert cli("releases", "status").exit_code == ExitCode.OK


def test_exit_1_generic(api: respx.MockRouter) -> None:
    api.get("/releases/status").respond(
        200, content=b"{not json", headers={"content-type": "application/json"}
    )
    result = cli("releases", "status")
    assert result.exit_code == ExitCode.ERROR
    assert result.stderr.startswith("plasticdb: error:")


def test_exit_2_usage() -> None:
    assert cli("proteins", "list", "--no-such-option").exit_code == ExitCode.USAGE
    assert cli("proteins", "list", "--polarity", "maybe").exit_code == ExitCode.USAGE


def test_exit_2_server_validation(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("bad_param"))
    assert cli("proteins", "list", "--evidence-method", "nope").exit_code == ExitCode.USAGE


def test_exit_3_not_found(api: respx.MockRouter) -> None:
    api.get("/proteins/PBNOPE").respond(404, json={"detail": "Protein not found"})
    result = cli("proteins", "get", "PBNOPE")
    assert result.exit_code == ExitCode.NOT_FOUND
    assert result.stdout == ""
    assert "Protein not found" in result.stderr


def test_exit_4_auth() -> None:
    result = cli("jobs", "list")
    assert result.exit_code == ExitCode.AUTH
    assert "plasticdb auth login" in result.stderr


def test_exit_4_invalid_key(api: respx.MockRouter, keyed: None) -> None:
    api.get("/jobs").respond(401, json={"detail": "Invalid API key"})
    assert cli("jobs", "list").exit_code == ExitCode.AUTH


def test_exit_5_rate_limited(api: respx.MockRouter) -> None:
    api.get("/proteins").respond(
        429, headers={"retry-after": "3600"}, json={"detail": "Rate limit exceeded"}
    )
    result = cli("proteins", "list")
    assert result.exit_code == ExitCode.RATE_LIMITED
    assert "retry after 3600 s" in result.stderr


def test_exit_6_release(api: respx.MockRouter) -> None:
    api.get("/proteins").mock(return_value=reply("unknown_release"))
    assert cli("--release", "1999.01", "proteins", "list").exit_code == ExitCode.RELEASE
    api.get("/releases/1999.01").respond(404, json={"detail": "No release 1999.01"})
    assert cli("cite", "--release", "1999.01").exit_code == ExitCode.RELEASE


def test_exit_6_live_only(api: respx.MockRouter) -> None:
    api.get("/search").respond(
        409,
        json={"detail": "live only", "reason": "live_only", "release": "2026.30"},
    )
    assert cli("--release", "2026.30", "search", "x").exit_code == ExitCode.RELEASE


def test_exit_7_job_failed(api: respx.MockRouter, keyed: None) -> None:
    api.get(f"/jobs/{JOB_ID}").mock(return_value=reply("job_error"))
    result = cli("jobs", "wait", JOB_ID)
    assert result.exit_code == ExitCode.JOB_FAILED
    assert "failed" in result.stderr


def test_exit_8_timeout(api: respx.MockRouter, keyed: None) -> None:
    api.get(f"/jobs/{JOB_ID}").mock(return_value=reply("job_running"))
    result = cli("jobs", "wait", JOB_ID, "--timeout", "0")
    assert result.exit_code == ExitCode.TIMEOUT
    assert "keeps running" in result.stderr


def test_exit_9_unavailable(api: respx.MockRouter) -> None:
    api.get("/releases/status").mock(side_effect=httpx.ConnectError("refused"))
    assert cli("releases", "status").exit_code == ExitCode.UNAVAILABLE
    api.get("/stats").respond(500, json={"detail": "boom"})
    assert cli("stats").exit_code == ExitCode.UNAVAILABLE


def test_exit_130_interrupted(api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(Proteins, "get", interrupt)
    result = cli("proteins", "get", "PBFCTW")
    assert result.exit_code == ExitCode.INTERRUPTED


def test_every_exit_code_is_tested() -> None:
    names = {name for name in globals() if name.startswith("test_exit_")}
    for code in ExitCode:
        assert any(n.startswith(f"test_exit_{int(code)}_") for n in names), code


# ── auth and the config file ─────────────────────────────────────────────────


def test_login_from_stdin_stores_the_key_0600(api: respx.MockRouter, _config_dir: Path) -> None:
    check = api.get("/jobs").respond(json={"total": 0, "items": []})
    result = cli("auth", "login", input=KEY + "\n")
    assert result.exit_code == 0, result.stderr
    assert check.calls.last.request.headers["authorization"] == f"Bearer {KEY}"
    path = _config_dir / "config.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"api_key": KEY}
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(_config_dir.stat().st_mode) == 0o700
    assert KEY not in result.stdout + result.stderr
    assert KEY[-4:] in result.stderr


def test_login_with_a_rejected_key_stores_nothing(api: respx.MockRouter, _config_dir: Path) -> None:
    api.get("/jobs").respond(401, json={"detail": "Invalid API key"})
    result = cli("auth", "login", "--key", KEY)
    assert result.exit_code == ExitCode.AUTH
    assert not (_config_dir / "config.json").exists()


def test_login_without_a_key_is_a_usage_error(_config_dir: Path) -> None:
    assert cli("auth", "login", "--no-verify", input="").exit_code == ExitCode.USAGE
    assert not (_config_dir / "config.json").exists()


def test_stored_key_is_used_and_the_environment_wins(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli("auth", "login", "--no-verify", "--key", KEY).exit_code == 0
    route = api.get("/jobs").respond(json={"total": 0, "items": []})
    assert cli("jobs", "list").exit_code == 0
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"

    status = json.loads(cli("auth", "status", "--format", "json").stdout)
    assert status["source"].endswith("config.json")
    assert status["api_key"] == f"…{KEY[-4:]}"

    monkeypatch.setenv("PLASTICDB_API_KEY", "pdb_from_the_environment")
    cli("jobs", "list")
    assert route.calls.last.request.headers["authorization"] == "Bearer pdb_from_the_environment"
    assert json.loads(cli("auth", "status", "--format", "json").stdout)["source"] == (
        "PLASTICDB_API_KEY"
    )
    cli("--api-key", "pdb_from_the_option", "jobs", "list")
    assert route.calls.last.request.headers["authorization"] == "Bearer pdb_from_the_option"


def test_logout_removes_the_key(_config_dir: Path) -> None:
    cli("auth", "login", "--no-verify", "--key", KEY)
    assert cli("auth", "logout").exit_code == 0
    assert _config.stored_key() is None
    assert cli("auth", "status").exit_code == ExitCode.AUTH


def test_a_world_readable_config_is_warned_about(_config_dir: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permissions")
    cli("auth", "login", "--no-verify", "--key", KEY)
    (_config_dir / "config.json").chmod(0o644)
    result = cli("auth", "status")
    assert result.exit_code == 0
    assert "readable by other users" in result.stderr


# ── downloads and citation (plan examples) ───────────────────────────────────

PARQUET = b"PAR1" + bytes(range(64)) + b"PAR1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _release_with_artefacts() -> tuple[dict[str, Any], bytes]:
    manifest = json.dumps(
        {"release": "2026.50", "files": [{"path": "parquet/proteins.parquet"}]}
    ).encode()
    release = {
        "version": "2026.50",
        "promoted_proteins": 3,
        "promoted_microorganisms": 1,
        "doi": "10.17608/k6.auckland.2026.50",
        "artefacts": {
            "release": "2026.50",
            "manifest_url": "/api/v1/releases/2026.50/artefacts/MANIFEST.json",
            "manifest_sha256": _sha(manifest),
            "files": [
                {
                    "path": "parquet/proteins.parquet",
                    "kind": "table",
                    "size": len(PARQUET),
                    "sha256": _sha(PARQUET),
                    "download_url": "/api/v1/releases/2026.50/artefacts/parquet/proteins.parquet",
                }
            ],
        },
    }
    return release, manifest


def test_plan_example_download_a_release_dataset(
    api: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # plasticdb download dataset --release 2026.50 -o data/
    monkeypatch.chdir(tmp_path)
    release, manifest = _release_with_artefacts()
    api.get("/releases/2026.50").respond(json=release)
    api.get("/releases/2026.50/artefacts/MANIFEST.json").respond(
        content=manifest, headers={"content-type": "application/json"}
    )
    api.get("/releases/2026.50/artefacts/parquet/proteins.parquet").respond(
        content=PARQUET, headers={"content-type": "application/octet-stream"}
    )
    result = cli("download", "dataset", "--release", "2026.50", "-o", "data/")
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "data" / "parquet" / "proteins.parquet").read_bytes() == PARQUET
    assert (tmp_path / "data" / "MANIFEST.json").read_bytes() == manifest
    assert result.stdout == ""
    assert "verified" in result.stderr


def test_live_dataset_download(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get("/downloads/dataset.zip").respond(
        content=b"PK\x03\x04zip",
        headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="plasticdb_dataset.zip"',
        },
    )
    result = cli("download", "dataset", "-o", str(tmp_path) + "/")
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "plasticdb_dataset.zip").read_bytes() == b"PK\x03\x04zip"


def test_download_table_with_filters(api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.get("/downloads/microorganisms.csv").respond(
        content=b"id,name\n1,x\n", headers={"content-type": "text/csv"}
    )
    target = tmp_path / "m.csv"
    result = cli("download", "table", "microorganisms", "--genus", "Bacillus", "-o", str(target))
    assert result.exit_code == 0, result.stderr
    assert target.read_bytes() == b"id,name\n1,x\n"
    assert route.calls.last.request.url.params["genus"] == "Bacillus"
    # A filter of another table is a usage error.
    bad = cli("download", "table", "plastics", "--genus", "Bacillus", "-o", str(target))
    assert bad.exit_code == ExitCode.USAGE


def test_plan_example_cite_a_release(api: respx.MockRouter) -> None:
    # plasticdb cite --release 2026.50
    release, _ = _release_with_artefacts()
    api.get("/releases/2026.50").respond(json=release)
    result = cli("cite", "--release", "2026.50")
    assert result.exit_code == 0, result.stderr
    assert "PlasticDB dataset release 2026.50" in result.stdout
    assert "https://doi.org/10.17608/k6.auckland.2026.50" in result.stdout
    assert "CC BY 4.0" in result.stdout
    bib = cli("cite", "--release", "2026.50", "--format", "bibtex").stdout
    assert "@misc{plasticdb_2026_50," in bib
    assert "doi = {10.17608/k6.auckland.2026.50}" in bib
    assert "@article{gambarini2022plasticdb," in bib


def test_provenance(api: respx.MockRouter) -> None:
    api.get("/releases/current").respond(
        json={"version": "2026.30"}, headers={"X-PlasticDB-Release": "2026.30"}
    )
    api.get("https://v2.plasticdb.org/health").respond(json={"version": "2.85.0"})
    data = json.loads(cli("provenance").stdout)
    assert data["server_version"] == "2.85.0"
    assert data["pinned"] is False


def test_changes_prints_as_of_on_stderr(api: respx.MockRouter) -> None:
    api.get("/changes").mock(return_value=reply("changes"))
    result = cli("changes", "--since", "2026-11-01T00:00:00Z")
    assert result.exit_code == 0, result.stderr
    assert "as_of:" in result.stderr
    assert "as_of" not in result.stdout
