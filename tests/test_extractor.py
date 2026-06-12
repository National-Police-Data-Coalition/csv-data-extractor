from __future__ import annotations

import json
from pathlib import Path

from csv_data_extractor.config import load_source_config
from csv_data_extractor.extractor import (
    extract_csv,
    summarize_jsonl,
    write_issues_csv,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "sources" / "example_npi_officers" / "source.yml"
CSV = ROOT / "sources" / "example_npi_officers" / "fixtures" / "officers.csv"
UTAH_CONFIG = ROOT / "sources" / "utah_npi" / "source.yml"
UTAH_CSV = ROOT / "datasets" / "utah-processed.csv"


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


def test_summarize_rejects_source_csv() -> None:
    try:
        summarize_jsonl(CSV)
    except ValueError as exc:
        assert "expects loader JSONL" in str(exc)
    else:
        raise AssertionError("summarize_jsonl accepted source CSV")


def test_utah_source_requires_runtime_source_uid() -> None:
    config = load_source_config(UTAH_CONFIG)
    result = extract_csv(config, UTAH_CSV)

    assert result.has_errors
    assert result.issues[0].field == "source.uid"


def test_utah_source_extracts_full_dataset() -> None:
    config = load_source_config(UTAH_CONFIG).with_source_uid("test-source-uid")
    result = extract_csv(config, UTAH_CSV)

    assert not result.has_errors

    model_counts = {}
    employment_count = 0
    for item in result.items:
        model_counts[item.model] = model_counts.get(item.model, 0) + 1
        loader_item = item.to_loader_dict()
        assert loader_item["source_uid"] == "test-source-uid"
        if item.model == "officer":
            employment_count += len(loader_item["employment"])

    assert model_counts == {"agency": 265, "officer": 17573}
    assert employment_count == 27986
    assert len(result.issues) == 0


def test_utah_stint_builder_repairs_event_date_artifacts() -> None:
    config = load_source_config(UTAH_CONFIG).with_source_uid("test-source-uid")
    result = extract_csv(config, UTAH_CSV)

    officers = [
        item.to_loader_dict()
        for item in result.items
        if item.model == "officer"
    ]
    gq_nielsen = next(
        item for item in officers
        if item["data"]["state_ids"][0]["value"] == "0001-3761"
    )
    matthew_harding = next(
        item for item in officers
        if item["data"]["state_ids"][0]["value"] == "0002-7859"
    )
    martha_alejandre = next(
        item for item in officers
        if item["data"]["state_ids"][0]["value"] == "0001-3401"
    )

    assert gq_nielsen["employment"][0]["earliest_date"] == "2002-09-01"
    assert matthew_harding["employment"][0]["latest_date"] == "2022-08-23"

    south_ogden = next(
        emp for emp in martha_alejandre["employment"]
        if emp["agency_label"] == "South Ogden City Police Department"
    )
    assert "earliest_date" not in south_ogden
    assert south_ogden["latest_date"] == "2016-02-29"


def test_writes_validation_issue_report(tmp_path: Path) -> None:
    config = load_source_config(UTAH_CONFIG).with_source_uid("test-source-uid")
    result = extract_csv(config, UTAH_CSV)
    output = tmp_path / "issues.csv"

    write_issues_csv(result.issues, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert "person_nbr" in lines[0]
    assert len(lines) == 1
