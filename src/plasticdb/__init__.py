"""Python client for the PlasticDB API (https://v2.plasticdb.org).

PlasticDB data is licensed CC BY 4.0: cite the dataset release you used
(``Client.cite()``).
"""

import logging

from . import errors, models
from ._client import DEFAULT_BASE_URL, Citation, Client, Provenance
from ._pagination import Collection
from ._response import APIResponse
from ._version import __version__
from .errors import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    GoneError,
    MergedError,
    NotFoundError,
    PermissionDenied,
    PlasticDBError,
    RateLimitError,
    ReleaseError,
    ReleaseNotAvailableError,
    ReleaseUnsupportedError,
    ServerError,
    ValidationError,
)

logging.getLogger("plasticdb").addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT_BASE_URL",
    "APIConnectionError",
    "APIResponse",
    "APITimeoutError",
    "AuthenticationError",
    "Citation",
    "Client",
    "Collection",
    "GoneError",
    "MergedError",
    "NotFoundError",
    "PermissionDenied",
    "PlasticDBError",
    "Provenance",
    "RateLimitError",
    "ReleaseError",
    "ReleaseNotAvailableError",
    "ReleaseUnsupportedError",
    "ServerError",
    "ValidationError",
    "__version__",
    "errors",
    "models",
]
