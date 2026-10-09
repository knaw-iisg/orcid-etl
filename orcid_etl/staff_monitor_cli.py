"""Command-line entry point: ``python -m orcid_etl.staff_monitor_cli``.

Daily job: fetch the IISG staff page, remove colleagues.yaml entries for
anyone no longer listed (logged to orcid-etl-removals.log first, so a
mistake is revertable), and report anyone new who has or might have a
public ORCID (never auto-added -- see staff_monitor.py's module docstring).
Writes the report to <data-dir>/staff-monitor-report.md and, if
STAFF_MONITOR_MAIL_TO is set, emails it via smtplib using SMTP_HOST/PORT/
USER/PASSWORD from the environment (loaded from <data-dir>/.env same as
cli.py's own secrets).

Safe to run daily via cron/systemd: state carries over in
<data-dir>/staff_monitor_state.yaml.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import yaml

from . import discover, staff_monitor
from .cli import resolve_data_dir


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def send_report_email(report: str, run_date: dt.date) -> None:
    to_addr = os.environ.get("STAFF_MONITOR_MAIL_TO")
    if not to_addr:
        return
    host = os.environ.get("SMTP_HOST")
    if not host:
        print("STAFF_MONITOR_MAIL_TO is set but SMTP_HOST isn't -- skipping email", file=sys.stderr)
        return

    msg = EmailMessage()
    msg["Subject"] = f"IISG staff / ORCID monitor -- {run_date.isoformat()}"
    msg["From"] = os.environ.get("SMTP_FROM", to_addr)
    msg["To"] = to_addr
    msg.set_content(report)

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    with smtplib.SMTP(host, port, timeout=30) as server:
        if os.environ.get("SMTP_STARTTLS", "1") != "0":
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="same --data-dir as python -m orcid_etl.cli")
    parser.add_argument("--staff-url", default=discover.STAFF_URL)
    args = parser.parse_args(argv)

    data_dir = resolve_data_dir(args.data_dir)
    load_dotenv(data_dir / ".env")

    colleagues_file = data_dir / "colleagues.yaml"
    cache_dir = data_dir / "orcid_cache"
    state_file = data_dir / "staff_monitor_state.yaml"
    removals_log = data_dir / "orcid-etl-removals.log"
    additions_log = data_dir / "orcid-etl-additions.log"
    report_path = data_dir / "staff-monitor-report.md"

    colleagues = yaml.safe_load(colleagues_file.read_text()) if colleagues_file.exists() else []
    state = yaml.safe_load(state_file.read_text()) if state_file.exists() else None
    previous_names = set((state or {}).get("staff_names") or [])
    previous_count = (state or {}).get("count") or 0

    current_staff = discover.parse_staff_page(discover.fetch_staff_html(args.staff_url))

    anomaly = None
    departed = []
    if state is not None and staff_monitor.is_scrape_anomaly(previous_count, len(current_staff)):
        anomaly = (
            f"staff page returned {len(current_staff)} entries, down from {previous_count} "
            f"last run -- looks like a broken scrape, not a mass departure"
        )
    else:
        departed = staff_monitor.find_departed(colleagues, current_staff)
        if departed:
            with removals_log.open("a") as f:
                for entry in departed:
                    f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()}\t{entry['orcid']}\t{entry.get('staff_name')}\n")
            departed_orcids = {e["orcid"] for e in departed}
            colleagues = [e for e in colleagues if e["orcid"] not in departed_orcids]
            colleagues_file.write_text(yaml.safe_dump(colleagues, sort_keys=False, allow_unicode=True))

    new_members = staff_monitor.find_new_staff(current_staff, previous_names, colleagues)
    new_staff_results = [staff_monitor.check_new_staff_orcid(m, cache_dir) for m in new_members]
    new_staff_results = staff_monitor.filter_known_orcids(new_staff_results, colleagues)
    added, needs_review = staff_monitor.select_auto_add_candidates(new_staff_results)

    if added:
        with additions_log.open("a") as f:
            for member, match in added:
                f.write(
                    f"{dt.datetime.now(dt.timezone.utc).isoformat()}\t{match['orcid_id']}\t{member.name}\t"
                    f"{match['confidence']}\n"
                )
        colleagues = colleagues + [
            {"orcid": match["orcid_id"], "staff_name": member.name} for member, match in added
        ]
        colleagues_file.write_text(yaml.safe_dump(colleagues, sort_keys=False, allow_unicode=True))

    run_date = dt.date.today()
    report = staff_monitor.build_report(added, needs_review, departed, anomaly, run_date)
    report_path.write_text(report)
    print(report)

    if anomaly is None:
        state_file.write_text(yaml.safe_dump({
            "staff_names": [m.name for m in current_staff],
            "count": len(current_staff),
            "last_run": run_date.isoformat(),
        }))

    send_report_email(report, run_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
