"""Fetch the live SDK contract and compare it with (or replace) the vendored copy.

python -m scripts.refresh_contract           # write tests/contract/sdk-v1.json
python -m scripts.refresh_contract --check   # exit 1 when the live copy differs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from plasticdb._client import DEFAULT_BASE_URL, user_agent

VENDORED = Path(__file__).resolve().parent.parent / "tests" / "contract" / "sdk-v1.json"


def fetch(base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/openapi.sdk.json",
        headers={"User-Agent": user_agent(), "Accept": "application/json"},
        timeout=60,
        follow_redirects=True,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def describe_diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines = []
    old_paths, new_paths = old.get("paths", {}), new.get("paths", {})
    for path in sorted(set(old_paths) | set(new_paths)):
        before, after = old_paths.get(path, {}), new_paths.get(path, {})
        for method in sorted(set(before) | set(after)):
            if method not in before:
                lines.append(f"+ {method.upper()} {path}")
            elif method not in after:
                lines.append(f"- {method.upper()} {path}")
            elif before[method] != after[method]:
                lines.append(f"~ {method.upper()} {path}")
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for name in sorted(set(old_schemas) | set(new_schemas)):
        if old_schemas.get(name) != new_schemas.get(name):
            mark = "+" if name not in old_schemas else "-" if name not in new_schemas else "~"
            lines.append(f"{mark} schema {name}")
    for key in sorted(set(old) | set(new)):
        if key not in ("paths", "components") and old.get(key) != new.get(key):
            lines.append(f"~ {key}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="compare only; never write")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)

    live = fetch(args.base_url)
    vendored = json.loads(VENDORED.read_text(encoding="utf-8")) if VENDORED.exists() else {}
    changes = describe_diff(vendored, live)
    if args.check:
        if changes:
            print("The live SDK contract differs from tests/contract/sdk-v1.json:")
            print("\n".join(changes))
            print("Run `make contract-refresh` and review the diff.")
            return 1
        print("The live SDK contract matches the vendored copy.")
        return 0
    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    VENDORED.write_text(render(live), encoding="utf-8")
    print("\n".join(changes) if changes else "No changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
