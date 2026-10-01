# NPDC CSV Data Extractor

This project scaffolds CSV-to-JSONL extractors for the NPDC loader.

The official scaffold is Python-first. Other languages can still participate by
emitting the same loader-compatible JSONL contract.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

Validate a source mapping:

```bash
npdc-csv-extractor validate sources/example_npi_officers/source.yml sources/example_npi_officers/fixtures/officers.csv
```

Extract loader JSONL:

```bash
npdc-csv-extractor extract sources/example_npi_officers/source.yml sources/example_npi_officers/fixtures/officers.csv -o output/example.jsonl
```

Summarize an emitted JSONL file:

```bash
npdc-csv-extractor summarize output/example.jsonl
```

Check that allegations resolve to complaints (and, when officer files are
included, to officers) across several JSONL files:

```bash
npdc-csv-extractor check-links output/complaints.jsonl output/allegations.jsonl [output/officers.jsonl]
```

Create a new source skeleton (`--template example_complaints` for complaint data):

```bash
npdc-csv-extractor init-source my_source
```

## Loader Contract

Each emitted line is a JSON object with:

- `model`
- `source_uid`
- `url`
- `scraped_at`
- `data`

`scraped_at` is formatted as `YYYY-MM-DD HH:MM:SS` to match the current loader.

Model emitters:

- `agency`
- `unit`
- `officer`
- `complaint`
- `allegation`

The loader contract is the source of truth: `csv_data_extractor/loader_contract.py`
transcribes what `data-loaders` reads for complaints and allegations. A mapping
that sets a field the loader would drop, or one that would fail a load batch, is
rejected when the config loads.

Complaints and allegations follow a few loader rules that are easy to miss:

- An allegation's `complaint_id`, `officer_state_id`, and `complainant` sit at
  the top level of the item, beside `data`, because that is where the loader
  reads them.
- The loader finds an allegation's complaint by source UID plus complaint
  `record_id`. Missing it means a silent drop, so extract complaints and
  allegations with the same `--source-uid`, and run `check-links` on the
  outputs.
- Complaint dates, complainant demographics, and the source `record_type` load
  into constrained neomodel properties. Values the loader would store but
  neomodel cannot read back are errors at extraction time.

See `docs/source-scaffold.md` for the mapping format, `sources/example_complaints`
for a one-CSV template, and `sources/nyc_ccrb` for a real two-CSV source.

Existing scraper structures may be useful references, but CSV extractors should
adapt source data to the loader dialect.
