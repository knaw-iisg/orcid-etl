"""Finds people with a public ORCID profile that mentions IISG, whose name
also matches someone on the institute's public staff page, so they can be
considered for addition to colleagues.yaml.

Never writes to colleagues.yaml itself -- that file is hand-curated PII (see
cli.py's module docstring), so a match here is only ever a suggestion for a
human to verify, surfaced via write_candidates_report() the same way
quality_gate.py surfaces SHACL failures.

Two separate stages, intentionally kept apart:

- Discovery (fetch_orcid_candidates): a broad ORCID search across spelling
  variants, cast wide on purpose to find candidates worth checking. Being
  found here does NOT mean a candidate qualifies.
- The rule (match_reason): the actual pass/fail test, applied to a
  candidate's full ORCID record (see harvest.fetch_record) -- qualifies iff
  the record mentions "International Institute of Social History" or
  "Internationaal Instituut voor Sociale Geschiedenis" (any case, anywhere
  in the record -- an affiliation's organization name, typically), or any
  URL on the record contains the domain "iisg.amsterdam" (an employment
  entry's own optional URL is often a staff bio page, e.g.
  https://iisg.amsterdam/en/about/staff/marco-van-leeuwen).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STAFF_URL = "https://iisg.amsterdam/en/about/staff"
STAFF_SITE_ORIGIN = "https://iisg.amsterdam"
ORCID_SEARCH_API = "https://pub.orcid.org/v3.0/expanded-search/"
ORCID_SEARCH_ROWS = 200  # expanded-search's page size cap

# Discovery net only (recall, not the rule) -- people type their own
# employer's name into ORCID free-text, so searching only the two canonical
# names below would miss candidates who, say, only wrote "IISH" but whose
# employment URL points at iisg.amsterdam. match_reason() below is what
# actually decides whether a discovered candidate qualifies.
INSTITUTION_NAME_VARIANTS = (
    "International Institute of Social History",
    "International Institute for Social History",
    "IISH",
    "Internationaal Instituut voor Sociale Geschiedenis",
)

# The rule: a full ORCID record qualifies iff it mentions one of these
# phrases (case-insensitive, anywhere) ...
AFFILIATION_PHRASES = (
    "international institute of social history",
    "internationaal instituut voor sociale geschiedenis",
)
# ... or one of these abbreviations as a whole word (word-boundaried,
# unlike AFFILIATION_PHRASES above, since "iisg"/"iish" are short enough
# that an unanchored substring check could false-positive inside an
# unrelated word) ...
AFFILIATION_ABBREVIATIONS = ("iish", "iisg")
# ... or contains a URL on this domain (anywhere).
IISG_URL_DOMAIN = "iisg.amsterdam"


@dataclass(frozen=True)
class StaffMember:
    name: str
    role: str
    profile_url: str


@dataclass(frozen=True)
class OrcidCandidate:
    orcid_id: str
    given_names: str
    family_names: str
    institution_names: tuple[str, ...]


def fetch_staff_html(url: str = STAFF_URL) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def parse_staff_page(html: str) -> list[StaffMember]:
    soup = BeautifulSoup(html, "html.parser")
    members = []
    for row in soup.select(".views-row"):
        link = row.select_one("a.full-click__trigger")
        name_el = row.select_one(".field--name-title")
        if not link or not name_el:
            continue
        role_el = row.select_one(".field--name-body p")
        members.append(
            StaffMember(
                name=name_el.get_text(strip=True),
                role=role_el.get_text(strip=True) if role_el else "",
                profile_url=link.get("href", ""),
            )
        )
    return members


def _run_orcid_search(query: str) -> dict[str, OrcidCandidate]:
    candidates: dict[str, OrcidCandidate] = {}
    start = 0
    while True:
        response = requests.get(
            ORCID_SEARCH_API,
            params={"q": query, "start": start, "rows": ORCID_SEARCH_ROWS},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        for result in payload.get("expanded-result") or []:
            orcid_id = result["orcid-id"]
            candidates[orcid_id] = OrcidCandidate(
                orcid_id=orcid_id,
                given_names=result.get("given-names") or "",
                family_names=result.get("family-names") or "",
                institution_names=tuple(result.get("institution-name") or ()),
            )
        start += ORCID_SEARCH_ROWS
        if start >= (payload.get("num-found") or 0):
            break
    return candidates


def fetch_orcid_candidates(
    institution_names: tuple[str, ...] = INSTITUTION_NAME_VARIANTS,
) -> dict[str, OrcidCandidate]:
    candidates: dict[str, OrcidCandidate] = {}
    for institution_name in institution_names:
        candidates.update(_run_orcid_search(f'affiliation-org-name:"{institution_name}"'))
    return candidates


def search_orcid_candidates_by_name(full_name: str) -> dict[str, OrcidCandidate]:
    """Unqualified phrase search across ORCID's default indexed fields, by
    full name rather than institution affiliation -- for staff_monitor to
    check brand-new staff who likely haven't put IISG into their ORCID
    profile yet (fetch_orcid_candidates above would miss them for exactly
    that reason)."""
    return _run_orcid_search(f'"{full_name}"')


def normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", " ", without_accents.lower()).strip()


def name_tokens(name: str) -> frozenset[str]:
    """Normalized tokens, dropping single-letter ones. ORCID given-names
    routinely carry middle initials ("Marco H.D.") that the staff page's
    plain "Marco van Leeuwen" doesn't -- without this, that counts as a
    dropped-name "partial" match instead of "exact"."""
    return frozenset(t for t in normalize_name(name).split() if len(t) > 1)


def match_staff_to_candidates(
    staff: list[StaffMember], candidates: dict[str, OrcidCandidate]
) -> list[dict]:
    """Name-only matching: same token set (any order, initials ignored) is
    "exact", one name's tokens being a subset of the other's (e.g. a dropped
    middle name) is "partial". Both produce false positives on common names
    -- this is a shortlist for a human to verify, not a resolver."""
    matches = []
    for member in staff:
        member_tokens = name_tokens(member.name)
        if len(member_tokens) < 2:
            continue
        for candidate in candidates.values():
            candidate_name = f"{candidate.given_names} {candidate.family_names}".strip()
            candidate_tokens = name_tokens(candidate_name)
            if len(candidate_tokens) < 2:
                continue

            if member_tokens == candidate_tokens:
                confidence = "exact"
            elif candidate_tokens <= member_tokens or member_tokens <= candidate_tokens:
                confidence = "partial"
            else:
                continue

            matches.append(
                {
                    "staff_name": member.name,
                    "role": member.role,
                    "staff_profile_url": member.profile_url,
                    "orcid_id": candidate.orcid_id,
                    "candidate_name": candidate_name,
                    "confidence": confidence,
                }
            )
    return matches


def match_reason(record: dict) -> str | None:
    """The rule: does this full ORCID record (see harvest.fetch_record)
    mention IISG by name or abbreviation, or carry a URL on its domain?
    Checking the whole serialized record rather than picking out specific
    fields means it doesn't matter whether the mention is in an
    employment's organization name, a department name, or that
    employment's own optional URL -- any of those, or a researcher-url,
    satisfy the same rule."""
    text = json.dumps(record).lower()
    for phrase in AFFILIATION_PHRASES:
        if phrase in text:
            return f'mentions "{phrase}"'
    for abbreviation in AFFILIATION_ABBREVIATIONS:
        if re.search(rf"\b{abbreviation}\b", text):
            return f'mentions "{abbreviation}"'
    if IISG_URL_DOMAIN in text:
        return f"a URL on the profile points to {IISG_URL_DOMAIN}"
    return None


def filter_by_iisg_mention(matches: list[dict], records: dict[str, dict]) -> list[dict]:
    """Keeps only matches whose full ORCID record passes match_reason().
    matches: output of match_staff_to_candidates(). records: orcid_id ->
    full record, as returned by harvest.fetch_record -- fetching those is
    the caller's job (discover_cli.py) since it needs the on-disk cache."""
    filtered = []
    for match in matches:
        record = records.get(match["orcid_id"])
        if record is None:
            continue
        reason = match_reason(record)
        if reason is None:
            continue
        filtered.append({**match, "match_reason": reason})
    return filtered


