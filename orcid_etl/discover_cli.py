"""Command-line entry point: ``python -m orcid_etl.discover_cli``.

Finds people with a public ORCID profile and an IISG-like affiliation whose
name matches someone on the institute's staff page, and aren't already in
colleagues.yaml. Writes a human-review report (see discover.py) -- never
edits colleagues.yaml. Safe to run daily (e.g. via cron): each run just
regenerates the report from current ORCID + staff-page state.
"""
from __future__ import annotations

import argparse
import sys

import requests
import yaml

from . import discover, harvest
from .cli import resolve_data_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find public ORCID profiles matching IISG staff not yet in colleagues.yaml"
    )
    parser.add_argument(
        "--data-dir",
        help="same --data-dir as python -m orcid_etl.cli "
             "(default: $ORCID_ETL_DATA_DIR or ~/orcid-etl-data)",
    )
    parser.add_argument("--staff-url", default=discover.STAFF_URL)
    parser.add_argument(
        "--refresh", action="store_true",
        help="bypass the local ORCID response cache (same cache cli.py uses)",
    )
    args = parser.parse_args(argv)

    data_dir = resolve_data_dir(args.data_dir)
    colleagues_file = data_dir / "colleagues.yaml"
    cache_dir = data_dir / "orcid_cache"
    report_path = data_dir / "orcid-etl-candidates.md"

    known_orcids = set()
    if colleagues_file.exists():
        colleagues = yaml.safe_load(colleagues_file.read_text()) or []
        known_orcids = {entry["orcid"] for entry in colleagues}

    staff = discover.parse_staff_page(discover.fetch_staff_html(args.staff_url))
    candidates = discover.fetch_orcid_candidates()
    new_candidates = {
        orcid_id: candidate
        for orcid_id, candidate in candidates.items()
        if orcid_id not in known_orcids
    }

    name_matches = discover.match_staff_to_candidates(staff, new_candidates)

    records = {}
    for orcid_id in {match["orcid_id"] for match in name_matches}:
        try:
            records[orcid_id] = harvest.fetch_record(orcid_id, cache_dir, force_refresh=args.refresh)
        except requests.exceptions.RequestException as e:
            print(f"WARNING: could not verify {orcid_id}, fetch failed: {e}", file=sys.stderr)

    matches = discover.filter_by_iisg_mention(name_matches, records)
    discover.write_candidates_report(matches, report_path)

    print(
        f"{len(staff)} staff page entries, {len(candidates)} ORCID profile(s) found via the "
        f"discovery search ({len(new_candidates)} not already in colleagues.yaml), "
        f"{len(name_matches)} name match(es), {len(matches)} confirmed by the IISG-mention rule"
    )
    if matches:
        print(f"See {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
