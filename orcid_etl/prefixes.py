"""Namespace/prefix declarations used throughout the pipeline."""

from rdflib import Namespace

BASE = "https://iisg.amsterdam/"
ID = BASE + "id/"

DATASET = Namespace(ID + "dataset/")

# schema.org: https://, as SCHEMA-AP-NDE requires ("publishers MUST use the
# https://schema.org/ namespace for newly published datasets" --
# https://docs.nde.nl/schema-profile/). Matches dataverse-etl; biblio-etl,
# archive-etl, findingaid-etl and authorities-etl currently use http://,
# which is actually the non-compliant one of the two schemes -- see the
# cross-repo note in nde_ap.py.
SDO = Namespace("https://schema.org/")

# Not real external vocabularies -- local URI schemes for entities this
# pipeline itself mints (an organization with no ROR/FUNDREF identifier, a
# journal/periodical, and an internal-only hint attached to a node that's
# about to be pruned by the quality gate; see pipeline.py and quality_gate.py).
LOCAL_ORG = Namespace("urn:orcidgraph:org:")
LOCAL_PERIODICAL = Namespace("urn:orcidgraph:periodical:")
INTERNAL = Namespace("urn:orcidgraph:internal:")

DEFAULT_GRAPH = "https://iisg.amsterdam/graph/orcid"

NAMESPACE_BINDINGS = {
    "sdo": SDO,
    "dataset": DATASET,
}
