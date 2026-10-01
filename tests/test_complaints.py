from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from csv_data_extractor.cli import app
from csv_data_extractor.config import SourceConfig, load_source_config
from csv_data_extractor.extractor import check_links, extract_csv, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "sources" / "example_complaints"
CCRB = ROOT / "sources" / "nyc_ccrb"
CCRB_DATA = ROOT / "datasets" / "nyc_ccrb"
OFFICERS = ROOT / "sources" / "example_npi_officers"


def _config(*mappings: dict) -> SourceConfig:
    return SourceConfig.model_validate({
        "source": {"uid": "test-source-uid", "name": "Test", "url": "https://example.org"},
        "defaults": {"state": "TX"},
        "mappings": list(mappings),
    })


def _complaint_mapping(**overrides) -> dict:
    mapping = {
        "model": "complaint",
        "fields": {"record_id": "cid"},
        "source_details": {"record_type": {"value": "government"}},
    }
    mapping.update(overrides)
    return mapping


def _allegation_mapping(**overrides) -> dict:
    mapping = {
        "model": "allegation",
        "fields": {"record_id": "aid"},
        "complaint_id": "cid",
    }
    mapping.update(overrides)
    return mapping


def _csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rows.csv"
    path.write_text(text, encoding="utf-8")
    return path


