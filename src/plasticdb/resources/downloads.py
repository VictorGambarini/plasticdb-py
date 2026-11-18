from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from typing_extensions import Unpack

from .. import errors
from .._endpoints import Endpoint
from ..models import ReleaseArtefactFile
from ._base import Resource
from ._filters import (
    MicroorganismFilters,
    PaperFilters,
    PlasticFilters,
    ProteinFilters,
    VariantDownloadFilters,
    check,
)
from .proteins import DOWNLOAD_FASTA

PathLike = str | os.PathLike[str]
TableFormat = Literal["csv", "json"]

_TABLES = {
    (table, fmt): Endpoint("GET", f"/downloads/{table}.{fmt}")
    for table in ("proteins", "microorganisms", "plastics", "papers", "protein-variants")
    for fmt in ("csv", "json")
}
_TABLES[("proteins", "fasta")] = DOWNLOAD_FASTA
_DATASET = {
    "zip": Endpoint("GET", "/downloads/dataset.zip", live_only=True),
    "json": Endpoint("GET", "/downloads/dataset.json", live_only=True),
}
_SEARCH = {
    "zip": Endpoint("GET", "/downloads/search.zip", live_only=True),
    "json": Endpoint("GET", "/downloads/search.json", live_only=True),
}
# Release artefacts are addressed by version in the path, never pinned by ?release=.
_ARTEFACT = Endpoint(
    "GET", "/releases/{version}/artefacts/{path}", pinnable=False, raw_params=("path",)
)
_RELEASE_STRUCTURE = Endpoint(
    "GET", "/releases/{version}/structures/{structure_id}", pinnable=False
)

MANIFEST = "MANIFEST.json"


