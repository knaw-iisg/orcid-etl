"""
Builds knaw-iisg-orcid.ttl: an RDF graph, in schema.org (sdo) terms,
describing the colleagues listed in colleagues.yaml, sourced from their
public ORCID records (profile, employment, works, and fundings —
education and other affiliation/activity types are intentionally left
out).

Colleagues are personally-identifying curation data, not code, so they
and everything derived from them (colleagues.yaml, knaw-iisg-orcid.ttl,
the quality report and its per-colleague snippets, the ORCID response
cache) live outside this repo entirely, in --data-dir (default:
$COLLEAGUE_GRAPH_DATA_DIR or ~/knaw-iisg-orcid-data). Only the
org_aliases.yaml/org_department_overrides.yaml config and the SHACL
shapes stay in this repo's data/ folder, since they're reusable across
any colleague list.

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r scripts/requirements.txt
    python3 scripts/build_colleague_graph.py

Re-run whenever colleagues.yaml changes. Add --refresh to bypass the
local ORCID response cache and re-fetch fresh data.
"""
import argparse
import os
import re
from pathlib import Path

import sys

import requests
import yaml
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from orcid_client import fetch_record
from quality_gate import apply_quality_gate, write_report, write_person_reports

REPO_ROOT = Path(__file__).resolve().parent.parent
ORG_ALIASES_FILE = REPO_ROOT / "data" / "org_aliases.yaml"
DEPARTMENT_OVERRIDES_FILE = REPO_ROOT / "data" / "org_department_overrides.yaml"

DEFAULT_DATA_DIR = Path.home() / "knaw-iisg-orcid-data"


def resolve_data_dir(cli_value):
    if cli_value:
        return Path(cli_value).expanduser()
    if os.environ.get("COLLEAGUE_GRAPH_DATA_DIR"):
        return Path(os.environ["COLLEAGUE_GRAPH_DATA_DIR"]).expanduser()
    return DEFAULT_DATA_DIR

SDO = Namespace("https://schema.org/")
LOCAL_ORG = Namespace("urn:orcidgraph:org:")
LOCAL_PERIODICAL = Namespace("urn:orcidgraph:periodical:")

# Not a real vocabulary - used to attach a human-readable "why this might
# get flagged" hint to activity nodes at mapping time (see add_employments
# below), for quality_gate.write_report to read before pruning. Never
# meant to survive into the published graph: it's only ever set on nodes
# that, by construction, are about to fail the SHACL check and get pruned.
INTERNAL = Namespace("urn:orcidgraph:internal:")

# Blank nodes get a fresh random ID every run, which reshuffles the
# serialized Turtle output even when the data hasn't changed. Periodicals
# and addresses get deterministic URIs instead, derived from content that
# already uniquely identifies them - this also lets identical journal
# names across different works/colleagues share one node.

# Populated from ORG_ALIASES_FILE in main(); org_uri() consults it to merge
# organization nodes that ORCID represents inconsistently across records.
ORG_ALIASES = {}

# Populated from DEPARTMENT_OVERRIDES_FILE in main(); keyed by
# (organization name, department name) -> override entry. add_employments
# consults it to use a named department's own organization as the
# sdo:worksFor target instead of the umbrella org ORCID lists.
DEPARTMENT_OVERRIDES = {}

# ORCID work "type" -> closest schema.org type. Anything not listed here
# falls back to the generic sdo:CreativeWork.
WORK_TYPE_MAP = {
    "journal-article": SDO.ScholarlyArticle,
    "book": SDO.Book,
    "book-chapter": SDO.Chapter,
    "dissertation-thesis": SDO.Thesis,
    "dataset": SDO.Dataset,
    "report": SDO.Report,
    "conference-paper": SDO.ScholarlyArticle,
    "conference-abstract": SDO.ScholarlyArticle,
    "preprint": SDO.ScholarlyArticle,
    "working-paper": SDO.ScholarlyArticle,
}


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def format_date(d):
    """ORCID dates are partial (year, or year+month, or full). Returns an
    xsd:date string anchored to day/month 01 when missing, or None."""
    if not d or not d.get("year"):
        return None
    year = d["year"]["value"]
    month = (d.get("month") or {}).get("value") or "01"
    day = (d.get("day") or {}).get("value") or "01"
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def external_id(summary, id_type):
    # ORCID sometimes has "external-ids": null rather than omitting the key,
    # so summary.get("external-ids", {}) isn't enough - .get()'s default only
    # kicks in when the key is *missing*, not when its value is None.
    external_ids = (summary.get("external-ids") or {}).get("external-id", []) or []
    for e in external_ids:
        if e["external-id-type"] == id_type:
            return e["external-id-value"]
    return None


def activity_uri(summary):
    """ORCID includes a stable permalink path on every activity summary
    (e.g. /0000-.../employment/25933636) - reuse it as the node's IRI."""
    return URIRef(f"https://orcid.org{summary['path']}")


