"""
Thin client for the public ORCID API (no auth needed for public data).

Responses are cached to disk under data/orcid_cache/ so re-running the
graph builder doesn't re-hit ORCID every time. Pass force_refresh=True
(or --refresh on build_colleague_graph.py) to bypass the cache.
"""
import json
import time
from pathlib import Path

import requests

API_BASE = "https://pub.orcid.org/v3.0"
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2


def fetch_record(orcid_id, cache_dir, force_refresh=False):
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / f"{orcid_id}.json"

    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text())

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                f"{API_BASE}/{orcid_id}/record",
                headers={"Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            record = response.json()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(record, indent=2))
            return record
        except requests.exceptions.RequestException as e:
            last_error = e
            # A 404 (bad ORCID iD) won't fix itself on retry - fail fast.
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None and e.response.status_code == 404:
                break
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_error
