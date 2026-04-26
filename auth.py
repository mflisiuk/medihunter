"""Medicover Online24 authentication — refresh_token + Playwright login fallback."""

import json
import time
from pathlib import Path

import requests

OAUTH_URL = "https://oauth.medicover.pl"
CLIENT_ID = "web"
TOKEN_CACHE = Path.home() / ".config" / "medicover" / "tokens.json"
CREDENTIALS_FILE = Path.home() / ".config" / "medicover" / "credentials.json"
BROWSER_STATE_FILE = Path.home() / ".config" / "medicover" / "browser_state.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _load_tokens() -> dict | None:
    if TOKEN_CACHE.exists():
        return json.loads(TOKEN_CACHE.read_text())
    return None


def _save_tokens(tokens: dict):
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tokens, ensure_ascii=False, indent=2))
    tmp.replace(TOKEN_CACHE)


def _is_token_valid(tokens: dict) -> bool:
    """Check if access_token is still within its lifetime (with 15s buffer)."""
    captured = tokens.get("captured_at", 0)
    expires_in = tokens.get("expires_in", 180)
    return time.time() < captured + expires_in - 15


def refresh_access_token(refresh_token: str) -> dict:
    """Refresh access token using OAuth refresh_token grant."""
    resp = requests.post(
        f"{OAUTH_URL}/connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        timeout=20,
    )
    resp.raise_for_status()
    tok = resp.json()
    return {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", refresh_token),
        "expires_in": tok.get("expires_in", 180),
        "scope": tok.get("scope"),
        "token_type": tok.get("token_type", "Bearer"),
        "captured_at": int(time.time()),
    }


