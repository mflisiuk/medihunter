# Medihunter v2

Automatyczne polowanie na wolne wizyty w Medicover (online24.medicover.pl).

## Co robi

- Loguje się do portalu Medicover przez headless browser (Playwright)
- Przechwytuje tokeny OAuth2 (access + refresh)
- Szuka wolnych terminów przez API gateway
- Cache tokenów — kolejne uruchomienia nie wymagają ponownego logowania

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Stwórz `.env`:
```
MEDICOVER_USER=twoj_numer_karty
MEDICOVER_PASS=twoje_haslo
```

## Użycie

```python
from medicover_session import MedicoverSession

s = MedicoverSession('numer_karty', 'haslo')
s.log_in()

# Szukaj wolnych wizyt (np. ginekolog w Warszawie)
slots = s.search_appointments(
    region=204,
    specialization=30,
    start_date='2026-02-17'
)

for item in slots['items']:
    print(f"{item['appointmentDate']} - {item['doctorName']} @ {item['clinicName']}")
```

## Architektura

```
medicover_browser_auth.py  — Playwright login + token capture
medicover_session.py       — sesja API, refresh tokenów, szukanie wizyt
```

## Endpointy API

| Endpoint | Opis |
|----------|------|
| `oauth.medicover.pl/connect/token` | Token exchange + refresh |
| `api-gateway-online24.medicover.pl/appointments/api/v2/search-appointments/filters` | Filtry (kliniki, lekarze, regiony) |
| `api-gateway-online24.medicover.pl/appointments/api/v2/search-appointments/slots` | Wolne terminy |

## Bazowane na

Fork [apqlzm/medihunter](https://github.com/apqlzm/medihunter) — przepisany pod nowe API Medicovera (2026).
