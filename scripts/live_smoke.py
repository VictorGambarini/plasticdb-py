"""Nightly smoke test against the live API (network required).

Checks that ``releases.current()`` answers (before the first dataset release the
server reports none, which is accepted), then that each data namespace parses a
small live answer. About fifteen requests, well inside the anonymous rate limit.
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
        smoke_data(client)
        if provenance.server_version is None:
            print("Server version unavailable from /health.", file=sys.stderr)
            return 1
    return 0


def smoke_data(client: plasticdb.Client) -> None:
    proteins = client.proteins.list(plastic="PET", kind="wild_type", limit=3)
    print(f"PET proteins: {proteins.total}; FASTA records: {proteins.to_fasta().count('>')}")
    first = proteins.first()
    assert first is not None and first.accession
    protein = client.proteins.get(first.accession, view="summary")
    print(f"{protein.accession}: {protein.name}, {len(protein.sequence or '')} aa")
    client.proteins.interactions(first.accession, limit=2).rows("observation")
    organism = client.microorganisms.list(limit=1).first()
    assert organism is not None
    client.microorganisms.get(organism.accession or organism.tax_id or organism.id)
    plastic = client.plastics.list(name="PET", limit=1).first()
    assert plastic is not None
    client.plastics.get(plastic.id, microorganism_limit=1, protein_limit=1)
    paper = client.papers.list(limit=1).first()
    assert paper is not None
    client.papers.get(paper.id)
    print(f"Search hits for PETase: {client.search('PETase', limit=1).total}")
    print(f"Stats: {client.stats.summary().model_dump()}")
    print(f"Pathways: {len(client.pathways.list().all())}")
    feed = client.changes("2026-01-01T00:00:00Z", limit=1)
    print(f"Changes since 2026-01-01: {feed.total} (as of {feed.as_of})")


if __name__ == "__main__":
    sys.exit(main())
