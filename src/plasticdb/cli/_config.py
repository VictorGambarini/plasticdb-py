"""The CLI's config file: where ``plasticdb auth login`` keeps the API key.

The file is JSON, readable by its owner only (0600, in a 0700 directory), at
``$PLASTICDB_CONFIG_DIR/config.json``, else ``$XDG_CONFIG_HOME/plasticdb/config.json``
(``~/.config/plasticdb`` by default; ``%APPDATA%\\plasticdb`` on Windows). The
environment variable ``PLASTICDB_API_KEY`` always wins over the file.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

CONFIG_NAME = "config.json"
FILE_MODE = 0o600
DIR_MODE = 0o700


def config_dir() -> Path:
    explicit = os.environ.get("PLASTICDB_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "plasticdb"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "plasticdb"


def config_path() -> Path:
    return config_dir() / CONFIG_NAME


def too_open(path: Path) -> bool:
    """Whether group or others can read the file (POSIX only)."""
    if os.name != "posix":
        return False
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def load() -> dict[str, Any]:
    """The config, or ``{}`` when there is none. A broken file raises ``ValueError``."""
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data


def save(data: dict[str, Any]) -> Path:
    """Write the config atomically, 0600, creating its directory 0700."""
    path = config_path()
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{CONFIG_NAME}.")
    try:
        # mkstemp already creates the file 0600; chmod in case the platform differs.
        os.chmod(tmp, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def stored_key() -> str | None:
    key = load().get("api_key")
    return key.strip() or None if isinstance(key, str) else None


def store_key(key: str) -> Path:
    data = load()
    data["api_key"] = key
    return save(data)


def forget_key() -> bool:
    """Remove the stored key. ``True`` when there was one."""
    data = load()
    if "api_key" not in data:
        return False
    del data["api_key"]
    path = config_path()
    if data:
        save(data)
    else:
        path.unlink()
    return True


def mask(key: str) -> str:
    """A key fit to print: its last four characters."""
    return f"…{key[-4:]}" if len(key) > 8 else "…"
