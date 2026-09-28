"""Maps one ORCID record (profile, employment, works, fundings - education
and other affiliation/activity types are intentionally left out) to RDF in
schema.org (sdo) terms.

Organization identity is handled directly by ORCID/ROR/FUNDREF/DOI: unlike
biblio-etl/archive-etl/findingaid-etl, which mint bare local authority IRIs
that authorities-etl later fills in with names and sameAs links, every
Person/Organization/CreativeWork node here is minted at its own real,
dereferenceable external identifier from the start (orcid.org, ror.org,
doi.org). There is deliberately no local "authority" layer to build.
"""
from __future__ import annotations

import re

import yaml
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from .prefixes import INTERNAL, LOCAL_ORG, LOCAL_PERIODICAL, SDO

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


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def format_date(d: dict | None) -> str | None:
    """ORCID dates are partial (year, or year+month, or full). Returns an
    xsd:date string anchored to day/month 01 when missing, or None."""
    if not d or not d.get("year"):
        return None
    year = d["year"]["value"]
    month = (d.get("month") or {}).get("value") or "01"
    day = (d.get("day") or {}).get("value") or "01"
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def external_id(summary: dict, id_type: str) -> str | None:
    # ORCID sometimes has "external-ids": null rather than omitting the key,
    # so summary.get("external-ids", {}) isn't enough - .get()'s default only
    # kicks in when the key is *missing*, not when its value is None.
    external_ids = (summary.get("external-ids") or {}).get("external-id", []) or []
    for e in external_ids:
        if e["external-id-type"] == id_type:
            return e["external-id-value"]
    return None


def activity_uri(summary: dict) -> URIRef:
    """ORCID includes a stable permalink path on every activity summary
    (e.g. /0000-.../employment/25933636) - reuse it as the node's IRI."""
    return URIRef(f"https://orcid.org{summary['path']}")


class OrgConfig:
    """Loaded from data/org_aliases.yaml and data/org_department_overrides.yaml
    -- see those files for why each exists. Not colleague data: reusable
    across any colleague list, so (unlike colleagues.yaml) it lives in this
    repo, not the external data-dir."""

    def __init__(self, aliases: dict | None = None, department_overrides: dict | None = None):
        self.aliases = aliases or {}
        self.department_overrides = department_overrides or {}

    @classmethod
    def load(cls, aliases_path, department_overrides_path) -> "OrgConfig":
        aliases = {}
        if aliases_path.exists():
            aliases = yaml.safe_load(aliases_path.read_text()) or {}
        department_overrides = {}
        if department_overrides_path.exists():
            entries = yaml.safe_load(department_overrides_path.read_text()) or []
            department_overrides = {(e["match_organization"], e["match_department"]): e for e in entries}
        return cls(aliases, department_overrides)


def org_uri(org: dict, config: OrgConfig) -> URIRef:
    dis = org.get("disambiguated-organization")
    ident = dis.get("disambiguated-organization-identifier") if dis else None
    # ROR/FUNDREF identifiers are already dereferenceable URLs; reuse them
    # so the same organization is shared across colleagues. Other sources
    # (e.g. RINGGOLD) are bare numbers, and orgs with no disambiguation at
    # all - both fall back to a name-derived local URI below.
    raw_uri = ident if (ident and ident.startswith("http")) else str(LOCAL_ORG[slugify(org["name"])])
    return URIRef(config.aliases.get(raw_uri, raw_uri))


def add_organization(g: Graph, org: dict, config: OrgConfig) -> URIRef:
    uri = org_uri(org, config)
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


def synthetic_org(spec: dict) -> dict:
    """Builds an org dict shaped like ORCID's own "organization" field,
    from a data/org_department_overrides.yaml entry."""
    return {
        "name": spec["name"],
        "disambiguated-organization": {
            "disambiguated-organization-identifier": spec["ror"],
            "disambiguation-source": "ROR",
        },
    }


def add_employments(g: Graph, person_uri: URIRef, activities: dict, config: OrgConfig) -> None:
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
            override = config.department_overrides.get((org_dict["name"], department))
            if override:
                org_dict = synthetic_org(override["organization"])
            else:
                # Near miss: this org matches a known umbrella institution
                # in org_department_overrides.yaml, but not with this
                # department (or none was given) - worth telling the
                # colleague exactly what to add rather than leaving them
                # to guess.
                known_departments = sorted({
                    dept for (name, dept) in config.department_overrides if name == org_dict["name"] and dept
                })
                if known_departments:
                    g.add((role_uri, INTERNAL.reportReason, Literal(
                        "no institutional identifier (e.g. ROR) attached in ORCID for this employer. "
                        f"If this role is actually within {' or '.join(known_departments)}, "
                        "add that as the department on this entry in ORCID to resolve it automatically."
                    )))

            org = add_organization(g, org_dict, config)
            g.add((role_uri, SDO.worksFor, org))
            g.add((person_uri, SDO.worksFor, role_uri))

            if override and override.get("parent_organization"):
                parent = add_organization(g, synthetic_org(override["parent_organization"]), config)
                g.add((org, SDO.parentOrganization, parent))


def add_works(g: Graph, person_uri: URIRef, activities: dict) -> None:
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

            g.add((work_uri, SDO.creator, person_uri))


def add_fundings(g: Graph, person_uri: URIRef, activities: dict, config: OrgConfig) -> None:
    for group in (activities.get("fundings") or {}).get("group", []):
        for s in group["funding-summary"]:
            grant_uri = activity_uri(s)
            g.add((grant_uri, RDF.type, SDO.MonetaryGrant))

            title = ((s.get("title") or {}).get("title") or {}).get("value")
            if title:
                g.add((grant_uri, SDO.name, Literal(title)))

            if s.get("organization"):
                funder = add_organization(g, s["organization"], config)
                g.add((grant_uri, SDO.funder, funder))

            # schema.org has no dedicated "recipient" property on Grant;
            # fundedItem is the documented way to point a Grant at the
            # Person it funded (inverse: sdo:funding on the Person).
            g.add((grant_uri, SDO.fundedItem, person_uri))

            grant_number = external_id(s, "grant_number")
            if grant_number:
                g.add((grant_uri, SDO.identifier, Literal(grant_number)))


def add_person(g: Graph, orcid_id: str, record: dict, config: OrgConfig) -> URIRef:
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
    add_employments(g, person_uri, activities, config)
    add_works(g, person_uri, activities)
    add_fundings(g, person_uri, activities, config)

    return person_uri
