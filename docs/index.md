# plasticdb

Python client and command-line tool for [PlasticDB](https://v2.plasticdb.org): a
curated database of microorganisms and proteins linked to plastic
biodegradation, and the evidence behind each link (paper, experiment,
measured value).

- **Typed.** Every record is a pydantic model; lists are lazy collections you
  can turn into pandas or polars tables, FASTA files or Biopython records.
- **Reproducible.** Pin a [dataset release](reproducibility.md) and every read
  comes from that frozen snapshot. `client.cite()` and `client.provenance()`
  say what you used.
- **Analyses.** Run DIAMOND annotation, genome comparison, pathway analysis, HMM
  screens and taxonomy trees as [jobs](jobs.md), with provenance on the results.
- **A command line.** `plasticdb proteins list --plastic PET --format fasta`,
  usable from [Snakemake and Nextflow](workflows.md).

## Install

```bash
pip install plasticdb
pip install "plasticdb[pandas]"    # or polars, bio, cli, all
pip install "plasticdb[cli]"       # the `plasticdb` command
```

Python 3.10 or newer. The core install needs only `httpx` and `pydantic`.
`to_pandas()`, `to_polars()` and `to_biopython()` need the matching extra and
say which one when it is missing.

## Where next

| I want to... | Go to |
|---|---|
| Fetch a table of PET enzymes in five minutes | [Quick start](quickstart.md) |
| Get the same answer next year | [Reproducibility](reproducibility.md) |
| Know what each `client.<namespace>` does | [Data guide](data.md) |
| Annotate a genome | [Jobs guide](jobs.md) |
| Script it from a shell or a pipeline | [Command line](cli.md), [Workflows](workflows.md) |
| Handle an exception | [Errors](errors.md) |
| Look up a signature | [API reference](api/client.md) |

## Citation and data licence

The code is MIT-licensed. **The PlasticDB data is licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**: cite the dataset
release you used and the PlasticDB paper.

> Gambarini V, Pantos O, Kingsbury JM, Weaver L, Handley KM, Lear G (2022).
> PlasticDB: a database of microorganisms and proteins linked to plastic
> biodegradation. *Database*, 2022:baac008. <https://doi.org/10.1093/database/baac008>

```python
import plasticdb

print(plasticdb.Client(release="2026.50").cite())  # dataset release (with its DOI), paper, licence
```

On the command line: `plasticdb cite --release 2026.50 --format bibtex`.

To cite the software itself, use the repository's
[`CITATION.cff`](https://github.com/VictorGambarini/plasticdb-py/blob/main/CITATION.cff)
(GitHub's "Cite this repository" button).

!!! warning "Pre-alpha"
    The package has not been released to PyPI yet; the API may still change.
