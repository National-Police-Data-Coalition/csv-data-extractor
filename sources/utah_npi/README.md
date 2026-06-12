# Utah National Police Index Source

This source maps the processed Utah CSV downloaded from:

`https://national.cpdp.co/states/utah?activeOnly=false`

The Utah page states that the employment-history data was obtained from Utah
Peace Officer Standards and Training under Utah GRAMA in July 2024.

## Strategy

The CSV is an employment-history table. It has many rows per officer, so this
extractor emits:

- one `agency` item per unique `agency_name`
- one `officer` item per unique `person_nbr`
- one employment stint per officer/agency/unit/rank, constructed from the CSV
  event rows

This pass does not emit explicit `unit` rows. Each employment uses
`unit_label: "Unknown"`, relying on the loader's agency ingest behavior, which
creates an `Unknown` unit for each agency.

For Utah, a stint is an interval where an officer held a given rank in a given
agency/unit. Rank changes become separate stints. Repeated rows for the same
officer, agency, unit, and rank are folded into one loader employment record
because the current loader merges employment by officer, unit, and
`highest_rank`.

The source UID is supplied at runtime:

```bash
npdc-csv-extractor validate sources/utah_npi/source.yml datasets/utah-processed.csv --source-uid <SOURCE_UID>
npdc-csv-extractor extract sources/utah_npi/source.yml datasets/utah-processed.csv --source-uid <SOURCE_UID> -o output/utah-npi.jsonl
```

## Data Notes

Known data-quality warnings are expected in the current CSV:

- a handful of future start/end dates
- several rows where start date is after end date
- non-active employment rows without an end date