def _loader_lines(path: Path) -> list[dict]:
    """Expected files fix scraped_at; compare everything else."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        obj.pop("scraped_at")
        out.append(obj)
    return out


def _items(result) -> list[dict]:
    out = []
    for item in result.items:
        obj = item.to_loader_dict()
        obj.pop("scraped_at")
        out.append(obj)
    return out


# --- the example source ---------------------------------------------------------

def test_example_complaints_match_expected_output() -> None:
    config = load_source_config(EXAMPLE / "source.yml").with_source_uid("test-source-uid")
    result = extract_csv(config, EXAMPLE / "fixtures" / "complaints.csv")

    assert result.issues == []
    assert _items(result) == _loader_lines(EXAMPLE / "expected" / "complaints.jsonl")
    counts = {}
    for item in result.items:
        counts[item.model] = counts.get(item.model, 0) + 1
    assert counts == {"complaint": 2, "allegation": 4}


def test_allegation_links_and_people_sit_at_the_top_level() -> None:
    config = load_source_config(EXAMPLE / "source.yml").with_source_uid("test-source-uid")
    items = _items(extract_csv(config, EXAMPLE / "fixtures" / "complaints.csv"))
    first = next(i for i in items if i["model"] == "allegation")

    assert first["complaint_id"] == "C-2024-0001"
    assert first["officer_state_id"] == {"state": "TX", "id_name": "NPI ID", "value": "1001"}
    assert first["complainant"] == {"age": 34, "ethnicity": "Black/African American",
                                    "gender": "Male"}
    assert not {"complaint_id", "officer_state_id", "complainant"} & set(first["data"])

    no_officer = next(i for i in items if i.get("data", {}).get("record_id") == "C-2024-0002-A2")
    assert "officer_state_id" not in no_officer


def test_example_links_to_the_npi_officers(tmp_path: Path) -> None:
    complaints = extract_csv(
        load_source_config(EXAMPLE / "source.yml").with_source_uid("test-source-uid"),
        EXAMPLE / "fixtures" / "complaints.csv",
    )
    officers = extract_csv(load_source_config(OFFICERS / "source.yml"),
                           OFFICERS / "fixtures" / "officers.csv")
    write_jsonl(complaints.items, tmp_path / "complaints.jsonl")
    write_jsonl(officers.items, tmp_path / "officers.jsonl")

    result, stats = check_links([tmp_path / "complaints.jsonl", tmp_path / "officers.jsonl"])

    assert not result.has_errors and result.issues == []
    assert stats["allegations linked to a complaint"] == 4
    assert stats["allegations linked to an officer"] == 3
    assert stats["allegations without an officer id"] == 1


# --- complaint behaviour -------------------------------------------------------------

def test_complaints_fold_repeats_and_report_conflicts(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(fields={"record_id": "cid", "category": "cat"}))
    rows = _csv(tmp_path, "cid,cat\n1,open\n1,open\n2,closed\n2,reopened\n")

    result = extract_csv(config, rows)

    assert [i.data["record_id"] for i in result.items] == ["1", "2"]
    assert result.items[1].data["category"] == "closed"
    [warning] = result.issues
    assert warning.severity == "warning" and "['category']" in warning.message
    assert warning.row_number == 5


def test_unparseable_dates_are_dropped_with_a_warning(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(
        fields={"record_id": "cid", "incident_date": {"column": "d", "transform": "date"}}))
    rows = _csv(tmp_path, "cid,d\n1,2022-__-__\n2,03/04/2020 10:00:00 PM\n")

    result = extract_csv(config, rows)

    assert not result.has_errors
    assert "incident_date" not in result.items[0].data
    assert result.items[1].data["incident_date"] == "2020-03-04"
    [warning] = result.issues
    assert warning.field == "incident_date" and warning.context["value"] == "2022-__-__"


def test_dates_must_reach_the_loader_as_iso(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(fields={"record_id": "cid", "closed_date": "d"}))
    result = extract_csv(config, _csv(tmp_path, "cid,d\n1,03/04/2020\n"))

    assert result.has_errors and result.items == []
    assert result.issues[0].field == "closed_date"


def test_record_type_must_be_a_loader_choice(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(source_details={"record_type": "kind"}))
    result = extract_csv(config, _csv(tmp_path, "cid,kind\n1,government\n2,blog\n"))

    assert [i.data["record_id"] for i in result.items] == ["1"]
    assert result.issues[0].field == "source_details.record_type"
    assert result.issues[0].context["value"] == "blog"


# --- allegation behaviour ------------------------------------------------------------

def test_allegations_need_record_and_complaint_ids(tmp_path: Path) -> None:
    config = _config(_allegation_mapping())
    result = extract_csv(config, _csv(tmp_path, "aid,cid\nA1,\n,C1\nA3,C1\n"))

    assert [i.data["record_id"] for i in result.items] == ["A3"]
    assert sorted(i.field for i in result.issues) == ["complaint_id", "record_id"]


def test_incomplete_officer_ids_are_omitted(tmp_path: Path) -> None:
    config = _config(_allegation_mapping(officer_state_id={
        "state": "st", "id_name": {"value": "Tax ID"}, "value": "tax"}))
    rows = _csv(tmp_path, "aid,cid,st,tax\nA1,C1,NY,123\nA2,C1,,456\nA3,C1,NY,\n")

    result = extract_csv(config, rows)
    extras = [i.model_extra for i in result.items]

    assert extras[0]["officer_state_id"] == {"state": "NY", "id_name": "Tax ID", "value": "123"}
    assert "officer_state_id" not in extras[1] and "officer_state_id" not in extras[2]
    [warning] = result.issues
    assert warning.row_number == 3 and warning.field == "officer_state_id"


def test_complainant_values_must_be_loader_choices(tmp_path: Path) -> None:
    config = _config(_allegation_mapping(complainant={"gender": "g"}))
    result = extract_csv(config, _csv(tmp_path, "aid,cid,g\nA1,C1,Male\nA2,C1,M\n"))

    assert [i.data["record_id"] for i in result.items] == ["A1"]
    assert result.issues[0].field == "complainant.gender"


def test_duplicate_allegations_are_reported(tmp_path: Path) -> None:
    config = _config(_allegation_mapping())
    result = extract_csv(config, _csv(tmp_path, "aid,cid\nA1,C1\nA1,C1\nA1,C2\n"))

    assert len(result.items) == 3
    [warning] = result.issues
    assert warning.row_number == 3 and "first on row 2" in warning.message


def test_run_with_complaints_flags_orphan_allegations(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(), _allegation_mapping(complaint_id="parent"))
    result = extract_csv(config, _csv(tmp_path, "cid,aid,parent\nC1,A1,C1\nC2,A2,C9\n"))

    [warning] = result.issues
    assert warning.field == "complaint_id" and warning.context["complaint_id"] == "C9"


# --- value mapping ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("policy", "kept", "severity"),
    [("keep", "Purple", None), ("null", None, None), ("warn", None, "warning"),
     ("error", None, "error")],
)
def test_unmapped_policies(tmp_path: Path, policy, kept, severity) -> None:
    config = _config(_complaint_mapping(fields={
        "record_id": "cid",
        "category": {"column": "c", "map": {"red": "Red"}, "unmapped": policy},
    }))
    result = extract_csv(config, _csv(tmp_path, "cid,c\n1,RED\n2,Purple\n"))

    assert result.items[0].data["category"] == "Red"
    if severity == "error":
        assert len(result.items) == 2 and result.has_errors
    assert result.items[1].data.get("category") == kept
    assert [i.severity for i in result.issues] == ([severity] if severity else [])


def test_contains_rules_apply_in_order_after_exact_matches(tmp_path: Path) -> None:
    spec = {"column": "e", "map": {"black": "Black/African American"},
            "contains": [["hispanic", "Hispanic/Latino"], ["black", "Black/African American"]],
            "unmapped": "null"}
    config = _config(_allegation_mapping(complainant={"ethnicity": spec}))
    rows = _csv(tmp_path, 'aid,cid,e\nA1,C1,Black\nA2,C1,"Black (R); Hispanic (E)"\nA3,C1,Other\n')

    result = extract_csv(config, rows)

    assert [i.model_extra.get("complainant") for i in result.items] == [
        {"ethnicity": "Black/African American"}, {"ethnicity": "Hispanic/Latino"}, None]


def test_column_lists_take_the_first_non_blank(tmp_path: Path) -> None:
    config = _config(_complaint_mapping(
        fields={"record_id": "cid", "category": {"column": ["legacy", "current"]}}))
    result = extract_csv(config, _csv(tmp_path, "cid,legacy,current\n1,old,new\n2,,new\n"))

    assert [i.data["category"] for i in result.items] == ["old", "new"]


def test_transform_failures_no_longer_crash_existing_models(tmp_path: Path) -> None:
    config = _config({
        "model": "officer",
        "required": ["first_name", "last_name"],
        "fields": {"first_name": "first", "last_name": "last",
                   "year_of_birth": {"column": "born", "transform": "int"}},
        "state_id": {"state": {"value": "TX"}, "id_name": {"value": "NPI ID"}, "value": "id"},
    })
    result = extract_csv(config, _csv(tmp_path, "first,last,born,id\nJo,Lee,abc,7\n"))

    assert not result.has_errors and "year_of_birth" not in result.items[0].data
    assert result.issues[0].field == "year_of_birth"


# --- config-time contract checks ---------------------------------------------------------

@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        (_complaint_mapping(fields={"record_id": "cid", "allegations": "a"}), "list-valued"),
        (_complaint_mapping(fields={"record_id": "cid", "disposition": "d"}),
         "not read by the loader"),
        (_complaint_mapping(fields={"category": "c"}), "fields.record_id is required"),
        (_complaint_mapping(source_details={"reporting_agency": "a"}),
         "record_type is required"),
        (_complaint_mapping(location={"precinct": "p"}), "location fields not read"),
        (_allegation_mapping(complaint_id=None), "complaint_id is required"),
        (_allegation_mapping(officer_state_id={"value": "tax"}), "officer_state_id needs"),
        (_allegation_mapping(complainant={"race": "r"}), "complainant fields not read"),
        ({"model": "officer", "location": {"city": "c"}}, "only valid on complaint"),
        (_complaint_mapping(fields={"record_id": {"column": "cid", "transfrom": "strip"}}),
         "unknown spec keys"),
        (_complaint_mapping(fields={"record_id": {"column": "cid", "transform": "slug"}}),
         "unknown transform"),
        (_complaint_mapping(fields={"record_id": {"column": "cid", "unmapped": "null"}}),
         "unmapped only applies"),
    ],
)
def test_mappings_that_break_the_loader_contract_are_rejected(mapping, message) -> None:
    with pytest.raises(ValidationError, match=message):
        _config(mapping)


# --- cross-file link check ----------------------------------------------------------------

def _jsonl(path: Path, *objs: dict) -> Path:
    path.write_text("".join(json.dumps(o) + "\n" for o in objs), encoding="utf-8")
    return path


def test_check_links_requires_the_same_source_uid(tmp_path: Path) -> None:
    complaints = _jsonl(tmp_path / "c.jsonl",
                        {"model": "complaint", "source_uid": "s1", "data": {"record_id": "C1"}})
    allegations = _jsonl(
        tmp_path / "a.jsonl",
        {"model": "allegation", "source_uid": "s1", "complaint_id": "C1",
         "data": {"record_id": "A1"}},
        {"model": "allegation", "source_uid": "s2", "complaint_id": "C1",
         "data": {"record_id": "A2"}},
    )

    result, stats = check_links([allegations, complaints])

    assert stats["allegations linked to a complaint"] == 1
    assert stats["allegations with no complaint"] == 1
    [error] = result.issues
    assert error.severity == "error" and "source s2" in error.message


def test_check_links_reports_unknown_officers(tmp_path: Path) -> None:
    files = [
        _jsonl(tmp_path / "c.jsonl",
               {"model": "complaint", "source_uid": "s", "data": {"record_id": "C1"}}),
        _jsonl(tmp_path / "o.jsonl",
               {"model": "officer", "source_uid": "s",
                "data": {"state_ids": [{"state": "NY", "id_name": "Tax ID", "value": "1"}]}}),
        _jsonl(tmp_path / "a.jsonl",
               {"model": "allegation", "source_uid": "s", "complaint_id": "C1",
                "officer_state_id": {"state": "NY", "id_name": "Tax ID", "value": "1"},
                "data": {"record_id": "A1"}},
               {"model": "allegation", "source_uid": "s", "complaint_id": "C1",
                "officer_state_id": {"state": "NY", "id_name": "Tax ID", "value": "2"},
                "data": {"record_id": "A2"}}),
    ]

    result, stats = check_links(files)

    assert not result.has_errors
    assert stats["allegations linked to an officer"] == 1
    assert stats["allegations whose officer is not in these files"] == 1


def test_check_links_command_exits_nonzero_on_orphans(tmp_path: Path) -> None:
    allegations = _jsonl(tmp_path / "a.jsonl",
                         {"model": "allegation", "source_uid": "s", "complaint_id": "C1",
                          "data": {"record_id": "A1"}})

    outcome = CliRunner().invoke(app, ["check-links", str(allegations)])

    assert outcome.exit_code == 1
    assert "allegations with no complaint" in outcome.output


def test_init_source_copies_the_complaints_template(tmp_path: Path) -> None:
    outcome = CliRunner().invoke(
        app, ["init-source", "mine", "--template", "example_complaints", "-d", str(tmp_path)])

    assert outcome.exit_code == 0
    assert (tmp_path / "mine" / "source.yml").read_text() == (EXAMPLE / "source.yml").read_text()


# --- NYC CCRB --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("config", "fixture", "expected", "warnings"),
    [("complaints.yml", "complaints-sample.csv", "complaints-sample.jsonl", 1),
     ("allegations.yml", "allegations-sample.csv", "allegations-sample.jsonl", 0)],
)
def test_ccrb_fixtures_match_expected_output(config, fixture, expected, warnings) -> None:
    source = load_source_config(CCRB / config).with_source_uid("test-source-uid")
    result = extract_csv(source, CCRB / "fixtures" / fixture)

    assert not result.has_errors
    assert len(result.issues) == warnings
    assert _items(result) == _loader_lines(CCRB / "expected" / expected)


def test_ccrb_fixture_outputs_link_up() -> None:
    result, stats = check_links([CCRB / "expected" / "complaints-sample.jsonl",
                                 CCRB / "expected" / "allegations-sample.jsonl"])

    assert result.issues == []
    assert stats["allegations linked to a complaint"] == 16


@pytest.mark.skipif(not CCRB_DATA.exists(), reason="full CCRB extract not in datasets/nyc_ccrb")
def test_ccrb_full_extract() -> None:
    complaints = extract_csv(
        load_source_config(CCRB / "complaints.yml").with_source_uid("test-source-uid"),
        CCRB_DATA / "Civilian_Complaint_Review_Board__Complaints_Against_Police_Officers_20260903.csv",
    )
    assert not complaints.has_errors
    assert len(complaints.items) == 141_539
    assert {i.field for i in complaints.issues} == {"incident_date"}
    assert len(complaints.issues) == 1_290

    allegations = extract_csv(
        load_source_config(CCRB / "allegations.yml").with_source_uid("test-source-uid"),
        CCRB_DATA / "Civilian_Complaint_Review_Board__Allegations_Against_Police_Officers_20260903.csv",
    )
    assert allegations.issues == []
    assert len(allegations.items) == 431_908
    assert sum(1 for i in allegations.items if "officer_state_id" in i.model_extra) == 263_039