class Downloads(Resource):
    """Files: filtered tables, the whole dataset, and a release's checksummed artefacts.

    Every method streams to ``path`` (a file, or an existing directory to save
    the server's file name in) and returns the path written. A pinned client
    downloads that release's tables as published, and keeps them in
    ``cache_dir``: a second call is a local copy. Release artefacts are verified
    against the SHA-256 the release published and are always cacheable.
    """

    def _table(
        self, table: str, fmt: str, path: PathLike, params: dict[str, Any], name: str
    ) -> Path:
        endpoint = _TABLES.get((table, fmt))
        if endpoint is None:
            raise ValueError(f"{table} downloads come as {_formats(table)}, not {fmt!r}")
        return self._client._download(
            endpoint, path, params=params, default_name=f"plasticdb_{name}.{fmt}"
        )

    def proteins(
        self,
        path: PathLike,
        *,
        format: Literal["csv", "json", "fasta"] = "csv",
        **filters: Unpack[ProteinFilters],
    ) -> Path:
        """Proteins (and variants, ``kind=``) as CSV, JSON or FASTA.

        A past release answers its published table unfiltered (any filter is
        :class:`~plasticdb.errors.UnsupportedFilterError`); its CSV/JSON is the
        wild-type table, variants are :meth:`protein_variants`.
        """
        params = check(filters, ProteinFilters, "downloads.proteins")
        return self._table("proteins", format, path, params, "proteins")

    def microorganisms(
        self,
        path: PathLike,
        *,
        format: TableFormat = "csv",
        **filters: Unpack[MicroorganismFilters],
    ) -> Path:
        params = check(filters, MicroorganismFilters, "downloads.microorganisms")
        return self._table("microorganisms", format, path, params, "microorganisms")

    def plastics(
        self, path: PathLike, *, format: TableFormat = "csv", **filters: Unpack[PlasticFilters]
    ) -> Path:
        params = check(filters, PlasticFilters, "downloads.plastics")
        return self._table("plastics", format, path, params, "plastics")

    def papers(
        self, path: PathLike, *, format: TableFormat = "csv", **filters: Unpack[PaperFilters]
    ) -> Path:
        params = check(filters, PaperFilters, "downloads.papers")
        return self._table("papers", format, path, params, "papers")

    def protein_variants(
        self,
        path: PathLike,
        *,
        format: TableFormat = "csv",
        **filters: Unpack[VariantDownloadFilters],
    ) -> Path:
        """Engineered variants with their parent, mutations and engineering method."""
        params = check(filters, VariantDownloadFilters, "downloads.protein_variants")
        return self._table("protein-variants", format, path, params, "protein_variants")

    def dataset(
        self, path: PathLike, *, format: Literal["zip", "json"] = "zip", max_wait: float = 0
    ) -> Path:
        """Every public record: a ZIP of CSVs with a README and licence, or one JSON (live only).

        The ZIP is built on demand; while it is being built the server answers
        503 with ``Retry-After`` (:class:`~plasticdb.errors.ServiceUnavailableError`,
        ``.retry_after``). ``max_wait`` (seconds) keeps waiting that long for it.
        For a past release, use :meth:`release_file` (or its Figshare archive).
        """
        if format not in _DATASET:
            raise ValueError("format must be 'zip' or 'json'")
        deadline = time.monotonic() + max_wait
        while True:
            try:
                return self._client._download(
                    _DATASET[format], path, default_name=f"plasticdb_dataset.{format}"
                )
            except errors.ServiceUnavailableError as exc:
                wait = exc.retry_after or 0.0
                if isinstance(exc, errors.ReleaseError) or not wait:
                    raise
                if time.monotonic() + wait > deadline:
                    raise
                self._client._sleep(wait)

    def search(self, path: PathLike, q: str, *, format: Literal["json", "zip"] = "json") -> Path:
        """Every record matching a search, as JSON or a ZIP of CSVs (live only)."""
        if format not in _SEARCH:
            raise ValueError("format must be 'json' or 'zip'")
        return self._client._download(
            _SEARCH[format], path, params={"q": q}, default_name=f"plasticdb_search.{format}"
        )

    # ── release artefacts ────────────────────────────────────────────────────

    def _version(self, release: str | None) -> str:
        version = release or self._client.release or self._client.releases.current().version
        if not version:
            raise errors.ReleaseError(
                "The server has no dataset release yet, so there are no release artefacts",
                reason="no_release",
            )
        return version

    def artefacts(self, release: str | None = None) -> list[ReleaseArtefactFile]:
        """The files a release published (``release`` defaults to the pinned or current one)."""
        version = self._version(release)
        record = self._client.releases.get(version)
        if record.artefacts is None:
            raise errors.ReleaseNoArtefactsError(
                f"Release {version} has no artefacts", release=version, reason="no_artefacts"
            )
        return list(record.artefacts.files)

    def release_file(self, file: str, path: PathLike, *, release: str | None = None) -> Path:
        """One artefact of a release (``parquet/proteins.parquet``, ``reference/PlasticDB.dmnd``…).

        The file is checked against the SHA-256 the release published; a mismatch
        raises :class:`~plasticdb.errors.ChecksumError` and nothing is kept.
        """
        version = self._version(release)
        record = self._client.releases.get(version)
        artefacts = record.artefacts
        if artefacts is None:
            raise errors.ReleaseNoArtefactsError(
                f"Release {version} has no artefacts", release=version, reason="no_artefacts"
            )
        sha: str | None = None
        if file == MANIFEST:
            sha = artefacts.manifest_sha256
        else:
            entry = next((f for f in artefacts.files if f.path == file), None)
            sha = entry.sha256 if entry else _manifest_sha(self.manifest(version), file)
        return self._client._download(
            _ARTEFACT,
            path,
            sha256=sha,
            immutable_release=version,
            default_name=Path(file).name,
            version=version,
            path=file,
        )

    def manifest(self, release: str | None = None) -> dict[str, Any]:
        """A release's ``MANIFEST.json``: every artefact with its SHA-256, size and licence."""
        version = self._version(release)
        cache = self._client.cache
        folder = cache.directory / ".manifests" if cache is not None else None
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            target = self.release_file(MANIFEST, folder / f"{version}.json", release=version)
            data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
            return data
        with tempfile.TemporaryDirectory(prefix="plasticdb-") as tmp:
            target = self.release_file(MANIFEST, Path(tmp) / MANIFEST, release=version)
            data = json.loads(target.read_text(encoding="utf-8"))
            return data

    def structure(self, structure_id: int, path: PathLike, *, release: str | None = None) -> Path:
        """One structure's coordinates as published by a release (gzipped PDB).

        ``structure_id`` is a :class:`~plasticdb.models.Structure` ``id``. The file
        is verified against the release manifest. Each file's licence follows its
        source (``manifest()["structures"]``).
        """
        version = self._version(release)
        sha = _manifest_sha(self.manifest(version), f"structures/{int(structure_id)}.pdb.gz")
        return self._client._download(
            _RELEASE_STRUCTURE,
            path,
            sha256=sha,
            immutable_release=version,
            default_name=f"{int(structure_id)}.pdb.gz",
            version=version,
            structure_id=int(structure_id),
        )


def _manifest_sha(manifest: dict[str, Any], file: str) -> str | None:
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and entry.get("path") == file:
            sha = entry.get("sha256")
            return sha if isinstance(sha, str) else None
    raise errors.NotFoundError(f"{file} is not in the release manifest")


def _formats(table: str) -> str:
    return " or ".join(repr(fmt) for (t, fmt) in _TABLES if t == table)
