"""Exercises staff-page parsing and name matching against hand-built,
synthetic HTML/data -- no real colleague data, no network access."""
from __future__ import annotations

from orcid_etl.discover import (
    OrcidCandidate,
    StaffMember,
    filter_by_iisg_mention,
    match_reason,
    match_staff_to_candidates,
    normalize_name,
    parse_staff_page,
    write_candidates_report,
)

STAFF_HTML = """
<div class="view-content">
  <div class="views-row">
    <div class="node">
      <h2 class="title-heading h3">
        <a class="full-click__trigger" href="/en/about/staff/ada-testperson">
          <span class="field field--name-title field--type-string">Ada Testperson</span>
        </a>
      </h2>
      <div class="field field--name-body field--type-text-with-summary">
        <div class="field__item"><p>Researcher</p></div>
      </div>
    </div>
  </div>
  <div class="views-row">
    <div class="node">
      <h2 class="title-heading h3">
        <a class="full-click__trigger" href="/en/node/999">
          <span class="field field--name-title field--type-string">No Match Person</span>
        </a>
      </h2>
      <div class="field field--name-body field--type-text-with-summary">
        <div class="field__item"><p>Librarian</p></div>
      </div>
    </div>
  </div>
</div>
"""


def test_parse_staff_page():
    members = parse_staff_page(STAFF_HTML)
    assert members == [
        StaffMember(name="Ada Testperson", role="Researcher", profile_url="/en/about/staff/ada-testperson"),
        StaffMember(name="No Match Person", role="Librarian", profile_url="/en/node/999"),
    ]


def test_normalize_name_strips_accents_and_case():
    assert normalize_name("Ada Tëst-Pérson") == "ada test person"


def test_match_exact_ignores_name_order_and_accents():
    staff = [StaffMember(name="Ada Tëstperson", role="Researcher", profile_url="/x")]
    candidates = {
        "0000-0000-0000-0001": OrcidCandidate(
            orcid_id="0000-0000-0000-0001",
            given_names="Testperson",
            family_names="Ada",
            institution_names=("Example Institute",),
        )
    }
    matches = match_staff_to_candidates(staff, candidates)
    assert len(matches) == 1
    assert matches[0]["confidence"] == "exact"
    assert matches[0]["orcid_id"] == "0000-0000-0000-0001"


def test_match_exact_ignores_middle_initials():
    staff = [StaffMember(name="Marco van Leeuwen", role="Fellow", profile_url="/x")]
    candidates = {
        "0000-0001-6459-7051": OrcidCandidate(
            orcid_id="0000-0001-6459-7051",
            given_names="Marco H.D.",
            family_names="van Leeuwen",
            institution_names=(),
        )
    }
    matches = match_staff_to_candidates(staff, candidates)
    assert len(matches) == 1
    assert matches[0]["confidence"] == "exact"


def test_match_partial_on_dropped_middle_name():
    staff = [StaffMember(name="Ada Maria Testperson", role="", profile_url="")]
    candidates = {
        "0000-0000-0000-0002": OrcidCandidate(
            orcid_id="0000-0000-0000-0002",
            given_names="Ada",
            family_names="Testperson",
            institution_names=(),
        )
    }
    matches = match_staff_to_candidates(staff, candidates)
    assert len(matches) == 1
    assert matches[0]["confidence"] == "partial"


def test_no_match_for_unrelated_names():
    staff = [StaffMember(name="No Match Person", role="", profile_url="")]
    candidates = {
        "0000-0000-0000-0003": OrcidCandidate(
            orcid_id="0000-0000-0000-0003",
            given_names="Someone",
            family_names="Else",
            institution_names=(),
        )
    }
    assert match_staff_to_candidates(staff, candidates) == []


def test_match_reason_on_english_affiliation_name():
    record = {"activities-summary": {"employments": {"affiliation-group": [
        {"summaries": [{"employment-summary": {"organization": {"name": "International Institute of Social History"}}}]}
    ]}}}
    assert match_reason(record) == 'mentions "international institute of social history"'


def test_match_reason_on_dutch_affiliation_name():
    record = {"activities-summary": {"employments": {"affiliation-group": [
        {"summaries": [{"employment-summary": {"organization": {"name": "Internationaal Instituut voor Sociale Geschiedenis"}}}]}
    ]}}}
    assert match_reason(record) is not None


def test_match_reason_on_employment_url_only():
    # organization name alone (e.g. "IISG") wouldn't satisfy the rule --
    # it's the employment's own url, pointing at the staff bio page, that does.
    record = {"activities-summary": {"employments": {"affiliation-group": [
        {"summaries": [{"employment-summary": {
            "organization": {"name": "IISG"},
            "url": {"value": "https://iisg.amsterdam/en/about/staff/marco-van-leeuwen"},
        }}]}
    ]}}}
    assert match_reason(record) == "a URL on the profile points to iisg.amsterdam"


def test_match_reason_none_when_no_mention():
    record = {"activities-summary": {"employments": {"affiliation-group": [
        {"summaries": [{"employment-summary": {"organization": {"name": "Some Other University"}}}]}
    ]}}}
    assert match_reason(record) is None


def test_filter_by_iisg_mention_drops_unverified_and_missing_records():
    matches = [
        {"orcid_id": "0000-0000-0000-0001", "staff_name": "A"},
        {"orcid_id": "0000-0000-0000-0002", "staff_name": "B"},
        {"orcid_id": "0000-0000-0000-0003", "staff_name": "C (no record fetched)"},
    ]
    records = {
        "0000-0000-0000-0001": {"org": "International Institute of Social History"},
        "0000-0000-0000-0002": {"org": "Unrelated University"},
    }
    filtered = filter_by_iisg_mention(matches, records)
    assert [m["orcid_id"] for m in filtered] == ["0000-0000-0000-0001"]
    assert "match_reason" in filtered[0]


def test_write_candidates_report_removes_stale_file_when_no_matches(tmp_path):
    path = tmp_path / "orcid-etl-candidates.md"
    path.write_text("stale content")
    write_candidates_report([], path)
    assert not path.exists()


def test_write_candidates_report_lists_each_match(tmp_path):
    path = tmp_path / "orcid-etl-candidates.md"
    matches = [
        {
            "staff_name": "Ada Testperson",
            "role": "Researcher",
            "staff_profile_url": "/en/about/staff/ada-testperson",
            "orcid_id": "0000-0000-0000-0001",
            "candidate_name": "Testperson Ada",
            "confidence": "exact",
            "match_reason": 'mentions "international institute of social history"',
        }
    ]
    write_candidates_report(matches, path)
    text = path.read_text()
    assert "Ada Testperson" in text
    assert "0000-0000-0000-0001" in text
    assert "exact" in text
