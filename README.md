# orcid-etl

One of five KNAW/IISG ETL pipelines producing [NDE Schema.org Application
Profile](https://docs.nde.nl/schema-profile/)-conformant RDF, alongside
[biblio-etl](https://github.com/knaw-iisg/biblio-etl),
[archive-etl](https://github.com/knaw-iisg/archive-etl),
[findingaid-etl](https://github.com/knaw-iisg/findingaid-etl) and
[authorities-etl](https://github.com/knaw-iisg/authorities-etl). Builds an
RDF graph (schema.org / `sdo` terms) describing colleagues, sourced from
their public ORCID records: profile, employment, works, and fundings.

Unlike the other four, this isn't an OAI-PMH harvest of a catalog -- it
fetches a short, hand-maintained list of colleagues directly from ORCID's
public API. Every Person/Organization/CreativeWork node is minted at its own
real, dereferenceable identifier from the start (`orcid.org`, `ror.org`,
`doi.org`); there's no local "authority" layer for a later pipeline to fill
in, the way biblio-etl/archive-etl/findingaid-etl's bare `person:`/
`organization:` IRIs need authorities-etl.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

Colleagues are personally-identifying curation data, not code, so they and
everything derived from them live outside this repo, in a data directory
(default `~/orcid-etl-data`; override with `--data-dir` or
`$ORCID_ETL_DATA_DIR`).

Add colleagues by ORCID iD to `<data-dir>/colleagues.yaml`:

```yaml
- orcid: "0000-0003-3902-3720"  # Richard Zijdeman
```

Then:

```
python3 -m orcid_etl.cli
```

This writes `<data-dir>/orcid-etl.ttl`. Pass `--refresh` to bypass the local
ORCID response cache in `<data-dir>/orcid_cache/` and re-fetch fresh data, or
`--out PATH` to write the graph somewhere else instead -- e.g. straight into
a local triplestore's `sources/` directory, without moving any
personally-identifying curation data out of `--data-dir`.

## NDE-AP compliance

Every colleague's Person node gets `sdo:isPartOf` a minimal `sdo:Dataset`
registration node (`dataset:orcid`), matching the pattern the other four
pipelines use. As with all four of them, this dataset is **not** yet
registered in the NDE Dataset Register -- that needs a license IRI, catalog
IRI and access-rights statement nobody has supplied yet (see the
`# TODO(IISG)` marker in `orcid_etl/nde_ap.py`, and the matching open issues
on the other repos: biblio-etl#5, archive-etl#4, findingaid-etl#3,
authorities-etl#1).

## Data quality

Organizations are deduplicated using ROR/FUNDREF identifiers where ORCID
provides them. Two curated files handle the cases it doesn't:

- `data/org_aliases.yaml` — merges an organization URI the script would
  otherwise mint (e.g. a name-derived fallback) into a canonical one.
- `data/org_department_overrides.yaml` — resolves cases where ORCID's
  "organization" field names an umbrella institution but the department
  field names the actual sub-institute (e.g. staff formally employed
  under an academy, with a specific institute as their department).

Organizations that still have no resolvable identifier fail the SHACL shape
in `data/shapes/organization_quality.ttl` and are excluded from
`<data-dir>/orcid-etl.ttl`. Instead, they're written to
`<data-dir>/orcid-etl-issues.md` — a plain-language report of what to go fix
in ORCID directly, grouped by colleague — plus a paste-able per-colleague
snippet in `<data-dir>/orcid-etl-issues/{lastname}-{orcid}.md` for messaging
that person directly. Re-running the script picks up the fix automatically.

## Tests

```
pip install -e ".[test]"
pytest
```

Runs against one hand-built, entirely synthetic ORCID record -- no real
colleague data, no network access.
