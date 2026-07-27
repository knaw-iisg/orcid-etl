# colleague-graph

Builds an RDF graph (schema.org / sdo terms) describing colleagues, sourced
from their public ORCID records: profile, employment, works, and fundings.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Colleagues are personally-identifying curation data, not code, so they and
everything derived from them live outside this repo, in a data directory
(default `~/colleague-graph-data`; override with `--data-dir` or
`$COLLEAGUE_GRAPH_DATA_DIR`).

Add colleagues by ORCID iD to `<data-dir>/colleagues.yaml`:

```yaml
- orcid: "0000-0003-3902-3720"  # Richard Zijdeman
```

Then:

```
python3 scripts/build_colleague_graph.py
```

This writes `<data-dir>/colleagues.ttl`. Pass `--refresh` to bypass the
local ORCID response cache in `<data-dir>/orcid_cache/` and re-fetch
fresh data.

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
`<data-dir>/colleagues.ttl`. Instead, they're written to
`<data-dir>/colleague_orcid_issues.md` — a plain-language report of what
to go fix in ORCID directly, grouped by colleague — plus a paste-able
per-colleague snippet in `<data-dir>/colleague_orcid_issues/{lastname}-{orcid}.md`
for messaging that person directly. Re-running the script picks up the
fix automatically.
