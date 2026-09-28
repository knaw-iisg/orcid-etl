"""Command-line entry point: ``python -m orcid_etl.cli``.

Colleagues are personally-identifying curation data, not code, so they and
everything derived from them (colleagues.yaml, the built graph, the quality
report and its per-colleague snippets, the ORCID response cache) live
outside this repo entirely, in --data-dir (default: $ORCID_ETL_DATA_DIR or
~/orcid-etl-data). Only data/org_aliases.yaml, data/org_department_overrides.yaml
and data/shapes/ stay in this repo, since they're reusable across any
colleague list.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
import yaml
from rdflib import Graph

from . import harvest
from .nde_ap import enrich
from .pipeline import OrgConfig, add_person
from .prefixes import DEFAULT_GRAPH, NAMESPACE_BINDINGS
from .quality_gate import apply_quality_gate, write_person_reports, write_report

REPO_ROOT = Path(__file__).resolve().parent.parent
ORG_ALIASES_FILE = REPO_ROOT / "data" / "org_aliases.yaml"
DEPARTMENT_OVERRIDES_FILE = REPO_ROOT / "data" / "org_department_overrides.yaml"

DEFAULT_DATA_DIR = Path.home() / "orcid-etl-data"


def resolve_data_dir(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    if os.environ.get("ORCID_ETL_DATA_DIR"):
        return Path(os.environ["ORCID_ETL_DATA_DIR"]).expanduser()
    return DEFAULT_DATA_DIR


def build_graph(colleagues: list[dict], cache_dir: Path, config: OrgConfig, *, refresh: bool = False) -> tuple[Graph, list[str]]:
    g = Graph(identifier=DEFAULT_GRAPH)
    for prefix, ns in NAMESPACE_BINDINGS.items():
        g.bind(prefix, ns)

    skipped = []
    for entry in colleagues:
        orcid_id = entry["orcid"]
        try:
            record = harvest.fetch_record(orcid_id, cache_dir, force_refresh=refresh)
        except requests.exceptions.RequestException as e:
            print(f"WARNING: skipping {orcid_id}, fetch failed: {e}", file=sys.stderr)
            skipped.append(orcid_id)
            continue
        person_uri = add_person(g, orcid_id, record, config)
        enrich(person_uri, g)

    return g, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KNAW/IISG staff ORCID profiles -> RDF")
    parser.add_argument("--refresh", action="store_true", help="bypass the local ORCID response cache")
    parser.add_argument(
        "--data-dir",
        help="where colleagues.yaml lives and the quality report/cache are written "
             "(default: $ORCID_ETL_DATA_DIR or ~/orcid-etl-data)",
    )
    parser.add_argument(
        "--out", type=Path,
        help="output Turtle path (default: <data-dir>/orcid-etl.ttl). Point this at, "
             "e.g., a local triplestore's sources/ directory to load this pipeline's "
             "output alongside the other five, without moving any personally-"
             "identifying curation data (colleagues.yaml, the ORCID cache, the "
             "quality report) out of --data-dir.",
    )
    args = parser.parse_args(argv)

    data_dir = resolve_data_dir(args.data_dir)
    colleagues_file = data_dir / "colleagues.yaml"
    cache_dir = data_dir / "orcid_cache"
    output_path = args.out or (data_dir / "orcid-etl.ttl")
    quality_report_path = data_dir / "orcid-etl-issues.md"
    quality_report_person_dir = data_dir / "orcid-etl-issues"

    if not colleagues_file.exists():
        sys.exit(
            f"No colleagues.yaml found at {colleagues_file}\n"
            f"Create it there (one '- orcid: \"0000-...\"' entry per colleague), "
            f"or pass --data-dir / set $ORCID_ETL_DATA_DIR to point elsewhere."
        )

    config = OrgConfig.load(ORG_ALIASES_FILE, DEPARTMENT_OVERRIDES_FILE)
    colleagues = yaml.safe_load(colleagues_file.read_text())

    g, skipped = build_graph(colleagues, cache_dir, config, refresh=args.refresh)

    quality_report = apply_quality_gate(g)
    write_report(quality_report, quality_report_path)
    write_person_reports(quality_report, quality_report_person_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(output_path), format="turtle")
    print(f"Wrote {len(g)} triples to {output_path}")
    if quality_report:
        print(f"{len(quality_report)} entr(ies) failed quality checks - see {quality_report_path}", file=sys.stderr)
    if skipped:
        print(f"Skipped {len(skipped)} colleague(s) due to fetch errors: {', '.join(skipped)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
