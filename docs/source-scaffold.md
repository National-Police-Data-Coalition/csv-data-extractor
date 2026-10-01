# Source Scaffold

The extractor scaffold is Python-first and emits loader-compatible JSONL.

## Source Directory

New sources should start from this shape:

```text
sources/<source_name>/
  README.md
  source.yml
  fixtures/
    sample.csv
  expected/
    sample.jsonl
```

Use the CLI to create a starter copy. Copy `example_complaints` for complaint data:

```bash
npdc-csv-extractor init-source <source_name>
npdc-csv-extractor init-source <source_name> --template example_complaints
```

A source whose models arrive as separate CSVs gets one config per CSV, e.g.
`sources/nyc_ccrb/complaints.yml` and `allegations.yml`. Each one is extracted on its
own run.

## Mapping Format

`source.yml` describes a source and one or more model mappings. Each mapping
turns CSV rows into loader JSONL items.

Supported model emitters:

- `agency`
- `unit`
- `officer`
- `complaint`
- `allegation`

Each field can be a CSV column name:

```yaml
first_name: "first_name"
```

Or an object with a column, default/value, and transforms:

```yaml
year_of_birth:
  column: "birth_year"
  transform: "int"
```

Constants use `value`; fallback values use `default`.

```yaml
hq_state:
  default: "$defaults.state"
```

A list of columns takes the first non-blank one:

```yaml
ethnicity:
  column: ["race_legacy", "race_current"]
```

`map` translates source vocabularies; matching ignores case and surrounding
spaces. `contains` rules are tried in order after `map` misses, with the first
substring hit winning. `unmapped` decides what happens to a value neither one
matches: `keep` (the default), `null`, `warn` (null plus a warning), or
`error`. A mapping to `null` drops the value deliberately. Quote YAML keys such
as `"Yes"` and `"No"`, which YAML otherwise reads as booleans.

```yaml
gender:
  column: "complainant_gender"
  map:
    "M": "Male"
    "F": "Female"
  contains:
    - ["nonbinary", "Other"]
  unmapped: "null"
```

The order is: column → `default` → `transform` → `map` / `contains` →
`unmapped`. A transform that cannot convert a cell, such as a redacted date like
`2022-__-__`, drops that value with a row-level warning instead of stopping the
run.

Built-in transforms:

- `strip`
- `title`
- `upper`
- `int`
- `date`

## Loader-Specific Rules

Agency rows must include `name` and `hq_state`.

Unit rows must include `data.name`, `data.hq_state`, top-level `agency`, and
top-level `a_hq_state`.

Officer rows must include `first_name`, `last_name`, and a complete primary
state ID. Employment records must include `agency_label`, `a_hq_state`,
`unit_label`, and `u_hq_state` or they will be skipped with a warning.

Complaint rows must include `data.record_id`, the loader's MERGE key. Complaint
fields go under `fields`; the nested sections go under `location` and
`source_details`, which the loader writes onto the complaint's `HAS_SOURCE`
relationship:

```yaml
- model: "complaint"
  fields:
    record_id: "complaint_number"
    received_date: {column: "received", transform: "date"}
  location:
    city: "city"
    state: {value: "$defaults.state"}
  source_details:
    record_type: {value: "government"}   # required: legal, news, government, personal
```

- One complaint is emitted per `record_id`, so allegation-level CSVs can repeat
  the complaint columns on every row. A repeat with different values is
  reported once, and the first row wins.
- `incident_date`, `received_date`, and `closed_date` (and `date_published` and
  `case_event_date` under `source_details`) must reach the loader as
  `YYYY-MM-DD`. neomodel's `DateProperty` cannot read any other shape, so use
  `transform: date`.
- The list-valued complaint fields (`civilian_witnesses`, `attachments`,
  `police_witnesses`, `allegations`, `investigations`, `penalties`) cannot be
  mapped. Neo4j rejects them and the whole load batch fails.

Allegation rows must include `data.record_id` and a `complaint_id`: the parent
complaint's `record_id`.

```yaml
- model: "allegation"
  fields:
    record_id: "allegation_number"
    allegation: "allegation"
  complaint_id: "complaint_number"
  officer_state_id:
    state: {value: "$defaults.state"}
    id_name: {value: "NPI ID"}
    value: "officer_npi"
  complainant:
    age: {column: "complainant_age", transform: "int"}
    gender: {column: "complainant_gender", map: {"M": "Male"}, unmapped: "null"}
```

- `complaint_id`, `officer_state_id`, and `complainant` are emitted at the top
  level of the item, where the loader reads them, not inside `data`.
- The loader looks up the complaint by source UID plus `complaint_id`, and
  silently drops the allegation if the complaint is not there. Extract
  complaints and allegations with the same `--source-uid`, then run
  `npdc-csv-extractor check-links` on both outputs. A run that emits both models
  checks this itself.
- `officer_state_id` is omitted when its `value` is blank, and the allegation
  loads without an officer. If a value is present but `state` or `id_name` is
  missing, the ID is dropped with a warning.
- `complainant.ethnicity` and `complainant.gender` only accept the loader's enum
  values (`Ethnicity`, `Gender`). Anything else is an error, because it would
  load and then raise on read. Map the source vocabulary, or set
  `unmapped: null`.

A mapping that sets a field these loaders do not read is rejected when the config
loads, rather than emitting data that never lands.

The scaffold formats `scraped_at` as `YYYY-MM-DD HH:MM:SS`, which is what the
current loader expects.