def login_playwright(card_number: str, password: str, headless: bool = True, fresh: bool = False) -> dict:
    """Login via Playwright browser and capture tokens from response intercept.

    Saves browser cookies/localStorage after login so 'trusted device' persists.
    Set fresh=True to ignore saved browser state and force a clean session.
    """
    from playwright.sync_api import sync_playwright

    tokens: dict = {}
    login_host = "login-online24.medicover.pl"
    portal_home = "https://online24.medicover.pl/home"

    if fresh and BROWSER_STATE_FILE.exists():
        BROWSER_STATE_FILE.unlink()
        print("[AUTH] Usunięto zapisany stan przeglądarki (fresh login)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_kwargs = {"user_agent": USER_AGENT, "locale": "pl-PL"}
        if BROWSER_STATE_FILE.exists():
            ctx_kwargs["storage_state"] = str(BROWSER_STATE_FILE)
            print("[AUTH] Wczytano zapisany stan przeglądarki (cookies/localStorage)")
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        def on_response(resp):
            nonlocal tokens
            if tokens:
                return
            url = resp.url
            if "/connect/token" in url and resp.request.method == "POST":
                try:
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "application/json" in ct:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("access_token") and data.get("refresh_token"):
                            tokens = {
                                "access_token": data["access_token"],
                                "refresh_token": data["refresh_token"],
                                "expires_in": data.get("expires_in"),
                                "scope": data.get("scope"),
                                "token_type": data.get("token_type", "Bearer"),
                                "captured_at": int(time.time()),
                            }
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(portal_home, wait_until="domcontentloaded", timeout=30000)

        # Wait for redirect to login page
        try:
            page.wait_for_url(f"**://{login_host}/**", timeout=30000)
        except Exception:
            browser.close()
            raise RuntimeError(f"Did not redirect to login host. URL: {page.url}")

        page.wait_for_timeout(2000)

        # Dismiss cookie consent
        for sel in ['#cmpwelcomebtnyes', 'button:has-text("Akceptuję")', '.cmpboxbtnyes']:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue

        try:
            page.evaluate('document.querySelectorAll("#cmpbox, #cmpbox2, .cmpboxBG").forEach(el => el.remove())')
        except Exception:
            pass

        # Fill login form
        user_filled = False
        for sel in ['input[name="Input.Username"]', 'input#Input_Username', 'input[type="text"]']:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(card_number)
                    user_filled = True
                    break
            except Exception:
                continue

        if not user_filled:
            browser.close()
            raise RuntimeError("Username field not found on login page")

        # Try password field
        pass_filled = False
        for sel in ['input[name="Input.Password"]', 'input#Input_Password', 'input[type="password"]']:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(password)
                    pass_filled = True
                    break
            except Exception:
                continue

        # Submit
        for sel in ['button[type="submit"]', 'button:has-text("Zaloguj")', 'button:has-text("Dalej")']:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    break
            except Exception:
                continue
        else:
            page.keyboard.press("Enter")

        # If 2-step login, wait for password field
        if not pass_filled:
            try:
                page.wait_for_selector('input[type="password"]', timeout=15000, state="visible")
                page.wait_for_timeout(500)
                for sel in ['input[name="Input.Password"]', 'input[type="password"]']:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.fill(password)
                            break
                    except Exception:
                        continue
                for sel in ['button[type="submit"]', 'button:has-text("Zaloguj")']:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click()
                            break
                    except Exception:
                        continue
                else:
                    page.keyboard.press("Enter")
            except Exception:
                pass

        # Wait for token capture or portal to load
        start = time.time()
        while time.time() - start < 60:
            if tokens:
                break

            # Check if we hit MFA
            url = page.url
            if "mfa" in url.lower() or "authenticator" in url.lower():
                print("\n[MFA] Wymagane uwierzytelnienie wieloskładnikowe (SMS).")

                # Debug: save screenshot and HTML of MFA page
                debug_dir = Path.home() / ".config" / "medicover" / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(debug_dir / "mfa_page.png"), full_page=True)
                    (debug_dir / "mfa_page.html").write_text(page.content())
                    print(f"[MFA] Debug: screenshot+HTML saved to {debug_dir}")
                except Exception:
                    pass

                # Check "trusted device" checkbox
                try:
                    trusted_cb = page.locator('#isTrustedDeviceCheckbox')
                    if trusted_cb.count() > 0:
                        if not trusted_cb.is_checked():
                            trusted_cb.check()
                            print("[MFA] Zaznaczono 'Zaufane urządzenie'")
                    else:
                        for sel in ['input[name="Input.IsTrustedDevice"]', 'input[name="IsDeviceTrusted"]']:
                            loc = page.locator(sel)
                            if loc.count() > 0:
                                if not loc.first.is_checked():
                                    loc.first.check()
                                break
                except Exception:
                    pass

                # Prompt for MFA code in terminal
                mfa_code = input("[MFA] Wpisz kod SMS: ").strip()
                if not mfa_code:
                    browser.close()
                    raise RuntimeError("MFA code not provided")

                # Fill MFA code — handle 6-pin group (each digit in separate input)
                code_filled = False
                pin_group = page.locator('.mfa-pin-group input')
                if pin_group.count() >= len(mfa_code):
                    print(f"[MFA] Wykryto pin group ({pin_group.count()} pól), wpisuję cyfry...")
                    for i, digit in enumerate(mfa_code):
                        inp = pin_group.nth(i)
                        inp.click()
                        inp.press_sequentially(digit, delay=80)
                    code_filled = True
                    print("[MFA] Kod wpisany w pin group")

                if not code_filled:
                    # Fallback: single input field
                    for sel in ['input[name="Code"]', 'input#Code', 'input[name="Input.Code"]', 'input[autocomplete="one-time-code"]']:
                        try:
                            loc = page.locator(sel)
                            if loc.count() > 0 and loc.first.is_visible():
                                loc.first.press_sequentially(mfa_code, delay=80)
                                code_filled = True
                                print(f"[MFA] Kod wpisany w pole: {sel}")
                                break
                        except Exception:
                            continue

                if not code_filled:
                    browser.close()
                    raise RuntimeError("MFA code input field not found")

                # Wait for JS to enable the submit button (requires all 6 digits)
                try:
                    page.wait_for_selector('button#mfa-button:not([disabled])', timeout=5000)
                    print("[MFA] Przycisk Dalej aktywny")
                except Exception:
                    page.wait_for_timeout(1000)
                    print("[MFA] Timeout czekania na aktywny przycisk, próbuję mimo to")

                # Submit MFA form
                submitted = False
                for sel in ['button#mfa-button', 'button[value="confirm"]', 'button[type="submit"]', 'button:has-text("Dalej")', 'button:has-text("Weryfikuj")', 'button:has-text("Zatwierdź")']:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(force=True)
                            submitted = True
                            print(f"[MFA] Kliknięto: {sel}")
                            break
                    except Exception:
                        continue

                if not submitted:
                    page.keyboard.press("Enter")
                    print("[MFA] Wysłano Enter")

                print("[MFA] Kod wysłany, czekam na odpowiedź...")

                # Wait for token capture after MFA
                mfa_start = time.time()
                while time.time() - mfa_start < 60:
                    if tokens:
                        break
                    page.wait_for_timeout(500)
                break

            page.wait_for_timeout(500)

        # Fallback: extract from localStorage
        if not tokens:
            try:
                storage = page.evaluate("""() => {
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        if (key.includes('oidc') && key.includes('web')) {
                            try {
                                const val = JSON.parse(localStorage.getItem(key));
                                if (val.access_token && val.refresh_token) return val;
                            } catch(e) {}
                        }
                    }
                    return null;
                }""")
                if storage and storage.get("access_token"):
                    tokens = {
                        "access_token": storage["access_token"],
                        "refresh_token": storage.get("refresh_token", ""),
                        "expires_in": storage.get("expires_in"),
                        "scope": storage.get("scope"),
                        "token_type": "Bearer",
                        "captured_at": int(time.time()),
                    }
            except Exception:
                pass

        # Save browser state (cookies + localStorage) so trusted device persists
        try:
            BROWSER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(BROWSER_STATE_FILE))
            print("[AUTH] Zapisano stan przeglądarki (zaufane urządzenie zapamiętane)")
        except Exception as e:
            print(f"[AUTH] Nie udało się zapisać stanu przeglądarki: {e}")

        browser.close()

    if not tokens:
        raise RuntimeError("Nie udało się przechwycić tokenów. Możliwe MFA/captcha lub zmieniony flow.")

    return tokens


