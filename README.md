# medihunter

Automatyczne wyszukiwanie, monitorowanie i rezerwowanie wizyt w Medicover Online24.

## Instalacja

```bash
pip install -r requirements.txt
playwright install chromium
```

## Pierwsze logowanie

```bash
python3 login.py
```

Podaj numer karty i hasło. Jeśli wymagane MFA (SMS), wpisz kod — skrypt automatycznie obsługuje 6-polowy PIN i zaznacza "Zaufane urządzenie". Tokeny zapisują się w `~/.config/medicover/tokens.json`.

Po pierwszym logowaniu tokeny odświeżają się automatycznie (refresh_token) — SMS potrzebny tylko ponownie gdy sesja wygaśnie.

## Użycie

### Szukanie wizyt

```bash
python3 monitor.py search \
  --specialty "Ortopedia i fizjoterapia" \
  --doctor "Markiewicz" \
  --date 2026-04-27 --date-to 2026-04-30
```

### Monitorowanie z auto-rezerwacją

Sprawdza co 20-30 min (losowo) i automatycznie rezerwuje pasujący slot:

```bash
python3 monitor.py monitor \
  --specialty "Ortopedia i fizjoterapia" \
  --doctor "Markiewicz" \
  --date 2026-04-27 --date-to 2026-04-30 \
  --interval 1200 --interval-max 1800 \
  --auto-book
```

Możesz uruchomić wiele instancji w tle (np. osobno dla różnych lekarzy):

```bash
nohup python3 monitor.py monitor --doctor "Markiewicz" --specialty "Ortopedia i fizjoterapia" --date 2026-04-27 --date-to 2026-04-30 --auto-book > markiewicz.log 2>&1 &
nohup python3 monitor.py monitor --doctor "Parol" --specialty "Ortopedia i fizjoterapia" --date 2026-04-27 --date-to 2026-04-30 --auto-book > parol.log 2>&1 &
```

### Profile w config.yaml

Zamiast podawać parametry w CLI, zdefiniuj profil:

```yaml
region: 204

monitoring_profiles:
  fizjo_markiewicz:
    doctor: "Marcin Markiewicz"
    specialty: "Ortopedia i fizjoterapia"
    date_from: "2026-04-27"
    date_to: "2026-05-15"
    time_from: "08:00"
    time_to: "12:00"
    interval_min: 1200
    interval_max: 1800
    auto_book: true
```

Potem uruchamiasz:

```bash
python3 monitor.py monitor --profile fizjo_markiewicz
```

### Lista specjalizacji

```bash
python3 monitor.py specialties
python3 monitor.py specialties --filter ortopedia
```

### Twoje wizyty

```bash
python3 monitor.py my-visits
python3 monitor.py my-visits --state Past
```

### Ręczna rezerwacja

```bash
python3 monitor.py book --booking-string "<bookingString z wyników search>"
```

### Ponowne logowanie

```bash
python3 monitor.py login
python3 monitor.py login --card 1234567 --password "hasło"
```

## Komendy

| Komenda | Opis |
|---------|------|
| `login` | Logowanie przez Playwright |
| `search` | Szukanie dostępnych slotów |
| `monitor` | Monitorowanie + auto-rezerwacja |
| `book` | Rezerwacja konkretnego slota |
| `my-visits` | Lista Twoich wizyt |
| `specialties` | Lista specjalizacji |
| `filters` | Lekarze i kliniki dla specjalizacji |

## Flagi monitora

| Flaga | Domyślnie | Opis |
|-------|-----------|------|
| `--specialty` | — | Nazwa specjalizacji |
| `--specialty-id` | — | ID specjalizacji |
| `--doctor` | dowolny | Filtr po nazwisku lekarza |
| `--region` | 204 (Warszawa) | Region |
| `--date` | dziś | Data od (YYYY-MM-DD) |
| `--date-to` | bez limitu | Data do (YYYY-MM-DD) |
| `--time-from` | 00:00 | Od godziny |
| `--time-to` | 23:59 | Do godziny |
| `--interval` | 1200s (20 min) | Min. interwał sprawdzania |
| `--interval-max` | 1800s (30 min) | Max interwał (losowy) |
| `--auto-book` | wyłączone | Automatyczna rezerwacja |
| `--profile` | — | Profil z config.yaml |

## Token flow

```
monitor.py → get_valid_token()
              ├─ access_token ważny? → użyj
              ├─ wygasł? → refresh_token (automatycznie, bez SMS)
              └─ refresh nie działa? → login.py (SMS wymagany)
```

## Struktura plików

```
auth.py              — OAuth2 + Playwright MFA login
api.py               — Medicover Online24 API client
monitor.py           — CLI: search / monitor / book / my-visits
config.py            — YAML config + specialty map loader
login.py             — Standalone login script
config.yaml          — Konfiguracja + profile monitorowania
data/specialty_map.json — Mapa specjalizacji → IDs
requirements.txt     — Zależności
```

## Dane

Poświadczenia i tokeny przechowywane lokalnie w `~/.config/medicover/`:
- `credentials.json` — numer karty + hasło
- `tokens.json` — OAuth2 tokeny (auto-odświeżane)
