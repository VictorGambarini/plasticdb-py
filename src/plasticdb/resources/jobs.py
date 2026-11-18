"""Jobs: submit an analysis, wait for it, read or download its results, share it."""

from __future__ import annotations

import csv
import io
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .. import errors
from .._endpoints import Endpoint
from .._inputs import Upload, genome_uploads, sequence_text, to_upload
from .._pagination import Collection, ResultTable
from .._response import APIResponse, new_idempotency_key
from ..models.jobs import (
    FINAL_STATUSES,
    AnnotatedTaxon,
    GenomeComparison,
    JobHit,
    JobInfo,
    JobShare,
    JobShareCreated,
    PathwayStep,
    SequenceSearchResult,
    TaxonomyTree,
)
from ._base import Resource

if TYPE_CHECKING:
    from .._client import Client

log = logging.getLogger("plasticdb")

PathLike = str | os.PathLike[str]
BlastType = Literal["blastp", "blastx"]
OrganismType = Literal["no_signalp", "arch", "gram+", "gram-", "euk"]
ThresholdMode = Literal["gathering", "evalue"]

# Submissions take the client's (or the call's) release; job reads never do: a
# job answers with the release it ran against (and says so in its headers).
# Creates carry an Idempotency-Key, so they are retried like reads.
_SUBMIT = Endpoint("POST", "/jobs", idempotency=True)
_TREE_BUILD = Endpoint("POST", "/tree/build", idempotency=True)
_SEARCH_SEQUENCE = Endpoint("POST", "/bioinformatics/search-sequence", retry_safe=True)
_LIST = Endpoint("GET", "/jobs", pinnable=False)
_GET = Endpoint("GET", "/jobs/{job_id}", pinnable=False)
_RESULTS = Endpoint("GET", "/jobs/{job_id}/results", pinnable=False)
_DOWNLOAD = Endpoint("GET", "/jobs/{job_id}/download", pinnable=False)
_COMPARISON = Endpoint("GET", "/jobs/{job_id}/comparison", pinnable=False)
_SHARES = Endpoint("GET", "/jobs/{job_id}/shares", pinnable=False)
_SHARE_CREATE = Endpoint("POST", "/jobs/{job_id}/shares", pinnable=False, idempotency=True)
_SHARE_REVOKE = Endpoint("DELETE", "/jobs/{job_id}/shares/{share_id}", pinnable=False)
_SHARED_GET = Endpoint("GET", "/jobs/shared/{token}", pinnable=False)
_SHARED_RESULTS = Endpoint("GET", "/jobs/shared/{token}/results", pinnable=False)
_SHARED_DOWNLOAD = Endpoint("GET", "/jobs/shared/{token}/download", pinnable=False)
_SHARED_COMPARISON = Endpoint("GET", "/jobs/shared/{token}/comparison", pinnable=False)

#: The job types ``POST /jobs`` runs, and the form fields each one takes.
_SEARCH = ("blast_type", "organism_type", "evalue", "pident", "include_variants")
_VIEW = ("min_qcov", "min_scov", "min_bitscore")
JOB_PARAMS: dict[str, tuple[str, ...]] = {
    "annotate_gene": _SEARCH + _VIEW,
    "annotate_genome": _SEARCH + _VIEW,
    "compare_genomes": _SEARCH + _VIEW,
    "annotate_list": ("genus_column", "species_column", "field_separator"),
    "pathway_analysis": (
        "target_plastic",
        "blast_type",
        "evalue",
        "pident",
        "min_step_identity",
        "max_step_evalue",
        "min_reference_coverage",
        "min_query_coverage",
    ),
    "hmm_screen": ("target_polymers", "threshold_mode", "evalue"),
}
#: ``POST /tree/build``'s filters (``class`` is spelled ``class_``).
TREE_FILTERS = (
    "microorganism_ids",
    "q",
    "name",
    "plastic",
    "plastic_id",
    "include_subtypes",
    "tax_id",
    "temp_class",
    "environment",
    "location",
    "year",
    "min_year",
    "max_year",
    "lineage",
    "domain",
    "kingdom",
    "phylum",
    "class_",
    "order",
    "family",
    "genus",
    "evidence_method",
    "substrate_form",
    "include_negative",
    "polarity",
    "evidence_scope",
    "sole_carbon_source",
    "aerobicity",
    "updated_since",
)
JOB_TYPES = (*JOB_PARAMS, "build_tree")
HIT_JOB_TYPES = frozenset({"annotate_gene", "annotate_genome", "compare_genomes", "hmm_screen"})

