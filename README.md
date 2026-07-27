# colleague-graph

Builds an RDF graph (schema.org / sdo terms) describing colleagues, sourced
from their public ORCID records: profile, employment, works, and fundings.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Add colleagues by ORCID iD to `data/colleagues.yaml`, then:

```
python3 scripts/build_colleague_graph.py
```

This writes `data/colleagues.ttl`. Pass `--refresh` to bypass the local
ORCID response cache in `data/orcid_cache/` and re-fetch fresh data.

## Data quality

Organizations are deduplicated using ROR/FUNDREF identifiers where ORCID
provides them. Two curated files handle the cases it doesn't:

- `data/org_aliases.yaml` — merges an organization URI the script would
  otherwise mint (e.g. a name-derived fallback) into a canonical one.
- `data/org_department_overrides.yaml` — resolves cases where ORCID's
  "organization" field names an umbrella institution but the department
  field names the actual sub-institute (e.g. staff formally employed
  under an academy, with a specific institute as their department).

Organizations that still have no resolvable identifier fail the SHACL
shape in `data/shapes/organization_quality.ttl` and are excluded from
`data/colleagues.ttl`. Instead, they're written to
`data/colleague_orcid_issues.md` — a plain-language report, per colleague,
of what to go fix in ORCID directly (with a link to the exact entry).
Re-running the script picks up the fix automatically.
