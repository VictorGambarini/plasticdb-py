# Command line

```bash
pip install "plasticdb[cli]"

plasticdb proteins list --plastic PET --format fasta > pet.fasta
plasticdb download dataset --release 2026.50 -o data/
plasticdb jobs submit annotate-genome genome.fna --wait -o hits.tsv
plasticdb cite --release 2026.50
```

`plasticdb` (also `python -m plasticdb`) is a thin layer over the SDK: each
command is one `Client` call. Without the `cli` extra the script says which
extra to install. This page's command tree and exit-code table are generated
from the tool itself.

## Output

- Data goes to **stdout**; messages, progress and warnings go to **stderr**.
- `--format` picks `tsv`, `csv`, `json`, `jsonl` or (for sequences) `fasta`.
  Lists default to `tsv`, single records to `json`.
- `-o FILE` writes to a file, and the extension picks the format when `--format`
  is not given (`.tsv`, `.csv`, `.json`, `.jsonl`, `.fasta`, and `.bib`, `.ris`,
  `.nwk` where they apply). Downloads take a file or a directory.
- TSV is one line per record: lists are joined with `;`, nested objects are
  JSON, and tabs and newlines inside values become spaces.
- `--limit/-n` caps a list. A closed pipe (`| head`) is not an error.

## No prompts

The tool never asks for anything unless you pass `--interactive`, and only
`auth login` ever asks.

## Global options and filters

`--release`, `--api-key`, `--base-url`, `--cache-dir`, `-q/--quiet` and
`-v/--verbose` (`-vv` logs every request and retry) work before or after the
subcommand. Their environment variables are `PLASTICDB_RELEASE`,
`PLASTICDB_API_KEY`, `PLASTICDB_BASE_URL`, `PLASTICDB_CACHE_DIR` and
`PLASTICDB_CONFIG_DIR`.

Filter options are the SDK's filter names (`--plastic`, `--include-subtypes`,
`--polarity`, `--min-year`, `--class`, ...), generated from its types, so
`plasticdb proteins list --help` lists exactly what `client.proteins.list()`
takes.

## Authentication

```bash
pbpaste | plasticdb auth login       # or: plasticdb auth login --interactive
plasticdb auth status                # which key, and where it came from
plasticdb auth logout
```

`auth login` checks the key with the server and saves it to
`$XDG_CONFIG_HOME/plasticdb/config.json` (`~/.config/plasticdb/`;
`%APPDATA%\plasticdb` on Windows; `$PLASTICDB_CONFIG_DIR` overrides it), mode
0600 in a 0700 directory. The key is read from stdin, or from `--key` (which
leaves it in your shell history), and is never echoed. Precedence: `--api-key`,
then `PLASTICDB_API_KEY`, then the file. Only `jobs` needs a key.

## Downloads and jobs

`download dataset` without a release saves the live database's ZIP. With
`--release`, `-o` is a directory and receives every artefact the release
published (Parquet tables, reference databases, `MANIFEST.json`), each checked
against its SHA-256.

`jobs submit` prints the job id. With `--wait` it also writes the results:
`-o hits.tsv` saves the server's results file (with provenance lines), and
`--format` writes the typed results instead. Each job parameter is an option
named after its SDK keyword (`--evalue`, `--target-polymers`, `--genus-column`,
...); the `build-tree` filters are the `/microorganisms` filters. An input of `-`
is read from stdin. Job types are kebab-case: `annotate-gene`, `annotate-genome`,
`compare-genomes`, `annotate-list`, `pathway-analysis`, `hmm-screen`,
`build-tree`.

## Exit codes

Stable, so pipelines can branch on them.

{{EXIT_CODES}}

## Reference

{{COMMANDS}}
