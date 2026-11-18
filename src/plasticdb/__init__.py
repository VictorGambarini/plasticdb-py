"""Python client for the PlasticDB API (https://v2.plasticdb.org).

PlasticDB data is licensed CC BY 4.0: cite the dataset release you used
(``Client.cite()``).
"""

import logging

from . import errors, models
from ._client import DEFAULT_BASE_URL, Citation, Client, Provenance
from ._pagination import Collection, ResultTable
from ._response import APIResponse
from ._version import __version__
from .errors import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ChecksumError,
    ConflictError,
    EngineeredVariantError,
    GoneError,
    IdempotencyKeyReusedError,
    JobError,
    JobFailedError,
    JobTimeoutError,
    LiveOnlyError,
    MergedError,
    MergedRecordWarning,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDenied,
    PlasticDBError,
    RateLimitError,
    ReleaseError,
    ReleaseNoArtefactsError,
    ReleaseNotAvailableError,
    ReleaseNotMountedError,
    ReleaseUnsupportedError,
    ResultsExpiredError,
    ServerError,
    ServiceUnavailableError,
    UnexpectedResponseError,
    UnknownReleaseError,
    UnsupportedFilterError,
    ValidationError,
)
from .resources.jobs import Job, JobProvenance

logging.getLogger("plasticdb").addHandler(logging.NullHandler())

__all__ = [
    "DEFAULT_BASE_URL",
    "APIConnectionError",
    "APIResponse",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ChecksumError",
    "Citation",
    "Client",
    "Collection",
    "ConflictError",
    "EngineeredVariantError",
    "GoneError",
    "IdempotencyKeyReusedError",
    "Job",
    "JobError",
    "JobFailedError",
    "JobProvenance",
    "JobTimeoutError",
    "LiveOnlyError",
    "MergedError",
    "MergedRecordWarning",
    "NotFoundError",
    "PayloadTooLargeError",
    "PermissionDenied",
    "PlasticDBError",
    "Provenance",
    "RateLimitError",
    "ReleaseError",
    "ReleaseNoArtefactsError",
    "ReleaseNotAvailableError",
    "ReleaseNotMountedError",
    "ReleaseUnsupportedError",
    "ResultTable",
    "ResultsExpiredError",
    "ServerError",
    "ServiceUnavailableError",
    "UnexpectedResponseError",
    "UnknownReleaseError",
    "UnsupportedFilterError",
    "ValidationError",
    "__version__",
    "errors",
    "models",
]