def org_uri(org):
    dis = org.get("disambiguated-organization")
    ident = dis.get("disambiguated-organization-identifier") if dis else None
    # ROR/FUNDREF identifiers are already dereferenceable URLs; reuse them
    # so the same organization is shared across colleagues. Other sources
    # (e.g. RINGGOLD) are bare numbers, and orgs with no disambiguation at
    # all - both fall back to a name-derived local URI below.
    raw_uri = ident if (ident and ident.startswith("http")) else str(LOCAL_ORG[slugify(org["name"])])
    return URIRef(ORG_ALIASES.get(raw_uri, raw_uri))


def add_organization(g, org):
    uri = org_uri(org)
    g.add((uri, RDF.type, SDO.Organization))
    g.add((uri, SDO.name, Literal(org["name"])))

    # Record an explicit sdo:identifier whenever the final URI (native or
    # resolved via data/org_aliases.yaml) is a real dereferenceable ID, not
    # our own urn:orcidgraph:org: fallback. This is what
    # data/shapes/organization_quality.ttl checks for.
    if str(uri).startswith("http"):
        dis = org.get("disambiguated-organization") or {}
        source = dis.get("disambiguation-source", "manual-alias")
        identifier_node = URIRef(f"{uri}#identifier")
        g.add((identifier_node, RDF.type, SDO.PropertyValue))
        g.add((identifier_node, SDO.propertyID, Literal(source)))
        g.add((identifier_node, SDO.value, Literal(str(uri))))
        g.add((uri, SDO.identifier, identifier_node))

    address = org.get("address")
    if address:
        # One org has one address, so hanging the URI off the org's own
        # URI is deterministic and avoids needing a blank node.
        addr_node = URIRef(f"{uri}#address")
        g.add((addr_node, RDF.type, SDO.PostalAddress))
        if address.get("city"):
            g.add((addr_node, SDO.addressLocality, Literal(address["city"])))
        if address.get("country"):
            g.add((addr_node, SDO.addressCountry, Literal(address["country"])))
        g.add((uri, SDO.address, addr_node))
    return uri


def synthetic_org(spec):
    """Builds an org dict shaped like ORCID's own "organization" field,
    from a data/org_department_overrides.yaml entry."""
    return {
        "name": spec["name"],
        "disambiguated-organization": {
            "disambiguated-organization-identifier": spec["ror"],
            "disambiguation-source": "ROR",
        },
    }


def add_employments(g, person_uri, activities):
    for group in (activities.get("employments") or {}).get("affiliation-group", []):
        for summary in group["summaries"]:
            s = summary["employment-summary"]
            role_uri = activity_uri(s)
            g.add((role_uri, RDF.type, SDO.OrganizationRole))
            g.add((role_uri, SDO.roleName, Literal(s.get("role-title") or "Employee")))
            start = format_date(s.get("start-date"))
            end = format_date(s.get("end-date"))
            if start:
                g.add((role_uri, SDO.startDate, Literal(start, datatype=XSD.date)))
            if end:
                g.add((role_uri, SDO.endDate, Literal(end, datatype=XSD.date)))

            org_dict = s["organization"]
            department = s.get("department-name")
            override = DEPARTMENT_OVERRIDES.get((org_dict["name"], department))
            if override:
                org_dict = synthetic_org(override["organization"])
            else:
                # Near miss: this org matches a known umbrella institution
                # in org_department_overrides.yaml, but not with this
                # department (or none was given) - worth telling the
                # colleague exactly what to add rather than leaving them
                # to guess.
                known_departments = sorted({
                    dept for (name, dept) in DEPARTMENT_OVERRIDES if name == org_dict["name"] and dept
                })
                if known_departments:
                    g.add((role_uri, INTERNAL.reportReason, Literal(
                        "no institutional identifier (e.g. ROR) attached in ORCID for this employer. "
                        f"If this role is actually within {' or '.join(known_departments)}, "
                        "add that as the department on this entry in ORCID to resolve it automatically."
                    )))

            org = add_organization(g, org_dict)
            g.add((role_uri, SDO.worksFor, org))
            g.add((person_uri, SDO.worksFor, role_uri))

            if override and override.get("parent_organization"):
                parent = add_organization(g, synthetic_org(override["parent_organization"]))
                g.add((org, SDO.parentOrganization, parent))


