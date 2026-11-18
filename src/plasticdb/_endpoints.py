"""Every API operation the SDK calls, declared once.

The contract test checks each registered endpoint against the vendored SDK
OpenAPI document, so a resource must go through an :class:`Endpoint` rather than
a hand-built path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

REGISTRY: list[Endpoint] = []

_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class Endpoint:
    """An API operation: method plus a path template relative to ``/api/v1``.

    ``pinnable`` operations receive the client's ``release`` as ``?release=``.
    ``live_only`` operations take ``release`` but answer only the current one
    (a past release is a 409 ``live_only``); they are still pinnable, so a pinned
    client gets that error instead of a silently live answer.
    ``in_contract=False`` marks the few calls outside the SDK contract (they are
    still listed, so the contract test can see them).
    ``raw_params`` are path parameters that may contain ``/``.
    ``root`` resolves the path against the server root instead of ``/api/v1``.
    ``idempotency`` marks a create operation that honours an ``Idempotency-Key``
    header: the client sends one with every attempt, so a ``POST`` there is
    retried like a ``GET``. ``retry_safe`` marks a ``POST`` that creates nothing
    (a synchronous search), so it is retried like a ``GET`` too.
    """

    method: str
    path: str
    pinnable: bool = True
    live_only: bool = False
    in_contract: bool = True
    raw_params: tuple[str, ...] = ()
    root: bool = False
    idempotency: bool = False
    retry_safe: bool = False

    def __post_init__(self) -> None:
        if self.live_only and not self.pinnable:
            raise ValueError(f"{self.path}: live_only implies pinnable")
        REGISTRY.append(self)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(_FIELD.findall(self.path))

    def format(self, **params: object) -> str:
        missing = set(self.fields) - set(params)
        if missing:
            raise TypeError(f"{self.path}: missing path parameter(s) {sorted(missing)}")
        values = {}
        for name in self.fields:
            text = str(params[name])
            if not text.strip():
                raise ValueError(f"{self.path}: path parameter {name!r} is empty")
            safe = "/" if name in self.raw_params else ""
            values[name] = quote(text, safe=safe)
        return self.path.format(**values)
