from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from . import errors
from ._endpoints import Endpoint
from ._fasta import parse_fasta, to_seqrecords, write_fasta
from ._response import APIResponse

if TYPE_CHECKING:
    from ._client import Client

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("plasticdb")

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100
_RATE_HEADER = "x-ratelimit-remaining"

Page = tuple[int, list[Any], APIResponse]


def _require(module: str, extra: str, feature: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"{feature} needs the optional dependency {module!r}. "
            f"Install it with: pip install 'plasticdb[{extra}]'"
        ) from exc


def normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every row the same columns, in first-seen order.

    A nested object that is ``null`` in one row and present in another would
    otherwise give both a ``parent`` column and ``parent.child`` columns.
    """
    keys: dict[str, None] = {}
    for row in rows:
        keys.update(dict.fromkeys(row))
    nested = {k.split(".", 1)[0] for k in keys if "." in k}
    drop = {k for k in keys if k in nested and all(r.get(k) is None for r in rows)}
    columns = [k for k in keys if k not in drop]
    return [{k: row.get(k) for k in columns} for row in rows]


class Collection(Generic[T]):
    """A lazy, paged list result (``{total, items}`` envelopes).

    Nothing is fetched until it is used. Iterating walks the pages in order, one
    request at a time; :meth:`all` (and everything built on it: ``records``,
    ``to_pandas``, ``to_polars``, ``to_fasta``) fetches the remaining pages at the
    largest page size, a few in parallel (``Client(max_concurrency=)``). ``len()``
    is the server's ``total`` (capped by ``limit``). ``limit`` caps how many items
    are returned, ``page_size`` how many each request asks for while iterating.
    """

    def __init__(
        self,
        client: Client,
        endpoint: Endpoint,
        model: type[T],
        *,
        params: Mapping[str, Any] | None = None,
        path_params: Mapping[str, Any] | None = None,
        page_size: int | None = None,
        limit: int | None = None,
        max_page_size: int = MAX_PAGE_SIZE,
    ) -> None:
        size = DEFAULT_PAGE_SIZE if page_size is None else page_size
        if not 1 <= size <= max_page_size:
            raise ValueError(f"page_size must be between 1 and {max_page_size}")
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        self._client = client
        self._endpoint = endpoint
        self._model = model
        self._params = {k: v for k, v in (params or {}).items() if v is not None}
        self._path_params = dict(path_params or {})
        self._page_size = min(size, max_page_size)
        self._max_page_size = max_page_size
        self._limit = limit
        self._first: Page | None = None
        self._envelope: dict[str, Any] = {}

    # ── fetching ─────────────────────────────────────────────────────────────

    def _fetch(self, offset: int, size: int) -> Page:
        params = {**self._params, "offset": offset, "limit": size}
        response = self._client._request(self._endpoint, params=params, **self._path_params)
        data = response.data
        if not isinstance(data, dict) or "items" not in data or "total" not in data:
            raise errors.UnexpectedResponseError(
                f"{self._endpoint.path} did not answer a {{total, items}} envelope"
            )
        items = [self._model.model_validate(item) for item in data["items"]]
        if offset == 0:
            self._envelope = {k: v for k, v in data.items() if k != "items"}
        return int(data["total"]), items, response

    def _first_page(self, size: int | None = None) -> Page:
        if self._first is None:
            self._first = self._fetch(0, self._size_for(0, size or self._page_size))
        return self._first

    def _size_for(self, offset: int, size: int) -> int:
        if self._limit is None:
            return size
        return max(1, min(size, self._limit - offset))

    def _check_release(self, first: APIResponse, response: APIResponse) -> None:
        if response.release != first.release:
            log.warning(
                "PlasticDB: release changed from %s to %s while paging %s; "
                "pin a release for a consistent result",
                first.release,
                response.release,
                self._endpoint.path,
            )

    @property
    def total(self) -> int:
        """How many records match on the server (ignores ``limit``)."""
        return self._first_page()[0]

    @property
    def release(self) -> str | None:
        """The release that answered the first page."""
        return self._first_page()[2].release

    @property
    def response(self) -> APIResponse:
        """The first page's response (headers, release, unavailable fields)."""
        return self._first_page()[2]

    def __len__(self) -> int:
        total = self.total
        return total if self._limit is None else min(total, self._limit)

    def __iter__(self) -> Iterator[T]:
        if self._limit == 0:
            return
        total, items, first = self._first_page()
        wanted = len(self)
        yielded = 0
        offset = 0
        while True:
            for item in items:
                if yielded >= wanted:
                    return
                yield item
                yielded += 1
            offset += len(items)
            if not items or yielded >= wanted or offset >= total:
                return
            total, items, response = self._fetch(offset, self._size_for(offset, self._page_size))
            self._check_release(first, response)
            wanted = total if self._limit is None else min(total, self._limit)

    def first(self) -> T | None:
        """The first item, or ``None`` when nothing matches."""
        if self._first is not None or self._limit == 0:
            return next(iter(self), None)
        _, items, _ = self._fetch(0, 1)
        return items[0] if items else None

    def all(self) -> list[T]:
        """Every item (up to ``limit``), fetching the remaining pages in bulk."""
        if self._limit == 0:
            return []
        total, items, first = self._first_page(self._max_page_size)
        wanted = len(self)
        out = list(items[:wanted])
        if not items or len(out) >= wanted:
            return out
        size = self._max_page_size
        offsets = list(range(len(items), wanted, size))
        workers = self._workers(first, len(offsets))

        def fetch(offset: int) -> Page:
            return self._fetch(offset, min(size, wanted - offset))

        pages: Iterable[Page]
        if workers <= 1:
            pages = map(fetch, offsets)
            for page_total, page_items, response in pages:
                self._check_release(first, response)
                out.extend(page_items)
                if not page_items or page_total != total:
                    return self._sequential_rest(out, first)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="plasticdb") as pool:
                results = list(pool.map(fetch, offsets))
            for page_total, page_items, response in results:
                self._check_release(first, response)
                out.extend(page_items)
                if page_total != total:
                    log.warning(
                        "PlasticDB: %s changed size while it was fetched (%d -> %d records); "
                        "pin a release for a consistent result",
                        self._endpoint.path,
                        total,
                        page_total,
                    )
        return out[:wanted]

    def _sequential_rest(self, out: list[T], first: APIResponse) -> list[T]:
        """The total moved mid-pull: finish one page at a time from where we are."""
        offset = len(out)
        while True:
            total, items, response = self._fetch(
                offset, self._size_for(offset, self._max_page_size)
            )
            self._check_release(first, response)
            wanted = total if self._limit is None else min(total, self._limit)
            out.extend(items)
            offset += len(items)
            if not items or offset >= wanted:
                return out[:wanted]

    def _workers(self, first: APIResponse, pages: int) -> int:
        workers = min(max(self._client.max_concurrency, 1), pages)
        remaining = first.headers.get(_RATE_HEADER)
        if remaining is not None and remaining.isdigit() and int(remaining) < pages + workers:
            # Not enough of the rate-limit allowance left to fan out: go one at a
            # time and let Retry-After pace the rest.
            return 1
        return workers

    # ── conversions ──────────────────────────────────────────────────────────

    def records(self) -> list[dict[str, Any]]:
        """Every item as a JSON-compatible dict, nested exactly as the API sends it."""
        return [item.model_dump(mode="json") for item in self.all()]

    def rows(self) -> list[dict[str, Any]]:
        """Every item as one flat row (see ``PlasticDBModel.to_row``)."""
        return normalise_rows([_row(item) for item in self.all()])

    def to_pandas(self) -> Any:
        """A ``pandas.DataFrame``, one flat row per item (needs ``plasticdb[pandas]``).

        Nested objects become ``parent.child`` columns; lists of plain values stay
        lists. ``records()`` keeps the full nesting.
        """
        pd = _require("pandas", "pandas", "to_pandas()")
        return pd.DataFrame.from_records(self.rows())

    def to_polars(self) -> Any:
        """A ``polars.DataFrame``, one flat row per item (needs ``plasticdb[polars]``)."""
        pl = _require("polars", "polars", "to_polars()")
        return _polars_frame(pl, self.rows())

    def _sequences(self) -> list[tuple[str, str | None, str]]:
        if "sequence" not in self._model.model_fields:
            raise TypeError(f"{self._model.__name__} records carry no sequences")
        out = []
        for item in self.all():
            sequence = getattr(item, "sequence", None)
            if sequence:
                out.append((str(item.seq_id), _description(item), sequence))  # type: ignore[attr-defined]
        return out

    def to_fasta(self, path: Any = None) -> str:
        """FASTA text of the items' sequences (``>seq_id name``); records without one are skipped.

        With ``path``, the text is also written there.
        """
        text = write_fasta(self._sequences())
        if path is not None:
            from pathlib import Path

            Path(path).write_text(text, encoding="utf-8")
        return text

    def to_biopython(self) -> list[Any]:
        """``Bio.SeqRecord.SeqRecord`` objects (needs ``plasticdb[bio]``)."""
        _require("Bio", "bio", "to_biopython()")
        return to_seqrecords(self._sequences())

    def __repr__(self) -> str:
        state = f"total={self._first[0]}" if self._first is not None else "not fetched"
        return f"<{type(self).__name__} {self._endpoint.path} of {self._model.__name__} ({state})>"


