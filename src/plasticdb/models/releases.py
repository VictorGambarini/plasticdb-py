from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ._base import PlasticDBModel

DATA_LICENSE = "CC BY 4.0"
DATA_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PAPER_CITATION = (
    "Gambarini V, Pantos O, Kingsbury JM, Weaver L, Handley KM, Lear G (2022). "
    "PlasticDB: a database of microorganisms and proteins linked to plastic "
    "biodegradation. Database, 2022:baac008."
)
PAPER_DOI = "10.1093/database/baac008"


class CurrentRelease(PlasticDBModel):
    """``GET /releases/current``. Every release field is ``None`` before the first release."""

    version: str | None = None
    released_at: datetime | None = None
    doi: str | None = None
    snapshot_release_label: str | None = None
    citation: str | None = None
    license: str = DATA_LICENSE
    license_spdx: str = "CC-BY-4.0"
    license_url: str = DATA_LICENSE_URL
    paper_citation: str = PAPER_CITATION
    paper_doi: str = PAPER_DOI


class ReleaseStatus(PlasticDBModel):
    """``GET /releases/status``: whether a release is being cut right now."""

    running: bool
    version: str | None = None
    started_at: datetime | None = None


class ReleaseArtefactFile(PlasticDBModel):
    path: str
    kind: str
    sha256: str
    size: int
    download_url: str
    rows: int | None = None
    table: str | None = None


class ReleaseToolVersion(PlasticDBModel):
    pinned: str | None = None
    reported: str | None = None
    status: str | None = None


class ReleaseArtefacts(PlasticDBModel):
    """The immutable, checksummed files a release published."""

    release: str
    manifest_url: str
    files: list[ReleaseArtefactFile] = Field(default_factory=list)
    tools: dict[str, ReleaseToolVersion] = Field(default_factory=dict)
    generated_at: str | None = None
    manifest_sha256: str | None = None
    inherited_from: str | None = None
    schema_id: str | None = None
    hmm_library_source: str | None = None
    pathways: dict[str, Any] | None = None
    structures: dict[str, Any] | None = None


class Release(PlasticDBModel):
    """One dataset release (``GET /releases`` item, ``GET /releases/{version}``)."""

    version: str
    promoted_proteins: int
    promoted_microorganisms: int
    released_at: datetime | None = None
    doi: str | None = None
    entity_counts: dict[str, int] | None = None
    artefacts: ReleaseArtefacts | None = None
