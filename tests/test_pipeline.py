"""Exercises the mapping against a hand-built, entirely synthetic ORCID
record (fake person, fake institutions) -- no real colleague data, and no
network access."""
from __future__ import annotations

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import XSD

from orcid_etl import nde_ap
from orcid_etl.pipeline import OrgConfig, add_person
from orcid_etl.prefixes import SDO

FAKE_ORCID = "0000-0000-0000-0001"

SYNTHETIC_RECORD = {
    "person": {
        "name": {
            "given-names": {"value": "Ada"},
            "family-name": {"value": "Testperson"},
        }
    },
    "activities-summary": {
        "employments": {
            "affiliation-group": [
                {
                    "summaries": [
                        {
                            "employment-summary": {
                                "path": f"/{FAKE_ORCID}/employment/1",
                                "role-title": "Researcher",
                                "start-date": {"year": {"value": "2020"}},
                                "end-date": None,
                                "organization": {
                                    "name": "Example Research Institute",
                                    "disambiguated-organization": {
                                        "disambiguated-organization-identifier": "https://ror.org/0000example",
                                        "disambiguation-source": "ROR",
                                    },
                                    "address": {"city": "Amsterdam", "country": "NL"},
                                },
                            }
                        }
                    ]
                },
                {
                    "summaries": [
                        {
                            "employment-summary": {
                                "path": f"/{FAKE_ORCID}/employment/2",
                                "role-title": "Fellow",
                                "start-date": {"year": {"value": "2018"}},
                                "end-date": {"year": {"value": "2019"}},
                                "organization": {
                                    "name": "Some Unidentified Institute",
                                    "disambiguated-organization": None,
                                },
                            }
                        }
                    ]
                },
            ]
        },
        "works": {
            "group": [
                {
                    "work-summary": [
                        {
                            "path": f"/{FAKE_ORCID}/work/1",
                            "type": "journal-article",
                            "title": {"title": {"value": "A Synthetic Study of Synthetic Things"}},
                            "publication-date": {"year": {"value": "2022"}, "month": {"value": "05"}},
                            "journal-title": {"value": "Journal of Fabricated Examples"},
                            "external-ids": {
                                "external-id": [
                                    {"external-id-type": "doi", "external-id-value": "10.1234/example.doi"}
                                ]
                            },
                        }
                    ]
                }
            ]
        },
        "fundings": {
            "group": [
                {
                    "funding-summary": [
                        {
                            "path": f"/{FAKE_ORCID}/funding/1",
                            "title": {"title": {"value": "Example Grant"}},
                            "organization": {
                                "name": "Example Funding Body",
                                "disambiguated-organization": {
                                    "disambiguated-organization-identifier": "https://ror.org/0000funder",
                                    "disambiguation-source": "ROR",
                                },
                            },
                            "external-ids": {
                                "external-id": [
                                    {"external-id-type": "grant_number", "external-id-value": "EG-123"}
                                ]
                            },
                        }
                    ]
                }
            ]
        },
    },
}


def _build():
    g = Graph()
    config = OrgConfig()  # no aliases/overrides -- exercises the plain paths
    person_uri = add_person(g, FAKE_ORCID, SYNTHETIC_RECORD, config)
    nde_ap.enrich(person_uri, g)
    return g, person_uri


def test_person_core_fields():
    g, person_uri = _build()
    assert person_uri == URIRef(f"https://orcid.org/{FAKE_ORCID}")
    assert (person_uri, RDF.type, SDO.Person) in g
    assert str(next(g.objects(person_uri, SDO.name))) == "Ada Testperson"
    assert str(next(g.objects(person_uri, SDO.givenName))) == "Ada"
    assert str(next(g.objects(person_uri, SDO.familyName))) == "Testperson"


def test_employment_with_ror_identified_org():
    g, person_uri = _build()
    org = URIRef("https://ror.org/0000example")
    assert (org, RDF.type, SDO.Organization) in g
    assert (org, SDO.identifier, None) in g  # has a resolvable identifier -> passes the quality gate
    roles = list(g.objects(person_uri, SDO.worksFor))
    assert len(roles) == 2  # both employments present before the quality gate runs
    ror_role = next(r for r in roles if (r, SDO.worksFor, org) in g)
    assert str(next(g.objects(ror_role, SDO.roleName))) == "Researcher"
    start = next(g.objects(ror_role, SDO.startDate))
    assert str(start) == "2020-01-01"
    assert start.datatype == XSD.date


def test_employment_with_unidentified_org_has_no_ror_style_identifier():
    g, _ = _build()
    orgs = list(g.subjects(RDF.type, SDO.Organization))
    unidentified = next(o for o in orgs if str(next(g.objects(o, SDO.name))) == "Some Unidentified Institute")
    assert str(unidentified).startswith("urn:orcidgraph:org:")
    assert (unidentified, SDO.identifier, None) not in g  # this is exactly what the SHACL shape flags


def test_work_uses_doi_as_its_own_uri():
    g, person_uri = _build()
    work = URIRef("https://doi.org/10.1234/example.doi")
    assert (work, RDF.type, SDO.ScholarlyArticle) in g
    assert str(next(g.objects(work, SDO.name))) == "A Synthetic Study of Synthetic Things"
    assert (work, SDO.creator, person_uri) in g
    journal = next(g.objects(work, SDO.isPartOf))
    assert (journal, RDF.type, SDO.Periodical) in g
    assert str(next(g.objects(journal, SDO.name))) == "Journal of Fabricated Examples"


def test_funding():
    g, person_uri = _build()
    grant = URIRef(f"https://orcid.org/{FAKE_ORCID}/funding/1")
    assert (grant, RDF.type, SDO.MonetaryGrant) in g
    assert (grant, SDO.fundedItem, person_uri) in g
    assert str(next(g.objects(grant, SDO.identifier))) == "EG-123"
    funder = next(g.objects(grant, SDO.funder))
    assert str(funder) == "https://ror.org/0000funder"


def test_nde_ap_dataset_registration():
    g, person_uri = _build()
    assert (person_uri, SDO.isPartOf, nde_ap.DATASET_IRI) in g
    assert (nde_ap.DATASET_IRI, RDF.type, SDO.Dataset) in g
    assert any(g.objects(nde_ap.DATASET_IRI, SDO.name))


def test_quality_gate_prunes_unidentified_organization():
    from orcid_etl.quality_gate import apply_quality_gate

    g, person_uri = _build()
    before = len(list(g.objects(person_uri, SDO.worksFor)))
    report = apply_quality_gate(g)
    after = len(list(g.objects(person_uri, SDO.worksFor)))

    assert before == 2
    assert after == 1  # the unidentified-org employment was pruned
    assert len(report) == 1
    assert report[0]["organization"] == "Some Unidentified Institute"
    assert report[0]["kind"] == "employment"