#: Polls and reads leave the hits out; :meth:`Job.hits` pages them.
_LEAN = {"include": "none"}

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_MAX_POLL_INTERVAL = 30.0


@dataclass(frozen=True)
class JobProvenance:
    """What a job ran against: report this with its results.

    ``release`` is the dataset release the job ran against (``release_source``
    ``snapshot`` for a past release's archived databases, ``live`` for the
    current one), as the job record and its ``X-PlasticDB-Release`` headers say;
    ``reference_sha256`` the SHA-256 of the DIAMOND database or HMM library
    searched, ``tool_versions`` e.g. ``{"diamond": "2.1.9"}``. ``record`` is
    the server's full provenance record (``result_metadata["provenance"]``).
    """

    job_id: str
    job_type: str
    release: str | None
    release_source: str | None
    reference_sha256: str | None
    tool_versions: dict[str, str] | None
    app_version: str | None = None
    record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Job:
    """A submitted job. ``status`` is the last one fetched; :meth:`refresh` asks again.

    ``wait()`` polls until the job is ``done`` (or raises
    :class:`~plasticdb.errors.JobFailedError` on ``error``); then ``results()``
    gives the typed results and ``download(path)`` the server's results file.
    """

    def __init__(
        self,
        client: Client,
        info: JobInfo,
        *,
        token: str | None = None,
        response: APIResponse | None = None,
    ) -> None:
        self._client = client
        self._info = info
        self._token = token
        self._response = response
        self._replayed = response.replayed if response is not None else False

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def job_type(self) -> str:
        return self._info.job_type

    @property
    def status(self) -> str:
        """``waiting``, ``running``, ``done`` or ``error`` (as last fetched)."""
        return self._info.status

    @property
    def info(self) -> JobInfo:
        """Everything the server said about the job, as last fetched."""
        return self._info

    @property
    def finished(self) -> bool:
        return self._info.status in FINAL_STATUSES

    @property
    def done(self) -> bool:
        return self._info.status == "done"

    @property
    def failed(self) -> bool:
        return self._info.status == "error"

    @property
    def error_message(self) -> str | None:
        return self._info.error_message

    @property
    def shared(self) -> bool:
        """True for a job opened from a share link (read-only)."""
        return self._token is not None

    @property
    def release(self) -> str | None:
        """The dataset release the job ran (or runs) against."""
        if self._info.release:
            return self._info.release
        return self._response.release if self._response is not None else None

    @property
    def replayed(self) -> bool:
        """True when the submission's ``Idempotency-Key`` returned an existing job.

        A retried submission whose first attempt reached the server gets that job
        back (in its current state) instead of a duplicate.
        """
        return self._replayed

    @property
    def provenance(self) -> JobProvenance:
        """The release, reference database checksum and tool versions the job used."""
        info = self._info
        meta = info.result_metadata or {}
        record = meta.get("provenance")
        record = dict(record) if isinstance(record, dict) else {}
        app = record.get("app_version")
        read = self._response
        return JobProvenance(
            job_id=info.id,
            job_type=info.job_type,
            release=info.release or (read.release if read else None),
            release_source=info.release_source or (read.release_source if read else None),
            reference_sha256=info.reference_sha256,
            tool_versions=dict(info.tool_versions) if info.tool_versions else None,
            app_version=app if isinstance(app, str) else None,
            record=record,
        )

    def __repr__(self) -> str:
        release = f" release={self._info.release}" if self._info.release else ""
        return f"<plasticdb.Job {self.id} {self.job_type} {self.status}{release}>"

    # ── polling ──────────────────────────────────────────────────────────────

    def refresh(self) -> Job:
        """Fetch the job again (status, summary, provenance; not the hits). Returns ``self``."""
        endpoint = _SHARED_GET if self._token is not None else _GET
        response = self._client._request(endpoint, params=_LEAN, **self._ref())
        self._info = JobInfo.model_validate(response.data)
        self._response = response
        return self

    def wait(
        self,
        timeout: float | None = None,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_poll_interval: float = DEFAULT_MAX_POLL_INTERVAL,
        raise_on_error: bool = True,
    ) -> Job:
        """Poll until the job finishes; return ``self``.

        Polls every ``poll_interval`` seconds, backing off by half each time up to
        ``max_poll_interval``; a 429 waits for the server's ``Retry-After``.
        ``timeout`` (seconds, default none) raises
        :class:`~plasticdb.errors.JobTimeoutError`; the job keeps running and you
        can ``wait()`` again. A failed job raises
        :class:`~plasticdb.errors.JobFailedError` unless ``raise_on_error=False``.
        """
        if poll_interval <= 0 or max_poll_interval <= 0:
            raise ValueError("poll intervals must be positive")
        deadline = None if timeout is None else time.monotonic() + timeout
        interval = poll_interval
        first = True
        while True:
            if not (first and self.finished):
                try:
                    self.refresh()
                except errors.RateLimitError as exc:
                    wait = exc.retry_after or max_poll_interval
                    self._sleep_until(wait, deadline, exc)
                    continue
            first = False
            if self.finished:
                break
            self._sleep_until(interval, deadline, None)
            interval = min(interval * 1.5, max_poll_interval)
        if raise_on_error and self.failed:
            reason = self.error_message or "no reason given"
            raise errors.JobFailedError(
                f"Job {self.id} ({self.job_type}) failed: {reason}", job=self
            )
        return self

    def _sleep_until(
        self, seconds: float, deadline: float | None, cause: BaseException | None
    ) -> None:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise errors.JobTimeoutError(
                    f"Job {self.id} is still {self.status} after the timeout; it keeps running "
                    "on the server (call wait() again, or client.jobs.get(id) later)",
                    job=self,
                ) from cause
            seconds = min(seconds, left)
        self._client._sleep(seconds)

    def _require_done(self) -> None:
        if self.failed:
            raise errors.JobFailedError(
                f"Job {self.id} failed: {self.error_message or 'no reason given'}", job=self
            )
        if not self.done:
            self.refresh()
        if self.failed:
            raise errors.JobFailedError(
                f"Job {self.id} failed: {self.error_message or 'no reason given'}", job=self
            )
        if not self.done:
            raise errors.JobError(
                f"Job {self.id} is still {self.status}: call job.wait() first", job=self
            )

    # ── results ──────────────────────────────────────────────────────────────

    def results(self) -> ResultTable[Any] | TaxonomyTree:
        """The results, typed by job type.

        * annotate_gene / annotate_genome / compare_genomes / hmm_screen:
          :meth:`hits` (every stored hit, paged from the server; the website's
          and ``download()``'s default view also hides hits under 70 % subject
          coverage).
        * pathway_analysis: :meth:`steps`.
        * annotate_list: :meth:`taxa`.
        * build_tree: :meth:`tree`.
        """
        kind = self.job_type
        if kind == "pathway_analysis":
            return self.steps()
        if kind == "annotate_list":
            return self.taxa()
        if kind == "build_tree":
            return self.tree()
        return self.hits()

    def hits(self, *, limit: int | None = None) -> ResultTable[JobHit]:
        """Every hit (or the first ``limit``), in the server's order (best first per query).

        Fetched page by page from ``GET /jobs/{id}/results`` (500 a request), so
        a genome with tens of thousands of hits never arrives as one response.
        """
        self._require_done()
        return ResultTable(self.iter_hits(limit=limit).all(), name=f"{self.job_type} hits")

    def iter_hits(
        self, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[JobHit]:
        """The hits as a lazy, paged collection (iterate it, or ``to_pandas()``)."""
        endpoint = _SHARED_RESULTS if self._token else _RESULTS
        return Collection(
            self._client,
            endpoint,
            JobHit,
            path_params=self._ref(),
            page_size=page_size,
            limit=limit,
        )

    def steps(self) -> ResultTable[PathwayStep]:
        """A pathway analysis: one row per pathway step, ``present`` and the best hit."""
        self._require_done()
        table = (self._info.result_metadata or {}).get("step_table") or []
        return ResultTable([PathwayStep.model_validate(row) for row in table], name="pathway steps")

    def taxa(self) -> ResultTable[AnnotatedTaxon]:
        """An annotate_list job: your table's rows with PlasticDB's columns added.

        Read from the results file (its ``#`` provenance lines are skipped); the
        job's summary is ``info.result_metadata["taxa"]``.
        """
        self._require_done()
        endpoint, path = self._download_target()
        data = self._client._request(endpoint, **path).data
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t")
        return ResultTable(
            [AnnotatedTaxon.model_validate(dict(row)) for row in reader], name="taxa"
        )

    def tree(self) -> TaxonomyTree:
        """A build_tree job: the Newick cladogram and the organism x plastic matrix."""
        self._require_done()
        if self._info.result_newick is None:
            self.refresh()
        meta = dict(self._info.result_metadata or {})
        meta.pop("provenance", None)
        return TaxonomyTree.model_validate({**meta, "newick": self._info.result_newick})

    def comparison(
        self,
        *,
        scov: float | None = None,
        qcov: float | None = None,
        bitscore: float | None = None,
        all_hits: bool = False,
    ) -> GenomeComparison:
        """A compare_genomes job's genome x plastic matrix for a view of the hits.

        With no arguments, the job's default view (hits covering at least 70 % of
        the reference); ``all_hits=True`` counts every hit; ``0`` turns one
        threshold off.
        """
        endpoint = _SHARED_COMPARISON if self._token else _COMPARISON
        params = {"format": "json", **_view(scov, qcov, bitscore, all_hits)}
        data = self._client._request(endpoint, params=params, **self._ref()).data
        matrix = data.get("comparison") if isinstance(data, dict) else None
        return GenomeComparison.model_validate(matrix or {})

    def download(
        self,
        path: PathLike = ".",
        *,
        scov: float | None = None,
        qcov: float | None = None,
        bitscore: float | None = None,
        all_hits: bool = False,
    ) -> Path:
        """Stream the results file to ``path`` (a file, or a directory to use the server's name).

        A TSV with ``#`` provenance lines for most job types; a ZIP for
        compare_genomes (per-genome hit tables, the comparison matrix and
        ``provenance.json``). Hit tables apply the job's default view unless you
        pass thresholds (``0`` turns one off) or ``all_hits=True``. Returns the
        path written.
        """
        expired = self._info.results_expired_at
        if expired is not None:
            raise errors.ResultsExpiredError(
                f"Job {self.id}'s result files were deleted on {expired:%Y-%m-%d} under the "
                "retention policy (its hits are still readable: job.hits())",
                results_expired_at=expired,
                status_code=410,
                body={"reason": "expired"},
            )
        endpoint, ref = self._download_target()
        return self._client._download(
            endpoint,
            path,
            params=_view(scov, qcov, bitscore, all_hits),
            default_name=f"plasticdb_{self.job_type}_{self.id[:8]}.tsv",
            **ref,
        )

    def _ref(self) -> dict[str, Any]:
        return {"token": self._token} if self._token else {"job_id": self.id}

    def _download_target(self) -> tuple[Endpoint, dict[str, Any]]:
        return (_SHARED_DOWNLOAD if self._token else _DOWNLOAD), self._ref()

    # ── sharing ──────────────────────────────────────────────────────────────

    def _owner_only(self) -> None:
        if self._token is not None:
            raise errors.PermissionDenied("A job opened from a share link is read-only")
        self._client.require_api_key("Share links")

    def share(
        self, *, expires_in_days: int | None = None, idempotency_key: str | None = None
    ) -> JobShareCreated:
        """Create a read-only link to this job's results (owner only; needs the API key).

        The token is in the answer **once**; keep ``.url``. ``expires_in_days``
        (1-365) defaults to a link that lasts as long as the job. The request is
        sent with an ``Idempotency-Key`` (``idempotency_key``, or a new one), so
        a retry returns the same link rather than a second one.
        """
        self._owner_only()
        if expires_in_days is not None and not 1 <= expires_in_days <= 365:
            raise ValueError("expires_in_days must be between 1 and 365")
        body = {"expires_in_days": expires_in_days} if expires_in_days is not None else None
        data = self._client._request(
            _SHARE_CREATE, json=body, idempotency_key=idempotency_key, job_id=self.id
        ).data
        created = JobShareCreated.model_validate(data)
        url = self._client._root_url + created.path
        return created.model_copy(update={"url": url})

    def shares(self) -> list[JobShare]:
        """The job's share links, newest first (never their tokens)."""
        self._owner_only()
        data = self._client._request(_SHARES, job_id=self.id).data or []
        return [JobShare.model_validate(item) for item in data]

    def unshare(self, share: JobShare | int) -> None:
        """Revoke a share link at once (its next request is a 404)."""
        self._owner_only()
        share_id = share.id if isinstance(share, JobShare) else int(share)
        self._client._request(_SHARE_REVOKE, job_id=self.id, share_id=share_id)


def _view(
    scov: float | None, qcov: float | None, bitscore: float | None, all_hits: bool
) -> dict[str, Any]:
    return {
        "scov": scov,
        "qcov": qcov,
        "bitscore": bitscore,
        "all": True if all_hits else None,
    }


def _form_value(value: Any) -> str | list[str]:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return [str(v) for v in value]
    return str(value)


def _check(params: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise TypeError(
            f"{where}() got unexpected parameter(s) {unknown}; valid: {sorted(allowed)}"
        )


def _normalise_type(job_type: str) -> str:
    name = job_type.strip().lower().replace("-", "_")
    if name in {"taxonomy_tree", "tree"}:
        name = "build_tree"
    return name


class Jobs(Resource):
    """Run PlasticDB's analyses: needs an API key (``Client(api_key=)`` / ``$PLASTICDB_API_KEY``).

    A pinned client (or ``release=`` on the call) runs the job against that
    release's archived reference databases; a job type or option a release
    cannot answer raises :class:`~plasticdb.errors.UnsupportedFilterError`.
    Submissions are rate-limited (30 an hour). Each submission is sent with an
    ``Idempotency-Key`` (a new UUID, or the ``idempotency_key=`` you pass), so it
    is retried on a timeout or a 5xx: if an attempt did reach the server, the
    retry gets that job back (``job.replayed``) instead of a duplicate. Reuse a
    key (within 24 hours) to recover a submission whose answer you lost; a key
    sent with a different request raises
    :class:`~plasticdb.errors.IdempotencyKeyReusedError`.
    """

    # ── reading ──────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Job:
        """A job by id (yours, or one submitted without an account)."""
        response = self._client._request(_GET, params=_LEAN, job_id=job_id)
        return Job(self._client, JobInfo.model_validate(response.data), response=response)

    def shared(self, token_or_url: str) -> Job:
        """A job someone shared with you, from its link or token (no API key needed)."""
        token = token_or_url.strip().rstrip("/").rsplit("/", 1)[-1]
        if "?" in token:
            token = token.split("?", 1)[0]
        if not token:
            raise ValueError("No share token given")
        response = self._client._request(_SHARED_GET, params=_LEAN, token=token)
        return Job(
            self._client, JobInfo.model_validate(response.data), token=token, response=response
        )

    # ── submitting ───────────────────────────────────────────────────────────

    def submit(
        self,
        job_type: str,
        input: Any = None,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        **params: Any,
    ) -> Job:
        """Submit a job by its server name (``annotate_genome``, ``hmm_screen``, ``build_tree``…).

        ``input`` is what the job reads (see the typed helpers, which document
        each job type's parameters); ``build_tree`` takes filters instead.
        Returns the :class:`Job` at once; call ``.wait()`` for the results.
        ``idempotency_key`` (any 1-255 printable ASCII characters; default a new
        UUID) identifies this submission: resending it returns the same job.
        """
        kind = _normalise_type(job_type)
        if kind == "build_tree":
            if input is not None:
                raise TypeError("build_tree takes filters, not an input file")
            return self.build_tree(release=release, idempotency_key=idempotency_key, **params)
        if kind == "search_sequence":
            raise ValueError("search_sequence is synchronous: use client.jobs.search_sequence()")
        if kind not in JOB_PARAMS:
            raise ValueError(f"Unknown job type {job_type!r}; valid: {sorted(JOB_TYPES)}")
        labels = params.pop("labels", None) if kind == "compare_genomes" else None
        _check(params, JOB_PARAMS[kind], f"jobs.submit({kind!r})")
        self._client.require_api_key()
        if input is None:
            raise TypeError(f"{kind} needs an input")
        if kind == "compare_genomes":
            uploads = genome_uploads(input, labels)
        elif kind == "annotate_list":
            uploads = [to_upload(input, ext="tsv", kind="taxa table")]
        else:
            uploads = [to_upload(input, ext="fasta")]
        return self._post_job(kind, uploads, params, release, idempotency_key)

    def _post_job(
        self,
        kind: str,
        uploads: list[Upload],
        params: Mapping[str, Any],
        release: str | None,
        idempotency_key: str | None,
    ) -> Job:
        field_name = "files" if kind == "compare_genomes" else "file"
        content_type = "text/tab-separated-values" if kind == "annotate_list" else "text/plain"

        def files() -> list[tuple[str, tuple[str, Any, str]]]:
            return [
                (
                    field_name,
                    (u.name, u.open(), "application/gzip" if u.gzipped else content_type),
                )
                for u in uploads
            ]

        form: dict[str, Any] = {"job_type": kind}
        for name, value in params.items():
            if value is not None:
                form[name] = _form_value(value)
        query = {"release": release} if release else None
        key = idempotency_key or new_idempotency_key()
        return self._created(
            key,
            lambda: self._client._request(
                _SUBMIT, params=query, data=form, files=files, idempotency_key=key
            ),
        )

    def _created(self, key: str, send: Callable[[], APIResponse]) -> Job:
        try:
            response = send()
        except (errors.APIConnectionError, errors.ServerError) as exc:
            exc.args = (
                f"{exc} The submission may have reached the server: submit it again with "
                f"idempotency_key={key!r} (within 24 hours) to get that job back rather than "
                "a duplicate.",
            )
            exc.message = str(exc.args[0])
            raise
        job = Job(self._client, JobInfo.model_validate(response.data), response=response)
        if job.replayed:
            log.info("PlasticDB: idempotency key %s returned the existing job %s", key, job.id)
        return job

    def annotate_gene(
        self,
        fasta: Any,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        blast_type: BlastType | None = None,
        organism_type: OrganismType | None = None,
        evalue: float | None = None,
        pident: int | None = None,
        include_variants: bool | None = None,
        min_qcov: float | None = None,
        min_scov: float | None = None,
        min_bitscore: float | None = None,
    ) -> Job:
        """Search a few genes/proteins against PlasticDB's proteins (DIAMOND).

        ``fasta`` is a path (gzip or plain), FASTA text, bytes, a file object,
        a ``Bio.SeqRecord`` or a list of them. ``blast_type="blastx"`` for
        nucleotide input. ``evalue`` (default 1e-5) and ``pident`` (30) bound the
        search; ``include_variants`` also searches engineered variants;
        ``min_qcov`` / ``min_scov`` (default 70) / ``min_bitscore`` set the
        default view of the results file and page (every hit is stored).
        ``organism_type`` turns on signal-peptide prediction (``gram-``,
        ``gram+``, ``arch``, ``euk``). ``idempotency_key``: see :meth:`submit`.
        """
        return self.submit(
            "annotate_gene",
            fasta,
            release=release,
            idempotency_key=idempotency_key,
            **_given(locals()),
        )

    def annotate_genome(
        self,
        fasta: Any,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        blast_type: BlastType | None = None,
        organism_type: OrganismType | None = None,
        evalue: float | None = None,
        pident: int | None = None,
        include_variants: bool | None = None,
        min_qcov: float | None = None,
        min_scov: float | None = None,
        min_bitscore: float | None = None,
    ) -> Job:
        """Annotate a whole genome's proteins (or, with ``blastx``, its contigs).

        Same inputs and parameters as :meth:`annotate_gene`.
        """
        return self.submit(
            "annotate_genome",
            fasta,
            release=release,
            idempotency_key=idempotency_key,
            **_given(locals()),
        )

    def compare_genomes(
        self,
        genomes: Any,
        *,
        labels: Sequence[str] | None = None,
        release: str | None = None,
        idempotency_key: str | None = None,
        blast_type: BlastType | None = None,
        organism_type: OrganismType | None = None,
        evalue: float | None = None,
        pident: int | None = None,
        include_variants: bool | None = None,
        min_qcov: float | None = None,
        min_scov: float | None = None,
        min_bitscore: float | None = None,
    ) -> Job:
        """Search two or more genomes (up to the server's limit, 20) and compare them.

        ``genomes`` is a list of genome inputs (each as for :meth:`annotate_gene`)
        or a ``{label: genome}`` mapping. A genome's label names its rows in the
        results: ``labels=``, the mapping key, else the file name. Results:
        ``job.hits()`` (``source_label`` is the genome) and ``job.comparison()``.
        """
        kwargs = _given(locals())
        kwargs.pop("labels", None)
        return self.submit(
            "compare_genomes",
            genomes,
            release=release,
            idempotency_key=idempotency_key,
            **kwargs,
            **_labels(labels),
        )

    def annotate_list(
        self,
        table: Any,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        genus_column: int | None = None,
        species_column: int | None = None,
        field_separator: Literal["tab", "comma", "space"] | None = None,
    ) -> Job:
        """Match a table of taxa (genus, species) against PlasticDB's organisms.

        ``table`` is a path, text, bytes, or a list of rows (sent tab-separated).
        ``genus_column`` / ``species_column`` are 1-based and default to 7 and 8
        (the server's layout), so pass them for any other table.
        ``field_separator`` is ``tab`` (default), ``comma`` or ``space``; the job
        records all three. Results: ``job.taxa()``.
        """
        return self.submit(
            "annotate_list",
            table,
            release=release,
            idempotency_key=idempotency_key,
            **_given(locals()),
        )

    def pathway_analysis(
        self,
        fasta: Any,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        target_plastic: str | None = None,
        blast_type: BlastType | None = None,
        evalue: float | None = None,
        pident: int | None = None,
        min_step_identity: float | None = None,
        max_step_evalue: float | None = None,
        min_reference_coverage: float | None = None,
        min_query_coverage: float | None = None,
    ) -> Job:
        """Map proteins onto a plastic's degradation pathway: which steps they cover.

        ``target_plastic`` names the plastic whose pathway is mapped (default
        ``PET``; the job records it in ``result_metadata``). The ``min_step_*`` /
        ``max_step_evalue`` / ``min_*_coverage`` thresholds decide when a hit
        calls a step present (defaults 40 %, 1e-10, 70 %, 50 %). Results:
        ``job.steps()``.
        """
        return self.submit(
            "pathway_analysis",
            fasta,
            release=release,
            idempotency_key=idempotency_key,
            **_given(locals()),
        )

    def hmm_screen(
        self,
        fasta: Any,
        *,
        release: str | None = None,
        idempotency_key: str | None = None,
        target_polymers: Sequence[str] | None = None,
        threshold_mode: ThresholdMode | None = None,
        evalue: float | None = None,
    ) -> Job:
        """Screen proteins against PlasticDB's polymer-specific HMM profiles (hmmsearch).

        ``threshold_mode="gathering"`` (default) uses each profile's calibrated
        cut-off and ignores ``evalue``; ``"evalue"`` uses ``evalue`` only.
        ``target_polymers`` (e.g. ``["PET", "PLA"]``) limits the polymer summary.
        Results: ``job.hits()`` and ``info.result_metadata["polymer_hits"]``.
        """
        kwargs = _given(locals())
        if target_polymers is not None:
            kwargs["target_polymers"] = list(target_polymers)
        return self.submit(
            "hmm_screen", fasta, release=release, idempotency_key=idempotency_key, **kwargs
        )

    def build_tree(
        self, *, release: str | None = None, idempotency_key: str | None = None, **filters: Any
    ) -> Job:
        """A taxonomy tree (NCBI-taxonomy cladogram) of organisms, by filter or id list.

        Filters are ``/microorganisms``'s (``plastic="PET"``, ``genus=``,
        ``class_=``, ``polarity=``, …) or ``microorganism_ids=[...]``. A pinned
        client (or ``release=``) builds it from that release. Results:
        ``job.tree()`` (Newick + organism x plastic matrix).
        """
        _check(filters, TREE_FILTERS, "jobs.build_tree")
        self._client.require_api_key()
        body: dict[str, Any] = {}
        for name, value in filters.items():
            if value is None:
                continue
            key = "class" if name == "class_" else name
            body[key] = value.isoformat() if hasattr(value, "isoformat") else value
        if "microorganism_ids" in body:
            body["microorganism_ids"] = [int(i) for i in body["microorganism_ids"]]
        query = {"release": release} if release else None
        key = idempotency_key or new_idempotency_key()
        return self._created(
            key,
            lambda: self._client._request(
                _TREE_BUILD, params=query, json=body, idempotency_key=key
            ),
        )

    def search_sequence(
        self,
        sequence: Any,
        *,
        release: str | None = None,
        blast_type: BlastType | None = None,
        evalue: float | None = None,
        pident: int | None = None,
        include_variants: bool | None = None,
    ) -> SequenceSearchResult:
        """Search one sequence now (synchronous, no job): DIAMOND hits with provenance.

        ``sequence`` is residues, one-record FASTA text or a ``Bio.SeqRecord``.
        The server gives it 15 seconds (past that,
        :class:`~plasticdb.errors.ServerError` with ``reason == "timeout"``); for
        more sequences use :meth:`annotate_gene`. While the reference database is
        unavailable it raises :class:`~plasticdb.errors.ServiceUnavailableError`
        (``retry_after``).
        """
        self._client.require_api_key("Sequence searches")
        params: dict[str, Any] = {"sequence": sequence_text(sequence), "release": release}
        params.update(
            {
                k: v
                for k, v in {
                    "blast_type": blast_type,
                    "evalue": evalue,
                    "pident": pident,
                    "include_variants": include_variants,
                }.items()
                if v is not None
            }
        )
        data = self._client._request(_SEARCH_SEQUENCE, params=params).data
        return SequenceSearchResult.model_validate(data)

    # Last, so the method does not shadow the builtin in the annotations above.
    def list(
        self, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[JobInfo]:
        """Your jobs, newest first (a lazy collection; ``to_pandas()`` for an overview).

        Items are :class:`~plasticdb.models.JobInfo`; ``client.jobs.get(item.id)``
        gives the :class:`Job` to wait on or download.
        """
        self._client.require_api_key("Listing jobs")
        return Collection(self._client, _LIST, JobInfo, page_size=page_size, limit=limit)


def _given(scope: dict[str, Any]) -> dict[str, Any]:
    """A helper's keyword arguments that were given (not ``None``), minus the input."""
    skip = {"self", "release", "idempotency_key", "fasta", "genomes", "table", "kwargs"}
    return {k: v for k, v in scope.items() if k not in skip and v is not None}


def _labels(labels: Sequence[str] | None) -> dict[str, Any]:
    return {"labels": list(labels)} if labels is not None else {}