def get_valid_token() -> str:
    """Get a valid access_token. Refreshes or re-logs-in as needed.

    Returns the access_token string.
    """
    tokens = _load_tokens()

    # Try cached access_token
    if tokens and _is_token_valid(tokens):
        return tokens["access_token"]

    # Try refresh_token
    if tokens and tokens.get("refresh_token"):
        try:
            new_tokens = refresh_access_token(tokens["refresh_token"])
            _save_tokens(new_tokens)
            print("[AUTH] Token refreshed successfully")
            return new_tokens["access_token"]
        except Exception as e:
            print(f"[AUTH] Refresh failed: {e}")

    # Need full login
    creds = _load_credentials()
    if not creds:
        raise RuntimeError(
            "Brak credentials. Ustaw kartę i hasło:\n"
            f"  Zapisz do {CREDENTIALS_FILE}\n"
            "  lub uruchom: python monitor.py login"
        )

    print("[AUTH] Logowanie przez Playwright...")
    new_tokens = login_playwright(creds["card_number"], creds["password"], fresh=False)
    _save_tokens(new_tokens)
    print("[AUTH] Login successful, tokens cached")
    return new_tokens["access_token"]


def _load_credentials() -> dict | None:
    if CREDENTIALS_FILE.exists():
        return json.loads(CREDENTIALS_FILE.read_text())
    return None


def save_credentials(card_number: str, password: str):
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps({"card_number": card_number, "password": password}))
    CREDENTIALS_FILE.chmod(0o600)
    print(f"[AUTH] Credentials saved to {CREDENTIALS_FILE}")


def force_login(fresh: bool = False):
    """Force a full Playwright login, ignoring cached tokens."""
    creds = _load_credentials()
    if not creds:
        card = input("Numer karty: ").strip()
        pwd = input("Hasło: ").strip()
        save_credentials(card, pwd)
        creds = _load_credentials()

    tokens = login_playwright(creds["card_number"], creds["password"], headless=True, fresh=fresh)
    _save_tokens(tokens)
    print("[AUTH] Force login successful")
