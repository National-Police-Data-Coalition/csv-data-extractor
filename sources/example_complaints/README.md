# Example Complaints Source

A small, copyable example of complaint and allegation extraction. It reads one
allegation-level CSV, where each row is an allegation and the complaint's
columns repeat on every row. From it, it emits:

- one `complaint` item per unique `complaint_number`, with `location` and
  `source_details`
- one `allegation` item per row, with the parent `complaint_id`, an
  `officer_state_id` when the row names an officer, and a `complainant`

Its officer IDs (`TX` / `NPI ID` / `1001`, `1002`) are the officers in
`sources/example_npi_officers`, so the two examples link up:

```bash
npdc-csv-extractor extract sources/example_complaints/source.yml sources/example_complaints/fixtures/complaints.csv --source-uid <SOURCE_UID> -o output/example_complaints.jsonl
npdc-csv-extractor extract sources/example_npi_officers/source.yml sources/example_npi_officers/fixtures/officers.csv -o output/example_npi_officers.jsonl
npdc-csv-extractor check-links output/example_complaints.jsonl output/example_npi_officers.jsonl
```

Start a new complaint source from it with:

```bash
npdc-csv-extractor init-source my_complaints --template example_complaints
```

`expected/complaints.jsonl` is the output for `--source-uid test-source-uid`, with
`scraped_at` fixed to `2026-10-01 00:00:00`.
