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

Create a new source skeleton:

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

Currently scaffolded model emitters:

- `agency`
- `unit`
- `officer`

The loader also has partial support for `complaint` and `allegation`, but those
emitters are not scaffolded yet. When they are added, the loader contract is the
source of truth. Existing scraper structures may be useful references, but CSV
extractors should adapt source data to the loader dialect.
