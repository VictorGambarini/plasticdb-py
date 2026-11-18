from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from . import errors
from ._endpoints import Endpoint
from ._response import APIResponse

if TYPE_CHECKING:
    from ._client import Client

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("plasticdb")

MAX_PAGE_SIZE = 500


def _require(module: str, extra: str, feature: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"{feature} needs the optional dependency {module!r}. "
            f"Install it with: pip install 'plasticdb[{extra}]'"
        ) from exc


class Collection(Generic[T]):
    """A lazy, paged list result (``{total, items}`` envelopes).

    Nothing is fetched until it is used. Iterating walks every page in order;
    ``len()`` is the server's ``total`` (capped by ``limit``). ``limit`` caps the
    number of items returned, ``page_size`` how many are fetched per request.
    """

    def __init__(
        self,
        client: Client,
        endpoint: Endpoint,
        model: type[T],
        *,
        params: Mapping[str, Any] | None = None,
        path_params: Mapping[str, Any] | None = None,
        page_size: int = 100,
        limit: int | None = None,
    ) -> None:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        self._client = client
        self._endpoint = endpoint
        self._model = model
        self._params = {k: v for k, v in (params or {}).items() if v is not None}
        self._path_params = dict(path_params or {})
        self._page_size = page_size
        self._limit = limit
        self._first: tuple[int, list[T], APIResponse] | None = None

    def _fetch(self, offset: int, size: int) -> tuple[int, list[T], APIResponse]:
        params = {**self._params, "offset": offset, "limit": size}
        response = self._client._request(self._endpoint, params=params, **self._path_params)
        data = response.data
        if not isinstance(data, dict) or "items" not in data or "total" not in data:
            raise errors.UnexpectedResponseError(
                f"{self._endpoint.path} did not answer a {{total, items}} envelope"
            )
        items = [self._model.model_validate(item) for item in data["items"]]
        return int(data["total"]), items, response

    def _first_page(self) -> tuple[int, list[T], APIResponse]:
        if self._first is None:
            self._first = self._fetch(0, self._page_size_for(0))
        return self._first

    def _page_size_for(self, offset: int) -> int:
        if self._limit is None:
            return self._page_size
        return max(1, min(self._page_size, self._limit - offset))

    @property
    def total(self) -> int:
        """How many records match on the server (ignores ``limit``)."""
        return self._first_page()[0]

    @property
    def release(self) -> str | None:
        """The release that answered the first page."""
        return self._first_page()[2].release

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
            total, items, response = self._fetch(offset, self._page_size_for(offset))
            if response.release != first.release:
                log.warning(
                    "PlasticDB: release changed from %s to %s while paging %s; "
                    "pin a release for a consistent result",
                    first.release,
                    response.release,
                    self._endpoint.path,
                )
            wanted = total if self._limit is None else min(total, self._limit)

    def first(self) -> T | None:
        """The first item, or ``None`` when nothing matches."""
        if self._first is not None or self._limit == 0:
            return next(iter(self), None)
        _, items, _ = self._fetch(0, 1)
        return items[0] if items else None

    def all(self) -> list[T]:
        """Every item (up to ``limit``), fetching all pages."""
        return list(self)

    def records(self) -> list[dict[str, Any]]:
        """Every item as a JSON-compatible dict."""
        return [item.model_dump(mode="json") for item in self]

    def to_pandas(self) -> Any:
        """A ``pandas.DataFrame`` with one row per item (needs ``plasticdb[pandas]``)."""
        pd = _require("pandas", "pandas", "to_pandas()")
        return pd.DataFrame.from_records(self.records())

    def to_polars(self) -> Any:
        """A ``polars.DataFrame`` with one row per item (needs ``plasticdb[polars]``)."""
        pl = _require("polars", "polars", "to_polars()")
        return pl.from_dicts(self.records())

    def to_fasta(self) -> str:
        """FASTA text of the items' sequences. Available on sequence collections."""
        raise TypeError(f"{self._model.__name__} records carry no sequences to write as FASTA")

    def to_biopython(self) -> list[Any]:
        """``Bio.SeqRecord`` objects (needs ``plasticdb[bio]``). Sequence collections only."""
        _require("Bio", "bio", "to_biopython()")
        raise TypeError(f"{self._model.__name__} records carry no sequences")

    def __repr__(self) -> str:
        state = f"total={self._first[0]}" if self._first is not None else "not fetched"
        return f"<Collection {self._endpoint.path} of {self._model.__name__} ({state})>"