def add_works(g, person_uri, activities):
    for group in (activities.get("works") or {}).get("group", []):
        for s in group["work-summary"]:
            doi = external_id(s, "doi")
            work_uri = URIRef(f"https://doi.org/{doi}") if doi else activity_uri(s)
            sdo_type = WORK_TYPE_MAP.get(s.get("type"), SDO.CreativeWork)
            g.add((work_uri, RDF.type, sdo_type))

            title = ((s.get("title") or {}).get("title") or {}).get("value")
            if title:
                g.add((work_uri, SDO.name, Literal(title)))

            pub_date = format_date(s.get("publication-date"))
            if pub_date:
                g.add((work_uri, SDO.datePublished, Literal(pub_date, datatype=XSD.date)))

            journal = (s.get("journal-title") or {}).get("value")
            if journal:
                periodical = LOCAL_PERIODICAL[slugify(journal)]
                g.add((periodical, RDF.type, SDO.Periodical))
                g.add((periodical, SDO.name, Literal(journal)))
                g.add((work_uri, SDO.isPartOf, periodical))

            g.add((work_uri, SDO.author, person_uri))


def add_fundings(g, person_uri, activities):
    for group in (activities.get("fundings") or {}).get("group", []):
        for s in group["funding-summary"]:
            grant_uri = activity_uri(s)
            g.add((grant_uri, RDF.type, SDO.MonetaryGrant))

            title = ((s.get("title") or {}).get("title") or {}).get("value")
            if title:
                g.add((grant_uri, SDO.name, Literal(title)))

            if s.get("organization"):
                funder = add_organization(g, s["organization"])
                g.add((grant_uri, SDO.funder, funder))

            # schema.org has no dedicated "recipient" property on Grant;
            # fundedItem is the documented way to point a Grant at the
            # Person it funded (inverse: sdo:funding on the Person).
            g.add((grant_uri, SDO.fundedItem, person_uri))

            grant_number = external_id(s, "grant_number")
            if grant_number:
                g.add((grant_uri, SDO.identifier, Literal(grant_number)))


def add_person(g, orcid_id, record):
    person_uri = URIRef(f"https://orcid.org/{orcid_id}")
    g.add((person_uri, RDF.type, SDO.Person))

    name = record.get("person", {}).get("name") or {}
    given = (name.get("given-names") or {}).get("value")
    family = (name.get("family-name") or {}).get("value")
    if given:
        g.add((person_uri, SDO.givenName, Literal(given)))
    if family:
        g.add((person_uri, SDO.familyName, Literal(family)))
    if given or family:
        g.add((person_uri, SDO.name, Literal(" ".join(p for p in (given, family) if p))))

    activities = record.get("activities-summary", {})
    add_employments(g, person_uri, activities)
    add_works(g, person_uri, activities)
    add_fundings(g, person_uri, activities)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="bypass the local ORCID response cache")
    parser.add_argument(
        "--data-dir",
        help="where colleagues.yaml lives and outputs are written "
             "(default: $COLLEAGUE_GRAPH_DATA_DIR or ~/knaw-iisg-orcid-data)",
    )
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    colleagues_file = data_dir / "colleagues.yaml"
    cache_dir = data_dir / "orcid_cache"
    output_path = data_dir / "knaw-iisg-orcid.ttl"
    quality_report_path = data_dir / "knaw-iisg-orcid-issues.md"
    quality_report_person_dir = data_dir / "knaw-iisg-orcid-issues"

    if not colleagues_file.exists():
        sys.exit(
            f"No colleagues.yaml found at {colleagues_file}\n"
            f"Create it there (one '- orcid: \"0000-...\"' entry per colleague), "
            f"or pass --data-dir / set $COLLEAGUE_GRAPH_DATA_DIR to point elsewhere."
        )

    global ORG_ALIASES, DEPARTMENT_OVERRIDES
    if ORG_ALIASES_FILE.exists():
        ORG_ALIASES = yaml.safe_load(ORG_ALIASES_FILE.read_text()) or {}
    if DEPARTMENT_OVERRIDES_FILE.exists():
        entries = yaml.safe_load(DEPARTMENT_OVERRIDES_FILE.read_text()) or []
        DEPARTMENT_OVERRIDES = {(e["match_organization"], e["match_department"]): e for e in entries}

    colleagues = yaml.safe_load(colleagues_file.read_text())

    g = Graph()
    g.bind("sdo", SDO)

    skipped = []
    for entry in colleagues:
        orcid_id = entry["orcid"]
        try:
            record = fetch_record(orcid_id, cache_dir, force_refresh=args.refresh)
        except requests.exceptions.RequestException as e:
            print(f"WARNING: skipping {orcid_id}, fetch failed: {e}", file=sys.stderr)
            skipped.append(orcid_id)
            continue
        add_person(g, orcid_id, record)

    quality_report = apply_quality_gate(g)
    write_report(quality_report, quality_report_path)
    write_person_reports(quality_report, quality_report_person_dir)

    g.serialize(destination=str(output_path), format="turtle")
    print(f"Wrote {len(g)} triples to {output_path}")
    if quality_report:
        print(f"{len(quality_report)} entr(ies) failed quality checks - see {quality_report_path}", file=sys.stderr)
    if skipped:
        print(f"Skipped {len(skipped)} colleague(s) due to fetch errors: {', '.join(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main()
