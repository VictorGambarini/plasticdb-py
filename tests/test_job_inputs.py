from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from pathlib import Path

import pytest

from plasticdb._inputs import genome_uploads, safe_stem, sequence_text, to_upload

FASTA = ">p1 PETase\nMNFPRASRLMQAAVLGG\n"


@dataclass
class FakeRecord:
    """Duck-typed ``Bio.SeqRecord`` (the SDK never imports Biopython to read one)."""

    id: str
    seq: str
    description: str = ""


def test_plain_file_keeps_its_stem(tmp_path: Path) -> None:
    path = tmp_path / "proteins.faa"
    path.write_text(FASTA)
    upload = to_upload(str(path), ext="fasta")
    assert upload.name == "proteins.fasta"
    assert upload.path == path
    assert not upload.gzipped
    assert upload.size == len(FASTA)


def test_gzip_content_is_named_gz_whatever_the_file_is_called(tmp_path: Path) -> None:
    misnamed = tmp_path / "genome.fna"
    misnamed.write_bytes(gzip.compress(FASTA.encode()))
    assert to_upload(misnamed, ext="fasta").name == "genome.fasta.gz"


def test_plain_content_loses_a_wrong_gz_suffix(tmp_path: Path) -> None:
    misnamed = tmp_path / "genome.fa.gz"
    misnamed.write_text(FASTA)
    assert to_upload(misnamed, ext="fasta").name == "genome.fasta"


def test_text_and_bytes() -> None:
    upload = to_upload(FASTA, ext="fasta")
    assert upload.name == "input.fasta"
    assert upload.content == FASTA.encode()
    packed = to_upload(gzip.compress(FASTA.encode()), ext="fasta")
    assert packed.name == "input.fasta.gz" and packed.gzipped


def test_a_bare_sequence_becomes_one_record() -> None:
    assert to_upload("MNFPRASRLMQ", ext="fasta").content == b">query\nMNFPRASRLMQ\n"


def test_a_missing_path_is_not_mistaken_for_text() -> None:
    with pytest.raises(FileNotFoundError):
        to_upload("genomes/missing.faa", ext="fasta")
    with pytest.raises(FileNotFoundError):
        to_upload(Path("genomes/missing.faa"), ext="fasta")


def test_empty_inputs(tmp_path: Path) -> None:
    empty = tmp_path / "empty.faa"
    empty.write_text("")
    with pytest.raises(ValueError, match="empty"):
        to_upload(empty, ext="fasta")
    with pytest.raises(ValueError, match="empty"):
        to_upload("   ", ext="fasta")
    with pytest.raises(ValueError, match="empty"):
        to_upload(b"", ext="fasta")


def test_file_objects_take_their_name() -> None:
    handle = io.BytesIO(FASTA.encode())
    handle.name = "/data/my_genome.fa"
    upload = to_upload(handle, ext="fasta")
    assert upload.name == "my_genome.fasta"
    assert to_upload(io.StringIO(FASTA), ext="fasta").content == FASTA.encode()


def test_seqrecords() -> None:
    records = [FakeRecord("p1", "MKV", "p1 PETase"), FakeRecord("p2", "MKL")]
    assert to_upload(records, ext="fasta").content == b">p1 PETase\nMKV\n>p2\nMKL\n"
    assert to_upload(records[0], ext="fasta").content == b">p1 PETase\nMKV\n"


def test_real_biopython_records() -> None:
    pytest.importorskip("Bio")
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord

    record = SeqRecord(Seq("MKV"), id="p1", description="PETase")
    assert to_upload([record], ext="fasta").content == b">p1 PETase\nMKV\n"
    assert sequence_text(record) == "MKV"


def test_lists_that_are_not_records() -> None:
    with pytest.raises(TypeError, match="compare_genomes"):
        to_upload(["a.faa", "b.faa"], ext="fasta")
    with pytest.raises(TypeError, match="Cannot use int"):
        to_upload(42, ext="fasta")


def test_rows_become_tsv() -> None:
    upload = to_upload([("Ideonella", "sakaiensis"), ["Bacillus", None]], ext="tsv")
    assert upload.name == "input.tsv"
    assert upload.content == b"Ideonella\tsakaiensis\nBacillus\t\n"


@pytest.mark.parametrize(
    ("name", "stem"),
    [
        ("E. coli K-12.fna", "E._coli_K-12"),
        ("genome.fa.gz", "genome"),
        ("archive.tar.gz", "archive.tar"),
        ("...", "genome_1"),
        ("", "genome_1"),
    ],
)
def test_safe_stem_matches_the_server(name: str, stem: str) -> None:
    assert safe_stem(name) == stem


def test_genome_labels_default_to_file_stems(tmp_path: Path) -> None:
    a, b = tmp_path / "a.faa", tmp_path / "b.faa.gz"
    a.write_text(FASTA)
    b.write_bytes(gzip.compress(FASTA.encode()))
    assert [u.name for u in genome_uploads([a, b])] == ["a.fasta", "b.fasta.gz"]
    assert [u.name for u in genome_uploads([FASTA, FASTA])] == [
        "genome_1.fasta",
        "genome_2.fasta",
    ]


def test_genome_uploads_refuse_a_single_genome() -> None:
    with pytest.raises(TypeError, match="list"):
        genome_uploads(FASTA)
    with pytest.raises(TypeError, match="not both"):
        genome_uploads({"a": FASTA, "b": FASTA}, labels=["a", "b"])


def test_sequence_text() -> None:
    assert sequence_text(" MKV LLA \n") == "MKVLLA"
    assert sequence_text(">q desc\nMKV\nLLA\n") == "MKVLLA"
    with pytest.raises(ValueError, match="empty"):
        sequence_text(">q\n")
    with pytest.raises(TypeError):
        sequence_text(b"MKV")
