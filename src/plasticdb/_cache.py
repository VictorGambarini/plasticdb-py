"""On-disk cache for answers from a past release's snapshot.

A past release is immutable, so its answers are kept indefinitely. Only
responses the server marks ``X-PlasticDB-Release-Source: snapshot`` are stored:
live answers (including the current release, which is served from the live
database) are never cached.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx

from ._response import (
    RELEASE_HEADER,
    RELEASE_SOURCE_HEADER,
    SHA256_HEADER,
    SOURCE_SHA256_HEADER,
    UNAVAILABLE_HEADER,
)

_KEPT_HEADERS = (
    RELEASE_HEADER,
    RELEASE_SOURCE_HEADER,
    UNAVAILABLE_HEADER,
    SHA256_HEADER,
    SOURCE_SHA256_HEADER,
    "etag",
    "content-type",
    "content-disposition",
    "link",
)
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_FORMAT = 1


class ResponseCache:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory).expanduser()

    def _paths(self, release: str, key: str) -> tuple[Path, Path]:
        folder = self.directory / _SAFE.sub("_", release) / key[:2]
        return folder / f"{key}.json", folder / f"{key}.body"

    @staticmethod
    def key(method: str, url: httpx.URL) -> str:
        params = sorted(url.params.multi_items())
        canonical = json.dumps(
            [_FORMAT, method.upper(), str(url.copy_with(query=None)), params],
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, release: str, key: str) -> tuple[int, httpx.Headers, bytes] | None:
        meta_path, body_path = self._paths(release, key)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
        except (OSError, ValueError):
            return None
        if meta.get("format") != _FORMAT or meta.get("size") != len(body):
            return None
        if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
            return None
        return int(meta["status"]), httpx.Headers(meta.get("headers") or {}), body

    def put(self, release: str, key: str, response: httpx.Response) -> None:
        meta_path, body_path = self._paths(release, key)
        body = response.content
        headers = {
            name: response.headers[name] for name in _KEPT_HEADERS if name in response.headers
        }
        meta = {
            "format": _FORMAT,
            "status": response.status_code,
            "url": str(response.request.url),
            "headers": headers,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(body_path, body)
            _atomic_write(meta_path, json.dumps(meta, indent=1).encode())
        except OSError:
            return

    def get_file(self, release: str, key: str) -> tuple[int, httpx.Headers, Path] | None:
        """A cached download: its status, headers and the path of its verified body."""
        meta_path, body_path = self._paths(release, key)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            size = body_path.stat().st_size
        except (OSError, ValueError):
            return None
        if meta.get("format") != _FORMAT or meta.get("size") != size:
            return None
        try:
            digest = sha256_file(body_path)
        except OSError:
            return None
        if digest != meta.get("sha256"):
            return None
        return int(meta["status"]), httpx.Headers(meta.get("headers") or {}), body_path

    def put_file(
        self,
        release: str,
        key: str,
        *,
        source: Path,
        status: int,
        headers: httpx.Headers,
        url: str,
        sha256: str,
    ) -> None:
        """Keep a copy of a downloaded file (already hashed as ``sha256``)."""
        import shutil

        meta_path, body_path = self._paths(release, key)
        kept = {name: headers[name] for name in _KEPT_HEADERS if name in headers}
        meta = {
            "format": _FORMAT,
            "status": status,
            "url": url,
            "headers": kept,
            "size": source.stat().st_size,
            "sha256": sha256,
        }
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=body_path.parent, prefix=".tmp-")
            os.close(fd)
            try:
                shutil.copyfile(source, tmp)
                os.replace(tmp, body_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            _atomic_write(meta_path, json.dumps(meta, indent=1).encode())
        except OSError:
            return

    def clear(self, release: str | None = None) -> None:
        import shutil

        target = self.directory / _SAFE.sub("_", release) if release else self.directory
        shutil.rmtree(target, ignore_errors=True)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
