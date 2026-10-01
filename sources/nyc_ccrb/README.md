# NYC CCRB Complaints and Allegations

Maps two NYC Open Data extracts from the Civilian Complaint Review Board:

| config | dataset | emits |
|---|---|---|
| `complaints.yml` | Complaints Against Police Officers (`2mby-ccnw`) | one `complaint` per `Complaint Id` |
| `allegations.yml` | Allegations Against Police Officers (`6xgr-kwjq`) | one `allegation` per row |

The datasets are separate CSVs, so each gets its own config and run. Use the same
`--source-uid` for both: the loader finds an allegation's complaint by source UID
plus complaint ID, and drops the allegation silently if the lookup misses.

```bash
npdc-csv-extractor extract sources/nyc_ccrb/complaints.yml datasets/nyc_ccrb/Civilian_Complaint_Review_Board__Complaints_Against_Police_Officers_20260903.csv --source-uid <SOURCE_UID> -o output/nyc_ccrb_complaints.jsonl
npdc-csv-extractor extract sources/nyc_ccrb/allegations.yml datasets/nyc_ccrb/Civilian_Complaint_Review_Board__Allegations_Against_Police_Officers_20260903.csv --source-uid <SOURCE_UID> -o output/nyc_ccrb_allegations.jsonl
npdc-csv-extractor check-links output/nyc_ccrb_complaints.jsonl output/nyc_ccrb_allegations.jsonl
```

Load complaints before allegations.

## Mapping notes

- `CCRB Complaint Disposition` (32 values) goes to `category`, an unconstrained
  string in both the loader's domain and the website backend.
- Borough → `location.city`; precinct → `location.administrative_area`, with
  `administrative_area_type: precinct`. Source details record the data as
  `government`, reported by NYC Open Data.
- `Tax ID` becomes `officer_state_id` (`NY` / `Tax ID`). It is blank on about 39% of
  allegation rows, and those allegations load without an officer.
- The complainant's ethnicity comes from the legacy race column, falling back to the
  newer free-form one. It is mapped to the loader's `Ethnicity` values: exact
  matches first, then keyword rules for the free-form values. `Other Race`,
  `Refused`, `Other (R): …` and `Middle Eastern/North African` have no enum member
  and are dropped. Gender `NA` is dropped the same way.

## Data notes

Against the 2026-09-03 extract:

- 141,539 complaints and 431,908 allegations, with no errors.
- 1,290 warnings, all `incident_date`: CCRB redacts some incident dates to a
  year or a year-month (`2022-__-__`). Those cannot be stored in a
  `DateProperty`, so the value is dropped and the rest of the complaint loads.
- 263,039 allegations carry an officer ID. Add the officer JSONL to `check-links`
  to see which ones resolve.

This output matches, record for record, the CCRB conversion that was loaded and
verified in the NPDC graph on 2026-10-01 (`ccrb-converter`).

## Not covered here

Officers and units come from the separate Police Officers dataset (`2fir-qns4`),
whose unit names need an evidence-based expansion step. Penalties
(`keep-pkmh`) have no loader handler yet.

## Fixtures

`fixtures/` holds 5 complaints and their 16 allegations, chosen to cover redacted
dates, blank Tax IDs, legacy and free-form race values, `Refused`,
`Other Race`, `Decline to Answer`, and gender `NA` and `TGNC / Other`. `expected/`
is their output for `--source-uid test-source-uid`, with `scraped_at` fixed to
`2026-10-01 00:00:00`.