def _row(item: BaseModel) -> dict[str, Any]:
    to_row = getattr(item, "to_row", None)
    if callable(to_row):
        row: dict[str, Any] = to_row()
        return row
    return item.model_dump(mode="json")


def _description(item: Any) -> str | None:
    for name in ("name", "variant_name"):
        value = getattr(item, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _polars_frame(pl: Any, rows: list[dict[str, Any]]) -> Any:
    try:
        return pl.from_dicts(rows, infer_schema_length=None, strict=False)
    except TypeError:  # polars < 1.0 has no ``strict``
        return pl.from_dicts(rows, infer_schema_length=None)


class ProteinCollection(Collection[T]):
    """``/proteins`` results. List items carry no sequence, so FASTA comes from the server.

    ``to_fasta()`` downloads ``/downloads/proteins.fasta`` once, with the same
    filters (a pinned client: that release's whole FASTA, kept in the cache),
    and keeps only the listed records, in list order.
    """

    def __init__(
        self,
        *args: Any,
        fasta_endpoint: Endpoint,
        fasta_params: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._fasta_endpoint = fasta_endpoint
        self._fasta_params = dict(fasta_params)

    def _sequences(self) -> list[tuple[str, str | None, str]]:
        items = self.all()
        if not items:
            return []
        params = {} if self._client.release else self._fasta_params
        data = self._client._request(self._fasta_endpoint, params=params).data
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data or "")
        sequences = {seq_id: seq for seq_id, _, seq in parse_fasta(text)}
        out = []
        for item in items:
            seq_id = str(item.seq_id)  # type: ignore[attr-defined]
            if seq_id in sequences:
                out.append((seq_id, _description(item), sequences[seq_id]))
        missing = len(items) - len(out)
        if missing:
            log.info("PlasticDB: %d of %d records have no sequence", missing, len(items))
        return out


class InteractionCollection(Collection[T]):
    """Interactions, with tables at three levels of the evidence spine."""

    def rows(self, level: str = "interaction") -> list[dict[str, Any]]:
        """Flat rows per ``interaction`` (default), ``experiment`` or ``observation``.

        Deeper levels repeat the interaction's columns on every row and add
        ``experiment.*`` (and ``observation.*``) columns; an interaction without
        experiments still gives one row.
        """
        method: Callable[[Any], list[dict[str, Any]]]
        if level == "interaction":
            return super().rows()
        if level == "experiment":
            method = lambda item: item.experiment_rows()  # noqa: E731
        elif level == "observation":
            method = lambda item: item.observation_rows()  # noqa: E731
        else:
            raise ValueError("level must be 'interaction', 'experiment' or 'observation'")
        return normalise_rows([row for item in self.all() for row in method(item)])

    def to_pandas(self, level: str = "interaction") -> Any:
        pd = _require("pandas", "pandas", "to_pandas()")
        return pd.DataFrame.from_records(self.rows(level))

    def to_polars(self, level: str = "interaction") -> Any:
        pl = _require("polars", "polars", "to_polars()")
        return _polars_frame(pl, self.rows(level))


class ChangeCollection(Collection[T]):
    """The ``/changes`` feed. ``as_of`` is the server time to pass as the next ``since``."""

    @property
    def as_of(self) -> str | None:
        self._first_page()
        value = self._envelope.get("as_of")
        return value if isinstance(value, str) else None

    @property
    def since(self) -> str | None:
        self._first_page()
        value = self._envelope.get("since")
        return value if isinstance(value, str) else None
