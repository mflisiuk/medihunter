#!/usr/bin/env python3
"""Medicover Smart Monitor — search, monitor, and auto-book appointments.

Usage:
    python monitor.py search --specialty "Ortopedia i fizjoterapia" --doctor "Markiewicz" --date 2026-04-27
    python monitor.py monitor --profile fizjo_markiewicz
    python monitor.py book --booking-string <string>
    python monitor.py my-visits
    python monitor.py login
    python monitor.py specialties
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from api import MedicoverAPI, filter_slots
from auth import force_login, save_credentials
from config import find_specialty_name, get_profile, load_specialty_map, resolve_specialty_ids, load_config


def cmd_login(args):
    """Force re-login via Playwright."""
    if args.card and args.password:
        save_credentials(args.card, args.password)
    force_login(fresh=args.relogin)


def cmd_specialties(args):
    """List available specialties from the map."""
    smap = load_specialty_map()
    if args.filter:
        matches = find_specialty_name(args.filter)
        if not matches:
            print(f"Nie znaleziono specjalizacji pasujących do '{args.filter}'")
            return
        for name in matches:
            ids = smap[name]
            print(f"  {name} → IDs: {', '.join(ids)}")
    else:
        for name, ids in sorted(smap.items()):
            print(f"  {name} → IDs: {', '.join(ids)}")


def cmd_search(args):
    """Search for available appointment slots with optional filtering."""
    api = MedicoverAPI()

    # Resolve specialty
    specialty_ids = _resolve_specialties(args)

    # Date range
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = args.date or today
    end_date = args.date_to or None
    if not end_date and args.days:
        end_date = (datetime.now() + __import__("datetime").timedelta(days=args.days)).strftime("%Y-%m-%d")

    region = args.region or _default_region()

    all_slots = []
    for sid in specialty_ids:
        print(f"[SEARCH] Specialty ID {sid}, region {region}, od {start_date}...")
        try:
            raw = api.search_slots(
                region_id=region,
                specialty_id=sid,
                start_date=start_date,
                end_date=end_date,
                page_size=100,
            )
            items = _extract_items(raw)
            all_slots.extend(items)
            print(f"  → {len(items)} slotów")
        except Exception as e:
            print(f"  ✗ Błąd: {e}")

    # Deduplicate by bookingString
    seen = set()
    unique = []
    for s in all_slots:
        bs = s.get("bookingString", "")
        if bs not in seen:
            seen.add(bs)
            unique.append(s)
    all_slots = unique

    if not all_slots:
        print("\nBrak dostępnych slotów.")
        return

    # Apply filters
    filtered = filter_slots(
        all_slots,
        doctor_name=args.doctor,
        date_from=args.date,
        date_to=args.date_to,
        time_from=args.time_from,
        time_to=args.time_to,
    )

    print(f"\n{'='*70}")
    if args.doctor or args.time_from or args.time_to:
        print(f"Znaleziono {len(filtered)} slotów (z {len(all_slots)} łącznie po filtrach)")
    else:
        print(f"Znaleziono {len(filtered)} slotów")
    print(f"{'='*70}")

    for i, s in enumerate(filtered[:50]):
        date = s.get("appointmentDate", "?")
        doctor = (s.get("doctor") or {}).get("name", "?")
        clinic = (s.get("clinic") or {}).get("name", "?")
        specialty = (s.get("specialty") or {}).get("name", "?")
        bs = s.get("bookingString", "")[:40] + "..."
        print(f"\n  [{i}] {date}")
        print(f"      Lekarz: {doctor} | {specialty}")
        print(f"      Klinika: {clinic}")
        print(f"      bookingString: {bs}")

    # Save for later booking
    if filtered:
        slots_file = Path.home() / ".config" / "medicover" / "last_search.json"
        slots_file.parent.mkdir(parents=True, exist_ok=True)
        slots_file.write_text(json.dumps(filtered, indent=2, ensure_ascii=False))
        print(f"\n[✓] Sloty zapisane do {slots_file}")


def cmd_monitor(args):
    """Monitor for new slots matching criteria and optionally auto-book."""
    # Load profile if specified
    if args.profile:
        profile = get_profile(args.profile)
        if not profile:
            print(f"Profil '{args.profile}' nie istnieje w config.yaml")
            print("Dostępne profile:")
            config = load_config()
            for name in config.get("monitoring_profiles", {}):
                print(f"  - {name}")
            return
        # Merge profile into args
        args.doctor = args.doctor or profile.get("doctor")
        args.specialty = args.specialty or profile.get("specialty")
        args.specialty_id = args.specialty_id or (int(profile["specialty_id"]) if profile.get("specialty_id") else None)
        args.date = args.date or profile.get("date_from")
        args.date_to = args.date_to or profile.get("date_to")
        args.time_from = args.time_from or profile.get("time_from")
        args.time_to = args.time_to or profile.get("time_to")
        args.region = args.region or profile.get("region")
        if not args.interval:
            args.interval = profile.get("interval_min", profile.get("interval", 1200))
        if not args.interval_max:
            args.interval_max = profile.get("interval_max", 1800)
        if not args.auto_book and profile.get("auto_book"):
            args.auto_book = True

    specialty_ids = _resolve_specialties(args)
    if not specialty_ids:
        print("Brak specialty IDs. Użyj --specialty lub --specialty-id")
        return

    region = args.region or _default_region()
    interval_min = args.interval or 1200
    interval_max = args.interval_max or max(interval_min, 1800)
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = args.date or today
    end_date = args.date_to

    doctor = args.doctor
    time_from = args.time_from
    time_to = args.time_to

    print(f"\n[MONITOR] Start")
    print(f"  Lekarz: {doctor or 'dowolny'}")
    print(f"  Specialty IDs: {specialty_ids}")
    print(f"  Region: {region}")
    print(f"  Data: {start_date} - {end_date or 'brak limitu'}")
    print(f"  Godziny: {time_from or '00:00'} - {time_to or '23:59'}")
    print(f"  Interwał: {interval_min}-{interval_max}s ({interval_min // 60}-{interval_max // 60} min, losowy)")
    print(f"  Auto-book: {'TAK' if args.auto_book else 'NIE'}")
    print(f"  Ctrl+C aby zatrzymać\n")

    api = MedicoverAPI()
    known_slots = set()
    check_count = 0

    while True:
        check_count += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            all_slots = []
            for sid in specialty_ids:
                try:
                    raw = api.search_slots(
                        region_id=region,
                        specialty_id=sid,
                        start_date=start_date,
                        end_date=end_date,
                        page_size=100,
                    )
                    items = _extract_items(raw)
                    all_slots.extend(items)
                except Exception as e:
                    print(f"[{now_str}] Błąd specialty {sid}: {e}")

            # Deduplicate
            seen = set()
            unique = []
            for s in all_slots:
                bs = s.get("bookingString", "")
                if bs not in seen:
                    seen.add(bs)
                    unique.append(s)
            all_slots = unique

            # Filter
            filtered = filter_slots(
                all_slots,
                doctor_name=doctor,
                date_from=start_date,
                date_to=end_date,
                time_from=time_from,
                time_to=time_to,
            )

            # Find new slots
            current_keys = set()
            new_slots = []
            for s in filtered:
                key = f"{s.get('appointmentDate')}:{(s.get('doctor') or {}).get('name', '')}"
                current_keys.add(key)
                if key not in known_slots:
                    new_slots.append(s)

            if new_slots:
                print(f"\n{'='*70}")
                print(f"[{now_str}] NOWE SLOTY! ({len(new_slots)})")
                print(f"{'='*70}")
                for s in new_slots:
                    date = s.get("appointmentDate", "?")
                    doc = (s.get("doctor") or {}).get("name", "?")
                    clinic = (s.get("clinic") or {}).get("name", "?")
                    spec = (s.get("specialty") or {}).get("name", "?")
                    print(f"  {date} | {doc} | {clinic} | {spec}")

                    if args.auto_book:
                        print(f"  Rezerwacja...")
                        try:
                            result = api.book_appointment(s["bookingString"])
                            print(f"  ZAREZERWOWANO! Result: {json.dumps(result, ensure_ascii=False)[:200]}")
                        except Exception as e:
                            print(f"  Błąd rezerwacji: {e}")

                print(f"{'='*70}\n")
            else:
                print(f"[{now_str}] Sprawdzono: {len(all_slots)} slotów, {len(filtered)} po filtrach, 0 nowych (check #{check_count})")

            known_slots = current_keys

        except KeyboardInterrupt:
            print("\n\n[MONITOR] Zatrzymany.")
            break
        except Exception as e:
            print(f"[{now_str}] Błąd: {e}")

        try:
            sleep_sec = random.randint(interval_min, interval_max)
            next_check = datetime.now().strftime("%H:%M:%S")
            print(f"  Następne sprawdzenie za {sleep_sec // 60}m {sleep_sec % 60}s")
            time.sleep(sleep_sec)
        except KeyboardInterrupt:
            print("\n\n[MONITOR] Zatrzymany.")
            break


def cmd_book(args):
    """Book an appointment by bookingString."""
    booking_string = args.booking_string
    if not booking_string:
        # Try first slot from last search
        slots_file = Path.home() / ".config" / "medicover" / "last_search.json"
        if slots_file.exists():
            slots = json.loads(slots_file.read_text())
            if slots:
                booking_string = slots[0].get("bookingString")
                print(f"Używam pierwszego slota: {slots[0].get('appointmentDate')} - {slots[0].get('doctor', {}).get('name', '?')}")
        if not booking_string:
            print("Brak bookingString. Użyj --booking-string lub najpierw --search")
            return

    api = MedicoverAPI()
    print(f"[BOOK] Rezerwacja...")
    try:
        result = api.book_appointment(booking_string)
        print(f"Wynik: {json.dumps(result, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"Błąd rezerwacji: {e}")


def cmd_my_visits(args):
    """List your appointments."""
    api = MedicoverAPI()
    state = args.state or "Planned"
    print(f"\n[MY VISITS] Stan: {state}")
    try:
        data = api.get_my_visits(state=state)
        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not items:
            print("  Brak wizyt.")
            return
        for v in items:
            date = v.get("appointmentDate") or v.get("appointmentDateTime") or "?"
            if "T" in str(date):
                date = str(date).replace("T", " ")[:16]
            doctor = v.get("doctor", {}).get("name", v.get("doctorName", "?"))
            specialty = v.get("specialty", {}).get("name", v.get("specialtyName", "?"))
            clinic = v.get("clinic", {}).get("name", v.get("clinicName", "?"))
            state_val = v.get("appointmentState", v.get("state", "?"))
            appt_id = v.get("id", v.get("appointmentId", "?"))
            print(f"  {date} | {doctor} | {specialty} | {clinic} | ID: {appt_id} | {state_val}")
    except Exception as e:
        print(f"Błąd: {e}")


def cmd_filters(args):
    """Show filters (doctors, clinics) for a specialty."""
    api = MedicoverAPI()
    specialty_ids = _resolve_specialties(args)
    region = args.region or _default_region()

    for sid in specialty_ids:
        print(f"\n[FILTERS] Specialty ID {sid}, region {region}")
        try:
            data = api.get_filters(region_id=region, specialty_id=sid)
            # Doctors
            doctors = data.get("doctors", [])
            if doctors:
                print(f"  Lekarze ({len(doctors)}):")
                for d in doctors:
                    print(f"    - {d.get('name', '?')} (ID: {d.get('id', '?')})")
            # Clinics
            clinics = data.get("clinics", [])
            if clinics:
                print(f"  Kliniki ({len(clinics)}):")
                for c in clinics:
                    print(f"    - {c.get('name', '?')} (ID: {c.get('id', '?')})")
        except Exception as e:
            print(f"  Błąd: {e}")


# ─── Helpers ────────────────────────────────────────────────────────────────

def _resolve_specialties(args) -> list[int]:
    """Resolve specialty from args (name, id, or both)."""
    ids = []

    if args.specialty_id:
        ids.append(int(args.specialty_id))

    if args.specialty:
        resolved = resolve_specialty_ids(args.specialty)
        if resolved:
            ids.extend(resolved)
        else:
            # Try partial match
            matches = find_specialty_name(args.specialty)
            if matches:
                print(f"Nie znaleziono dokładnie '{args.specialty}'. Podobne:")
                for m in matches:
                    print(f"  - {m}")
                # Use first match
                resolved = resolve_specialty_ids(matches[0])
                ids.extend(resolved)
                print(f"Używam: {matches[0]}")
            else:
                print(f"Nie znaleziono specjalizacji '{args.specialty}'")
                print("Użyj 'python monitor.py specialties' aby zobaczyć listę")

    if not ids:
        print("Brak specialty. Użyj --specialty lub --specialty-id")

    return list(set(ids))


def _extract_items(raw) -> list:
    """Extract items list from API response."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("items", [])
    return []


