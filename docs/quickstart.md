# Quick start

Reads need no account or API key.

```python
import plasticdb

client = plasticdb.Client()

# Proteins with positive evidence on PET: a table and the sequences
pet = client.proteins.list(plastic="PET", kind="wild_type")
print(pet.total)  # the server's total; nothing else is fetched yet
df = pet.to_pandas()  # all pages, one flat row per protein   (plasticdb[pandas])
pet.to_fasta("pet_enzymes.fasta")  # sequences, in list order
records = pet.to_biopython()  # list of Bio.SeqRecord                 (plasticdb[bio])

# One record
petase = client.proteins.get("PBFCTW")
print(petase.name, petase.source_microorganism.name)

# The evidence behind it: interaction -> experiment -> observation
evidence = client.evidence.interactions("PBFCTW")
measurements = evidence.to_pandas(level="observation")  # one row per measured value
```

## Lists are lazy

`list()` and friends return a [`Collection`](api/collections.md). Nothing is
fetched until you use it:

| You write | What happens |
|---|---|
| `len(c)` or `c.total` | the server's total (one request) |
| `c.first()` | the first record |
| `for item in c:` | pages are fetched as you go |
| `c.all()`, `c.rows()`, `c.to_pandas()`, `c.to_polars()`, `c.to_fasta()` | every remaining page, 500 records each, up to `max_concurrency` (4) at a time |

Pass `limit=` to a `list()` call to cap it. Entity lists show only records with
a **positive** result, as the website does; pass `polarity="negative"` or
`include_negative=True` for the rest.

## Filters

Keyword arguments are the API's own query parameters and mean the same on every
resource: `plastic`, `include_subtypes`, `microorganism`, `polarity`,
`evidence_method`, `min_year`, `max_year`, `updated_since`, `sort_by`, `sort_order`.
The taxonomic rank `class` is spelled `class_`. An unknown name raises
`TypeError` naming the valid ones; an unknown value is a server 422
(`ValidationError`).

## Pin a release

```python
client = plasticdb.Client(release="2026.50")
```

Everything you read now comes from that frozen release. See
[Reproducibility](reproducibility.md).

## From the shell

```bash
pip install "plasticdb[cli]"
plasticdb proteins list --plastic PET --format fasta > pet.fasta
plasticdb cite --release 2026.50
```

See [Command line](cli.md).
