"""Streaming downloads to disk, with checksum verification and the release cache."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from . import errors
from ._cache import ResponseCache, sha256_file
from ._endpoints import Endpoint
from ._response import APIResponse, error_from_response, release_headers

if TYPE_CHECKING:
    from ._client import Client

_FILENAME = re.compile(r"""filename\*?=(?:UTF-8'')?"?([^";]+)"?""", re.IGNORECASE)


def filename_from(headers: httpx.Headers, default: str) -> str:
    match = _FILENAME.search(headers.get("content-disposition", ""))
    name = Path(match.group(1)).name if match else ""
    return name if name and name not in {".", ".."} else default


def resolve_target(dest: str | os.PathLike[str], headers: httpx.Headers, default: str) -> Path:
    path = Path(dest).expanduser()
    text = os.fspath(dest)
    if path.is_dir() or text.endswith(("/", os.sep)):
        return path / filename_from(headers, default)
    return path


def default_mode(path: str | os.PathLike[str]) -> None:
    """Give a ``mkstemp`` file (0600) the mode a new file gets: 0666 less the umask."""
    if os.name != "posix":
        return
    mask = os.umask(0)
    os.umask(mask)
    os.chmod(path, 0o666 & ~mask)


def _place(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        default_mode(tmp)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def stream_to_file(
    client: Client,
    endpoint: Endpoint,
    dest: str | os.PathLike[str],
    *,
    params: Mapping[str, Any] | None,
    path_params: Mapping[str, Any],
    sha256: str | None,
    immutable_release: str | None,
    default_name: str,
) -> Path:
    method, url, pinned = client._prepare(endpoint, params, path_params)
    expected = sha256.lower() if sha256 else None
    cache = client.cache
    cache_release = immutable_release or pinned
    key = ResponseCache.key(method, url) if cache is not None and cache_release else None

    if cache is not None and cache_release and key:
        hit = cache.get_file(cache_release, key)
        if hit is not None and (expected is None or sha256_file(hit[2]) == expected):
            status, headers, body = hit
            target = resolve_target(dest, headers, default_name)
            _place(body, target)
            client._remember(_result(status, headers, str(url), target, from_cache=True), endpoint)
            return target

    response = client._send(method, url, follow_redirects=True, stream=True)
    try:
        if not response.is_success:
            raise error_from_response(response)
        target = resolve_target(dest, response.headers, default_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
                    digest.update(chunk)
            actual = digest.hexdigest()
            if expected is not None and actual != expected:
                raise errors.ChecksumError(
                    f"{url.path}: SHA-256 {actual} does not match the published {expected}",
                    expected=expected,
                    actual=actual,
                    status_code=response.status_code,
                    response=response,
                )
            default_mode(tmp)
            os.replace(tmp, target)
        except httpx.TransportError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise errors.APIConnectionError(f"Download interrupted: {exc}") from exc
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
    finally:
        response.close()

    result = _result(response.status_code, response.headers, str(response.url), target)
    client._remember(result, endpoint)
    if cache is not None and cache_release and key:
        storable = immutable_release is not None or (
            result.release_source == "snapshot" and result.release == pinned
        )
        if storable:
            cache.put_file(
                cache_release,
                key,
                source=target,
                status=response.status_code,
                headers=response.headers,
                url=str(response.url),
                sha256=actual,
            )
    return target


def _result(
    status: int, headers: httpx.Headers, url: str, target: Path, *, from_cache: bool = False
) -> APIResponse:
    release, source, unavailable = release_headers(headers)
    return APIResponse(
        status_code=status,
        data=target,
        headers=headers,
        url=url,
        release=release,
        release_source=source,
        unavailable_fields=unavailable,
        from_cache=from_cache,
    )
