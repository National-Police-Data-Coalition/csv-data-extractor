from __future__ import annotations

import json
from pathlib import Path

from csv_data_extractor.config import load_source_config
from csv_data_extractor.extractor import extract_csv, summarize_jsonl, write_jsonl


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "sources" / "example_npi_officers" / "source.yml"
CSV = ROOT / "sources" / "example_npi_officers" / "fixtures" / "officers.csv"


def test_extracts_loader_compatible_items() -> None:
    config = load_source_config(CONFIG)
    result = extract_csv(config, CSV)

    assert not result.has_errors
    assert [issue.severity for issue in result.issues] == []

    items = [item.to_loader_dict() for item in result.items]
    model_counts = {}
    for item in items:
        model_counts[item["model"]] = model_counts.get(item["model"], 0) + 1
        assert item["source_uid"] == "d4254e0c94034e77be95e1b8dc7bb661"
        assert item["url"] == "https://invisible.institute/national-police-index"
        assert "T" not in item["scraped_at"]

    assert model_counts == {"agency": 2, "unit": 3, "officer": 2}


def test_officer_rows_are_deduped_with_multiple_employments() -> None:
    config = load_source_config(CONFIG)
    result = extract_csv(config, CSV)

    officers = [
        item.to_loader_dict()
        for item in result.items
        if item.model == "officer"
    ]
    jane = next(
        item for item in officers
        if item["data"]["state_ids"][0]["value"] == "1001"
    )

    assert jane["data"]["first_name"] == "Jane"
    assert jane["data"]["year_of_birth"] == 1981
    assert len(jane["employment"]) == 2
    assert jane["employment"][0]["agency_label"] == "Austin Police Department"
    assert jane["employment"][0]["unit_label"] == "Patrol"
    assert jane["employment"][0]["earliest_date"] == "2020-01-15"


def test_write_and_summarize_jsonl(tmp_path: Path) -> None:
    config = load_source_config(CONFIG)
    result = extract_csv(config, CSV)
    output = tmp_path / "out.jsonl"

    write_jsonl(result.items, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7
    assert json.loads(lines[0])["model"] == "agency"
    assert summarize_jsonl(output) == {"agency": 2, "officer": 2, "unit": 3}
