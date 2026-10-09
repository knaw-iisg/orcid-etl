"""Exercises staff_monitor's pure logic against hand-built data -- no
network access, matching test_discover.py's convention."""
from __future__ import annotations

from orcid_etl.discover import StaffMember
from orcid_etl.staff_monitor import (
    NewStaffResult,
    build_report,
    filter_known_orcids,
    find_departed,
    find_new_staff,
    is_scrape_anomaly,
    select_auto_add_candidates,
)


def test_is_scrape_anomaly_detects_big_drop():
    assert is_scrape_anomaly(previous_count=50, current_count=5)


def test_is_scrape_anomaly_allows_small_drop():
    assert not is_scrape_anomaly(previous_count=50, current_count=47)


def test_is_scrape_anomaly_ignores_first_run():
    assert not is_scrape_anomaly(previous_count=0, current_count=3)


def test_find_departed_matches_by_normalized_name():
    colleagues = [
        {"orcid": "0000-0000-0000-0001", "staff_name": "Ada Testperson"},
        {"orcid": "0000-0000-0000-0002"},  # no staff_name -- hand-added, never auto-removed
    ]
    current_staff = [StaffMember(name="Someone Else", role="", profile_url="")]
    departed = find_departed(colleagues, current_staff)
    assert len(departed) == 1
    assert departed[0]["orcid"] == "0000-0000-0000-0001"


def test_find_departed_keeps_still_present_staff():
    colleagues = [{"orcid": "0000-0000-0000-0001", "staff_name": "Ada Tëstperson"}]
    current_staff = [StaffMember(name="Ada Testperson", role="Researcher", profile_url="/x")]
    assert find_departed(colleagues, current_staff) == []


def test_find_new_staff_excludes_known_and_curated():
    current_staff = [
        StaffMember(name="Known Yesterday", role="", profile_url=""),
        StaffMember(name="Already Curated", role="", profile_url=""),
        StaffMember(name="Brand New Person", role="", profile_url=""),
    ]
    previous_names = {"Known Yesterday"}
    colleagues = [{"orcid": "0000-0000-0000-0003", "staff_name": "Already Curated"}]
    new = find_new_staff(current_staff, previous_names, colleagues)
    assert [m.name for m in new] == ["Brand New Person"]


def test_build_report_notes_anomaly_and_skips_departures():
    report = build_report(added=[], needs_review=[], departed=[], anomaly="staff page looked broken")
    assert "WARNING" in report
    assert "staff page looked broken" in report


def test_build_report_lists_departed_and_empty_new():
    departed = [{"orcid": "0000-0000-0000-0001", "staff_name": "Gone Person"}]
    report = build_report(added=[], needs_review=[], departed=departed, anomaly=None)
    assert "Gone Person" in report
    assert "None." in report  # the "Added" / "needs review" sections, since both are empty


def _result(name: str, matches: list[dict]) -> NewStaffResult:
    return NewStaffResult(staff_member=StaffMember(name=name, role="", profile_url=""), orcid_matches=tuple(matches))


def test_select_auto_add_on_single_confirmed_match():
    result = _result("New Person", [
        {"orcid_id": "0000-0000-0000-0009", "candidate_name": "New Person", "confidence": "exact", "iisg_confirmed": True},
    ])
    added, needs_review = select_auto_add_candidates([result])
    assert len(added) == 1
    assert added[0][0].name == "New Person"
    assert added[0][1]["orcid_id"] == "0000-0000-0000-0009"
    assert needs_review == []


def test_select_auto_add_drops_unconfirmed_match_entirely():
    # No confirmed candidate at all -- this is the common-name noise case
    # (e.g. dozens of unconfirmed candidates for a generic name) and should
    # not appear in the report at all, not even under "needs review".
    result = _result("New Person", [
        {"orcid_id": "0000-0000-0000-0009", "candidate_name": "New Person", "confidence": "exact", "iisg_confirmed": False},
    ])
    added, needs_review = select_auto_add_candidates([result])
    assert added == []
    assert needs_review == []


def test_select_auto_add_sends_single_partial_confirmed_to_review():
    # Confirmed affiliation, but the name match was only partial (e.g. a
    # dropped middle/maiden name) -- worth a human glance, not auto-added.
    result = _result("New Person", [
        {"orcid_id": "0000-0000-0000-0005", "candidate_name": "New Person Extra", "confidence": "partial", "iisg_confirmed": True},
    ])
    added, needs_review = select_auto_add_candidates([result])
    assert added == []
    assert len(needs_review) == 1
    assert needs_review[0].orcid_matches[0]["orcid_id"] == "0000-0000-0000-0005"


def test_filter_known_orcids_drops_already_present_orcid():
    # Reproduces the Richard Zijdeman bug: an orcid already in colleagues.yaml
    # under an entry with no staff_name (predates staff_name tracking) must
    # still be excluded, even though find_new_staff's name-based dedup can't
    # see it.
    result = _result("Richard Zijdeman", [
        {"orcid_id": "0000-0003-3902-3720", "candidate_name": "Richard Zijdeman", "confidence": "exact", "iisg_confirmed": True},
    ])
    colleagues = [{"orcid": "0000-0003-3902-3720"}]  # no staff_name, like the project's own placeholder entry
    filtered = filter_known_orcids([result], colleagues)
    assert filtered == []


def test_filter_known_orcids_keeps_unrelated_candidate():
    result = _result("Brand New Person", [
        {"orcid_id": "0000-0000-0000-0099", "candidate_name": "Brand New Person", "confidence": "exact", "iisg_confirmed": True},
    ])
    colleagues = [{"orcid": "0000-0003-3902-3720"}]
    filtered = filter_known_orcids([result], colleagues)
    assert len(filtered) == 1
    assert filtered[0].orcid_matches[0]["orcid_id"] == "0000-0000-0000-0099"


def test_select_auto_add_skips_ambiguous_multiple_confirmed_matches():
    result = _result("Common Name", [
        {"orcid_id": "0000-0000-0000-0001", "candidate_name": "Common Name", "confidence": "exact", "iisg_confirmed": True},
        {"orcid_id": "0000-0000-0000-0002", "candidate_name": "Common Name", "confidence": "exact", "iisg_confirmed": True},
    ])
    added, needs_review = select_auto_add_candidates([result])
    assert added == []
    assert needs_review == [result]