def _default_region() -> int:
    config = load_config()
    return config.get("region", 204)


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Medicover Smart Monitor — szukaj, monitoruj, rezerwuj wizyty",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # login
    p_login = sub.add_parser("login", help="Zaloguj się (Playwright)")
    p_login.add_argument("--card", help="Numer karty Medicover")
    p_login.add_argument("--password", help="Hasło")
    p_login.add_argument("--relogin", action="store_true", help="Wyczyść zapisany stan przeglądarki i zaloguj od zera (nowe MFA)")

    # search
    p_search = sub.add_parser("search", help="Szukaj dostępnych slotów")
    p_search.add_argument("--specialty", help="Nazwa specjalizacji (np. 'Ortopedia i fizjoterapia')")
    p_search.add_argument("--specialty-id", type=int, help="ID specjalizacji (np. 163)")
    p_search.add_argument("--doctor", help="Imię/nazwisko lekarza (substring match)")
    p_search.add_argument("--region", type=int, help="Region ID (default: 204 = Warszawa)")
    p_search.add_argument("--date", help="Data od (YYYY-MM-DD)")
    p_search.add_argument("--date-to", help="Data do (YYYY-MM-DD)")
    p_search.add_argument("--days", type=int, help="Ile dni do przodu (default: 7)")
    p_search.add_argument("--time-from", help="Od godziny (HH:MM)")
    p_search.add_argument("--time-to", help="Do godziny (HH:MM)")

    # monitor
    p_monitor = sub.add_parser("monitor", help="Monitoruj sloty i rezerwuj automatycznie")
    p_monitor.add_argument("--profile", help="Nazwa profilu z config.yaml")
    p_monitor.add_argument("--specialty", help="Nazwa specjalizacji")
    p_monitor.add_argument("--specialty-id", type=int, help="ID specjalizacji")
    p_monitor.add_argument("--doctor", help="Imię/nazwisko lekarza")
    p_monitor.add_argument("--region", type=int, help="Region ID")
    p_monitor.add_argument("--date", help="Data od (YYYY-MM-DD)")
    p_monitor.add_argument("--date-to", help="Data do (YYYY-MM-DD)")
    p_monitor.add_argument("--time-from", help="Od godziny (HH:MM)")
    p_monitor.add_argument("--time-to", help="Do godziny (HH:MM)")
    p_monitor.add_argument("--interval", type=int, help="Minimalny interwał w sekundach (default: 1200)")
    p_monitor.add_argument("--interval-max", type=int, dest="interval_max", help="Maksymalny interwał w sekundach (default: 1800)")
    p_monitor.add_argument("--auto-book", action="store_true", help="Automatycznie rezerwuj pasujące sloty")

    # book
    p_book = sub.add_parser("book", help="Zarezerwuj wizytę")
    p_book.add_argument("--booking-string", help="bookingString z wyników wyszukiwania")

    # my-visits
    p_visits = sub.add_parser("my-visits", help="Lista Twoich wizyt")
    p_visits.add_argument("--state", help="Stan wizyt (Planned/Past/Cancelled)", default="Planned")

    # specialties
    p_spec = sub.add_parser("specialties", help="Lista specjalizacji")
    p_spec.add_argument("--filter", help="Filtruj po nazwie")

    # filters
    p_filters = sub.add_parser("filters", help="Pokaż lekarzy i kliniki dla specjalizacji")
    p_filters.add_argument("--specialty", help="Nazwa specjalizacji")
    p_filters.add_argument("--specialty-id", type=int, help="ID specjalizacji")
    p_filters.add_argument("--region", type=int, help="Region ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "login": cmd_login,
        "search": cmd_search,
        "monitor": cmd_monitor,
        "book": cmd_book,
        "my-visits": cmd_my_visits,
        "specialties": cmd_specialties,
        "filters": cmd_filters,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
