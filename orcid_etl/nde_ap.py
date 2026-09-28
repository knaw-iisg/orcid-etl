"""NDE Schema.org Application Profile (SCHEMA-AP-NDE) enrichment, applied to
each colleague's Person node after their ORCID activities are mapped.

Reference: https://docs.nde.nl/schema-profile/ -- per-item (here: Person)
requirements used here: a persistent URI (colleagues are minted at their own
real https://orcid.org/<orcid-id>, so this is already satisfied for free),
sdo:name, and sdo:isPartOf linking to the dataset.

What this deliberately does *not* do: register this dataset in the NDE
Dataset Register. That needs a license IRI, catalog IRI and access-rights
statement that aren't derivable from this codebase -- see the ``# TODO(IISG)``
marker below. This is the same open gap tracked in knaw-iisg/biblio-etl#5,
knaw-iisg/archive-etl#4, knaw-iisg/findingaid-etl#3 and
knaw-iisg/authorities-etl#1 -- not a regression specific to this pipeline.
A minimal sdo:Dataset node is still emitted so that sdo:isPartOf resolves to
something.
"""
from __future__ import annotations

from rdflib import RDF, Graph, Literal, URIRef

from .prefixes import DATASET, SDO

DATASET_IRI = DATASET["orcid"]


def _emit_dataset_description(g: Graph) -> None:
    if (DATASET_IRI, RDF.type, SDO.Dataset) in g:
        return
    g.add((DATASET_IRI, RDF.type, SDO.Dataset))
    g.add((DATASET_IRI, SDO.name, Literal("KNAW/IISG medewerkers ORCID-profielen", lang="nl")))
    g.add((DATASET_IRI, SDO.name, Literal("KNAW/IISG Staff ORCID Profiles", lang="en")))
    g.add((DATASET_IRI, SDO.description, Literal(
        "Profile, employment, works and funding data for KNAW/IISG-affiliated "
        "researchers, sourced from their public ORCID records.", lang="en",
    )))
    # TODO(IISG): sdo:license (a license IRI), sdo:includedInDataCatalog (the
    # NDE Dataset Register catalog IRI this dataset is registered under) and
    # sdo:accessRights aren't in the source pipeline or its config, and
    # shouldn't be guessed -- fill in once known.


def enrich(person_uri: URIRef, g: Graph, *, dataset_iri: URIRef = DATASET_IRI) -> None:
    g.add((person_uri, SDO.isPartOf, dataset_iri))
    _emit_dataset_description(g)
