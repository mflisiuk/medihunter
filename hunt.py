#!/usr/bin/env python3
"""Medihunter v2 — simple appointment hunter CLI.

Features:
- Logs in via MedicoverSession (uses cached refresh_token when available)
- Resolves region/specialty by name (via filters endpoint)
- Polls slots endpoint on an interval
- Prints newly found slots to stdout

Notifications:
- For now: stdout only (clean + reliable)

Env:
- MEDICOVER_USER, MEDICOVER_PASS

Examples:
  python3 hunt.py --region Warszawa --spec urolog --days 14 --interval 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

from medicover_session import MedicoverSession


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _today_iso() -> str:
    # keep it simple; dates are used as YYYY-MM-DD strings
    return datetime.now().strftime("%Y-%m-%d")


def _parse_args():
    ap = argparse.ArgumentParser(description="Medihunter v2 — poll for free slots")
    ap.add_argument("--region", required=True, help="Region name, e.g. Warszawa")
    ap.add_argument("--spec", required=True, help="Specialty name, e.g. ginekolog, urolog dorośli")
    ap.add_argument("--start", default=_today_iso(), help="Start date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--days", type=int, default=14, help="How many days ahead to search (default: 14)")
    ap.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    ap.add_argument("--page-size", type=int, default=500, help="Slots page size (default: 500)")
    ap.add_argument("--once", action="store_true", help="Run once and exit")
    ap.add_argument("--json", action="store_true", help="Print new slots as JSON")
    return ap.parse_args()


def resolve_region_and_spec(session: MedicoverSession, region_name: str, spec_name: str):
    # We need *some* region/spec ids to call filters endpoint.
    # Trick: call filters with any plausible defaults, then match by names.
    # The API returns lists including regions and specialties.
    # We'll use region_id/spec_id after matching.

    # Try a couple of common IDs as bootstrap; if fails, we'll still have token errors anyway.
    bootstrap_region = 204
    bootstrap_spec = 30

    data = session.search_appointment_filters(region_id=bootstrap_region, specialty_id=bootstrap_spec)
    regions = data.get("regions", []) or []
    specs = data.get("specialties", []) or []

    target_region = None
    for r in regions:
        if _norm(r.get("value", "")) == _norm(region_name):
            target_region = r
            break
    if not target_region:
        # allow partial match
        for r in regions:
            if _norm(region_name) in _norm(r.get("value", "")):
                target_region = r
                break

    target_spec = None
    for s in specs:
        if _norm(s.get("value", "")) == _norm(spec_name):
            target_spec = s
            break
    if not target_spec:
        for s in specs:
            if _norm(spec_name) in _norm(s.get("value", "")):
                target_spec = s
                break

    if not target_region:
        raise SystemExit(f"Could not resolve region '{region_name}'. Available examples: {', '.join(sorted({r.get('value','') for r in regions if r.get('value')}) )[:200]}")
    if not target_spec:
        raise SystemExit(f"Could not resolve specialty '{spec_name}'. Available examples: {', '.join(sorted({s.get('value','') for s in specs if s.get('value')}) )[:200]}")

    return int(target_region["id"]), int(target_spec["id"]), target_region["value"], target_spec["value"]


def iter_dates(start_iso: str, days: int):
    start = datetime.strptime(start_iso, "%Y-%m-%d")
    for i in range(max(1, days)):
        yield (start + timedelta(days=i)).strftime("%Y-%m-%d")


def slot_key(item: dict) -> str:
    return "|".join(
        [
            str(item.get("appointmentDate") or ""),
            str(item.get("clinicName") or ""),
            str(item.get("doctorName") or ""),
            str(item.get("specializationName") or ""),
        ]
    )


def main():
    args = _parse_args()

    user = os.environ.get("MEDICOVER_USER")
    pwd = os.environ.get("MEDICOVER_PASS")
    if not user or not pwd:
        raise SystemExit("Missing MEDICOVER_USER / MEDICOVER_PASS in env (.env)")

    s = MedicoverSession(user, pwd)
    s.log_in()

    region_id, spec_id, region_label, spec_label = resolve_region_and_spec(s, args.region, args.spec)

    seen: set[str] = set()

    print(f"[medihunter] OK. Hunting: region={region_label} ({region_id}), spec={spec_label} ({spec_id}), start={args.start}, days={args.days}, interval={args.interval}s")
    sys.stdout.flush()

    while True:
        new_items = []

        for date_iso in iter_dates(args.start, args.days):
            resp = s.search_appointments(
                region=region_id,
                specialization=spec_id,
                start_date=date_iso,
                page_size=args.page_size,
            )
            items = resp.get("items", []) or []
            for it in items:
                k = slot_key(it)
                if k not in seen:
                    seen.add(k)
                    new_items.append(it)

        if new_items:
            if args.json:
                print(json.dumps({"found": len(new_items), "items": new_items}, ensure_ascii=False))
            else:
                print(f"[medihunter] FOUND {len(new_items)} new slot(s)")
                for it in new_items:
                    print(
                        f"  {it.get('appointmentDate')} | {it.get('specializationName')} | {it.get('clinicName')} | {it.get('doctorName')}"
                    )
            sys.stdout.flush()

        if args.once:
            return 0

        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