def write_candidates_report(matches: list[dict], path) -> None:
    path = Path(path)
    if not matches:
        path.unlink(missing_ok=True)
        return

    lines = [
        "# ORCID candidates for IISG staff",
        "",
        "Generated by `python -m orcid_etl.discover_cli`. Each entry below is a",
        "public ORCID profile with an IISG-like affiliation whose name matches",
        "someone on https://iisg.amsterdam/en/about/staff, but isn't yet in",
        "colleagues.yaml. Same name doesn't always mean same person -- verify by",
        "hand before adding an `- orcid: \"...\"` line. Nothing here is added",
        "automatically, and re-running this command regenerates the whole file.",
        "",
    ]
    for match in sorted(matches, key=lambda m: (m["confidence"] != "exact", m["staff_name"])):
        heading = match["staff_name"]
        if match["role"]:
            heading += f" ({match['role']})"
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(f"- confidence: {match['confidence']}")
        lines.append(f"- matched because: {match['match_reason']}")
        lines.append(f"- ORCID name on file: {match['candidate_name']}")
        lines.append(f"- https://orcid.org/{match['orcid_id']}")
        if match["staff_profile_url"]:
            lines.append(f"- staff bio: {STAFF_SITE_ORIGIN}{match['staff_profile_url']}")
        lines.append("")

    path.write_text("\n".join(lines))
