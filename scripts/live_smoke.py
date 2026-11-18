"""Nightly smoke test against the live API (network required).

Checks that ``releases.current()`` answers. Before the first dataset release the
server reports no current release, which is accepted.
"""

from __future__ import annotations

import logging
import sys

import plasticdb


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with plasticdb.Client() as client:
        current = client.releases.current()
        if current.version is None:
            print("No current dataset release yet (accepted).")
        else:
            print(f"Current release: {current.version} (DOI {current.doi})")
            release = client.releases.get(current.version)
            assert release.version == current.version
        listed = client.releases.list(limit=5)
        print(f"Releases listed: {len(listed)} (first page {[r.version for r in listed]})")
        provenance = client.provenance()
        print(f"Provenance: {provenance.to_dict()}")
        print(str(client.cite()))
        if provenance.server_version is None:
            print("Server version unavailable from /health.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
