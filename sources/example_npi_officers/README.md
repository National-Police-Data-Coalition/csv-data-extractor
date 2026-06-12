# Example NPI Officers Source

This source is a small, copyable example based on the National Police Index CSV
patterns in `vendor/police-data-trust-scrapers`.

It emits:

- one `agency` item per unique agency/state
- one `unit` item per unique agency/unit/state
- one `officer` item per unique state ID, with loader-compatible employment
  records

Run it with:

```bash
npdc-csv-extractor validate sources/example_npi_officers/source.yml sources/example_npi_officers/fixtures/officers.csv
npdc-csv-extractor extract sources/example_npi_officers/source.yml sources/example_npi_officers/fixtures/officers.csv -o output/example_npi_officers.jsonl
```
