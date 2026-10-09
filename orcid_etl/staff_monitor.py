"""Daily check: did anyone on colleagues.yaml leave the IISG staff page, and
did anyone new join who has (or might have) a public ORCID?

Two separate, deliberately asymmetric actions, matching colleagues.yaml's
existing hand-curation philosophy (see discover.py's module docstring):

- Departures are auto-removed from colleagues.yaml -- a person who no longer
  appears on the staff page shouldn't keep being listed as current IISG
  staff on a public knowledge graph, and the cost of being wrong (someone
  temporarily missing from a staff page re-add, revertable via
  orcid-etl-removals.log) is much lower than the cost of a wrong addition.
- New arrivals are only ever reported, never auto-added -- same reasoning
  as discover.py: a name match isn't an identity match.

A broken staff-page scrape (HTML redesign, site down, transient error) would
otherwise look exactly like "everyone left" and wipe colleagues.yaml -- see
is_scrape_anomaly().
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import discover, harvest
from .discover import StaffMember

ANOMALY_MIN_RATIO = 0.5  # current staff count below this fraction of the last-known-good count looks like a broken scrape, not a mass departure


@dataclass(frozen=True)
class NewStaffResult:
    staff_member: StaffMember
    orcid_matches: tuple[dict, ...]  # each: {"orcid_id", "candidate_name", "confidence", "iisg_confirmed": bool}

    @property
    def confirmed_matches(self) -> tuple[dict, ...]:
        return tuple(m for m in self.orcid_matches if m["iisg_confirmed"])


def is_scrape_anomaly(previous_count: int, current_count: int, min_ratio: float = ANOMALY_MIN_RATIO) -> bool:
    """True if current_count looks like a broken scrape rather than a real drop."""
    if previous_count <= 0:
        return False
    return current_count < previous_count * min_ratio


def find_departed(colleagues: list[dict], current_staff: list[StaffMember]) -> list[dict]:
    """colleagues.yaml entries with a staff_name no longer matching anyone on
    current_staff. Entries with no staff_name (hand-added, not from
    discover_cli/staff_monitor) are never touched here."""
    current_tokens = [discover.name_tokens(m.name) for m in current_staff]
    departed = []
    for entry in colleagues:
        staff_name = entry.get("staff_name")
        if not staff_name:
            continue
        entry_tokens = discover.name_tokens(staff_name)
        if not any(entry_tokens == tokens for tokens in current_tokens):
            departed.append(entry)
    return departed


def find_new_staff(current_staff: list[StaffMember], previous_names: set[str], colleagues: list[dict]) -> list[StaffMember]:
    """Staff present today but absent from both yesterday's snapshot and
    colleagues.yaml -- i.e. neither previously seen nor already curated."""
    known_tokens = {discover.name_tokens(n) for n in previous_names}
    known_tokens |= {discover.name_tokens(e["staff_name"]) for e in colleagues if e.get("staff_name")}
    return [m for m in current_staff if discover.name_tokens(m.name) not in known_tokens]


def check_new_staff_orcid(member: StaffMember, cache_dir) -> NewStaffResult:
    """Does this brand-new staff member have a public ORCID? Checked by name
    (not by institution affiliation, which fetch_orcid_candidates uses --
    someone who joined last week likely hasn't updated their ORCID profile
    yet) using the same exact/partial token matching as discover_cli's own
    candidate search, so a dropped middle name doesn't get missed here
    either. IISG affiliation (discover.match_reason -- phrase, abbreviation,
    or iisg.amsterdam URL) is checked per-candidate; see
    select_auto_add_candidates() for what happens with the result."""
    candidates = discover.search_orcid_candidates_by_name(member.name)
    name_matches = discover.match_staff_to_candidates([member], candidates)
    matches = []
    for name_match in name_matches:
        iisg_confirmed = False
        try:
            record = harvest.fetch_record(name_match["orcid_id"], cache_dir)
            iisg_confirmed = discover.match_reason(record) is not None
        except Exception:
            pass
        matches.append({
            "orcid_id": name_match["orcid_id"],
            "candidate_name": name_match["candidate_name"],
            "confidence": name_match["confidence"],
            "iisg_confirmed": iisg_confirmed,
        })
    return NewStaffResult(staff_member=member, orcid_matches=tuple(matches))


def select_auto_add_candidates(results: list[NewStaffResult]) -> tuple[list[tuple[StaffMember, dict]], list[NewStaffResult]]:
    """Splits new-staff results into (auto_add, needs_review).

    Auto-add iff: on the staff page (true by construction -- these are all
    new_staff_results) AND a name match (exact or partial) AND that
    specific candidate's ORCID record confirms IISG affiliation. If more
    than one candidate for the same person clears that bar, it's ambiguous
    (which real ORCID is this person's?) -- flagged for review rather than
    guessing."""
    auto_add = []
    needs_review = []
    for result in results:
        confirmed = result.confirmed_matches
        if len(confirmed) == 1:
            auto_add.append((result.staff_member, confirmed[0]))
        else:
            needs_review.append(result)
    return auto_add, needs_review


def build_report(
    added: list[tuple[StaffMember, dict]],
    needs_review: list[NewStaffResult],
    departed: list[dict],
    anomaly: str | None,
    run_date: dt.date | None = None,
) -> str:
    run_date = run_date or dt.date.today()
    lines = [f"# IISG staff / ORCID monitor -- {run_date.isoformat()}", ""]

    if anomaly:
        lines += [f"**WARNING: {anomaly}** -- departures were NOT applied this run, "
                   "to avoid treating a broken scrape as a mass staff exodus. "
                   "No colleagues.yaml changes were made.", ""]

    lines.append("## Added to colleagues.yaml (staff-page name match + IISG affiliation confirmed on ORCID)")
    if not added:
        lines.append("None.")
    else:
        for member, match in added:
            lines.append(f"- **{member.name}** -- https://orcid.org/{match['orcid_id']} ({match['confidence']} name match)")
    lines.append("")

    lines.append("## New staff needing manual review (no confirmed single match)")
    if not needs_review:
        lines.append("None.")
    else:
        for result in needs_review:
            m = result.staff_member
            lines.append(f"- **{m.name}** ({m.role or 'role not listed'})")
            if not result.orcid_matches:
                lines.append("  - no public ORCID profile found by name search")
            for match in result.orcid_matches:
                status = "IISG affiliation confirmed" if match["iisg_confirmed"] else "affiliation not yet confirmed on ORCID"
                lines.append(
                    f"  - https://orcid.org/{match['orcid_id']} ({match['confidence']} name match, {status})"
                    f" -- not added, review by hand"
                )
    lines.append("")

    lines.append("## Departed (removed from colleagues.yaml)")
    if not departed:
        lines.append("None.")
    else:
        for entry in departed:
            lines.append(f"- {entry.get('staff_name')} (https://orcid.org/{entry['orcid']})")
    lines.append("")

    return "\n".join(lines)
