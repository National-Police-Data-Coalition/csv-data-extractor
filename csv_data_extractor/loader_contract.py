"""What the NPDC loader reads for complaints and allegations.

Transcribed from data-loaders@714df2d (main). The JSONL contract is identical at
the vendored submodule's pin (f0e3b6d); only the complaint-key derivation changed.

  * loader/ingest/complaint.py   COMPLAINT_FIELDS, LOCATION_FIELDS, SOURCE_DETAILS_FIELDS
  * loader/ingest/allegation.py  ALLEGATION_FIELDS, CIVILIAN_FIELDS; complaint_id,
                                 officer_state_id and complainant are read at TOP level
  * loader/domain/complaint.py   DateProperty fields, ComplaintSourceRel.record_type
  * loader/domain/types/enums.py Ethnicity, Gender choices (Civilian)

Fields outside these lists are silently dropped by the loader, so the extractor
rejects them at config time rather than emitting data that never lands.
"""
from __future__ import annotations

COMPLAINT_FIELDS = (
    "category",
    "incident_date",
    "received_date",
    "closed_date",
    "updated_date",
    "reason_for_contact",
    "outcome_of_contact",
    "civilian_witnesses",
    "attachments",
    "civilian_review_board_uid",
    "police_witnesses",
    "allegations",
    "investigations",
    "penalties",
)

# In COMPLAINT_FIELDS but list-valued: the loader passes them straight into
# `SET c += row.props`, and Neo4j cannot store a list of maps, so one structured
# value fails the whole batch. A mapping may not set them.
COMPLAINT_LIST_FIELDS = (
    "civilian_witnesses",
    "attachments",
    "police_witnesses",
    "allegations",
    "investigations",
    "penalties",
)

LOCATION_FIELDS = (
    "location_type",
    "location_description",
    "address",
    "city",
    "state",
    "zip",
    "administrative_area",
    "administrative_area_type",
)

SOURCE_DETAILS_FIELDS = (
    "record_type",
    "date_published",
    "court",
    "judge",
    "docket_number",
    "case_event_date",
    "publication_name",
    "publication_url",
    "author",
    "author_url",
    "author_email",
    "reporting_agency",
    "reporting_agency_url",
    "reporting_agency_email",
)

ALLEGATION_FIELDS = (
    "allegation",
    "type",
    "subtype",
    "recommended_finding",
    "recommended_outcome",
    "finding",
    "outcome",
)

CIVILIAN_FIELDS = ("age", "age_range", "ethnicity", "gender")

STATE_ID_FIELDS = ("state", "id_name", "value")

# neomodel DateProperty.inflate is strptime(value, "%Y-%m-%d"): any other stored
# shape writes fine through raw Cypher and raises on every later read.
COMPLAINT_DATE_FIELDS = ("incident_date", "received_date", "closed_date")
SOURCE_DETAILS_DATE_FIELDS = ("date_published", "case_event_date")

# choices= constraints: values outside these write fine and raise on read.
ETHNICITY_CHOICES = (
    "Unknown",
    "White",
    "Black/African American",
    "American Indian/Alaska Native",
    "Asian",
    "Native Hawaiian/Pacific Islander",
    "Hispanic/Latino",
)
GENDER_CHOICES = ("Other", "Male", "Female")
RECORD_TYPE_CHOICES = ("legal", "news", "government", "personal")

# (model, path) -> allowed values, checked on every emitted item.
CHOICES: dict[tuple[str, str], tuple[str, ...]] = {
    ("complaint", "source_details.record_type"): RECORD_TYPE_CHOICES,
    ("allegation", "complainant.ethnicity"): ETHNICITY_CHOICES,
    ("allegation", "complainant.gender"): GENDER_CHOICES,
}
