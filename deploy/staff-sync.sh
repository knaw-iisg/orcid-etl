#!/usr/bin/env bash
# Daily: regenerate the ORCID-candidates report (discover_cli -- a
# human-review tool, never writes colleagues.yaml on its own) and run the
# staff monitor (adds a new staff member iff their name matches the staff
# page AND exactly one ORCID candidate confirms IISG affiliation; removes
# anyone no longer listed on the staff page, logged first; reports anyone
# ambiguous or unconfirmed for manual review). See orcid_etl/discover.py
# and orcid_etl/staff_monitor.py for the respective policies.
#
# Runs before nightly-harvest.timer's 03:00 run (see ../../triplestore),
# so same-day additions/removals land in that night's knowledge graph
# build. Runs as the `silk` user via staff-sync.timer.
set -uo pipefail

ORCID_ETL="$HOME/pipelines/orcid-etl"
DATA_DIR="$HOME/orcid-etl-data"
LOG="$DATA_DIR/staff-sync.log"

{
  echo "=== staff sync started $(date -Is) ==="

  echo "--- discover_cli (candidate report, read-only) ---"
  "$ORCID_ETL/.venv/bin/python" -m orcid_etl.discover_cli --data-dir "$DATA_DIR"

  echo "--- staff_monitor_cli (add confirmed / remove departed / report the rest) ---"
  "$ORCID_ETL/.venv/bin/python" -m orcid_etl.staff_monitor_cli --data-dir "$DATA_DIR"

  echo "=== staff sync finished $(date -Is) ==="
  echo
} >> "$LOG" 2>&1
