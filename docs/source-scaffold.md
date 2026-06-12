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

Use the CLI to create a starter copy:

```bash
npdc-csv-extractor init-source <source_name>
```

## Mapping Format

`source.yml` describes a source and one or more model mappings. Each mapping
turns CSV rows into loader JSONL items.

Supported model emitters:

- `agency`
- `unit`
- `officer`

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

The scaffold formats `scraped_at` as `YYYY-MM-DD HH:MM:SS`, which is what the
current loader expects.
