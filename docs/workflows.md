# Workflows

Pin the release in the pipeline's configuration and every run reads the same
data. Both examples record the provenance and the citation next to the results.
Install the tool in the rule's environment (`pip install "plasticdb[cli]"`).

## Snakemake

```python
RELEASE = config.get("release", "2026.50")   # snakemake --config release=2026.50

rule all:
    input:
        "results/pet_enzymes.fasta",
        "results/pet_enzymes.tsv",
        "results/provenance.json",
        "results/citation.bib",


rule pet_enzymes:
    """Wild-type PET enzymes of one pinned release, as a table and as sequences."""
    output:
        fasta="results/pet_enzymes.fasta",
        table="results/pet_enzymes.tsv",
    params:
        release=RELEASE,
    log:
        "logs/pet_enzymes.log",
    shell:
        """
        plasticdb proteins list --plastic PET --kind wild_type \
            --release {params.release} -o {output.fasta} 2> {log}
        plasticdb proteins list --plastic PET --kind wild_type \
            --release {params.release} -o {output.table} 2>> {log}
        """


rule provenance:
    output:
        prov="results/provenance.json",
        cite="results/citation.bib",
    params:
        release=RELEASE,
    shell:
        """
        plasticdb provenance --release {params.release} -o {output.prov}
        plasticdb cite --release {params.release} --format bibtex -o {output.cite}
        """


rule annotate_genome:
    """An analysis job against the same release (needs PLASTICDB_API_KEY)."""
    input:
        "genomes/{genome}.faa",
    output:
        "results/{genome}.hits.tsv",
    params:
        release=RELEASE,
    envvars:
        "PLASTICDB_API_KEY",
    shell:
        """
        plasticdb jobs submit annotate-genome {input} --wait --timeout 7200 \
            --release {params.release} -o {output}
        """
```

The `-o` extension picks the format, so the same command writes FASTA or TSV.
A non-zero [exit code](cli.md#exit-codes) fails the rule: 7 means the job failed
on the server, 8 that `--timeout` ran out (the job keeps running), 9 that the
server was unreachable.

## Nextflow

```groovy
params.release = '2026.50'
params.genomes = 'genomes/*.faa'

process PET_ENZYMES {
    publishDir 'results', mode: 'copy'

    output:
    path 'pet_enzymes.fasta'
    path 'provenance.json'
    path 'citation.bib'

    script:
    """
    plasticdb proteins list --plastic PET --kind wild_type \\
        --release ${params.release} -o pet_enzymes.fasta
    plasticdb provenance --release ${params.release} -o provenance.json
    plasticdb cite --release ${params.release} --format bibtex -o citation.bib
    """
}

process ANNOTATE_GENOME {
    publishDir 'results', mode: 'copy'
    // the API key comes from the environment, never from the script
    secret 'PLASTICDB_API_KEY'
    errorStrategy { task.exitStatus in [5, 8, 9] ? 'retry' : 'terminate' }
    maxRetries 2

    input:
    path genome

    output:
    path "${genome.baseName}.hits.tsv"

    script:
    """
    plasticdb jobs submit annotate-genome ${genome} --wait --timeout 7200 \\
        --release ${params.release} -o ${genome.baseName}.hits.tsv
    """
}

workflow {
    PET_ENZYMES()
    ANNOTATE_GENOME(Channel.fromPath(params.genomes))
}
```

!!! warning "Retries resubmit the job"
    Retrying `jobs submit --wait` submits a new job. For a long analysis,
    split it: `plasticdb jobs submit ...` prints the id, and
    `plasticdb jobs wait ID` and `plasticdb jobs download ID` can be retried
    safely.

## Notebook

[Example notebook](examples/explore.ipynb): pin a release, pull and plot a
table, read the evidence, and record provenance and the citation. It is not
executed when the docs are built.
