import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Start from portal home — let the app generate the full authorize URL
# (with all required params like code_challenge, device_id, app_version, etc.)
PORTAL_HOME = "https://online24.medicover.pl/home"
TOKEN_URL_SUBSTR = "/connect/token"
LOGIN_HOST = "login-online24.medicover.pl"


def _cache_path(username: str) -> Path:
    base = Path(os.path.expanduser("~/.cache/medihunter")) / username
    base.mkdir(parents=True, exist_ok=True)
    return base / "tokens.json"


def login_and_capture_tokens(username: str, password: str, headless: bool = True, timeout_sec: int = 180, debug_dir: str | None = None):
    """Login using real browser and capture OAuth token response.

    Strategy (v2 — fixed unauthorized_client):
    - Start at online24.medicover.pl/home (portal generates proper authorize URL)
    - Wait for redirect to login-online24 host
    - Fill login form (handles both 1-step and 2-step variants)
    - Capture JSON from POST to */connect/token via response intercept

    If debug_dir is provided, saves screenshot/html on failure.
    """
    tokens: dict = {}
    debug_path = Path(debug_dir) if debug_dir else None
    if debug_path:
        debug_path.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="pl-PL",
        )
        page = context.new_page()

        def on_response(resp):
            nonlocal tokens
            try:
                if tokens:
                    return
                url = resp.url
                if TOKEN_URL_SUBSTR in url and resp.request.method == "POST":
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "application/json" in ct:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("access_token") and data.get("refresh_token"):
                            tokens = {
                                "access_token": data.get("access_token"),
                                "refresh_token": data.get("refresh_token"),
                                "expires_in": data.get("expires_in"),
                                "scope": data.get("scope"),
                                "token_type": data.get("token_type"),
                                "captured_at": int(time.time()),
                            }
            except Exception:
                return

        page.on("response", on_response)

        # Step 1: Go to portal home — it will redirect through authorize -> login
        page.goto(PORTAL_HOME, wait_until="domcontentloaded", timeout=30000)

        # Step 2: Wait until we land on the login host (login-online24.medicover.pl)
        # The portal should redirect: online24 -> oauth.medicover.pl/connect/authorize -> login-online24
        try:
            page.wait_for_url(f"**://{LOGIN_HOST}/**", timeout=30000)
        except Exception:
            # Maybe it landed on oauth error or somewhere else — dump debug
            if debug_path:
                page.screenshot(path=str(debug_path / "no-redirect-to-login.png"), full_page=True)
                (debug_path / "no-redirect.html").write_text(page.content())
                (debug_path / "current-url.txt").write_text(page.url)
            browser.close()
            raise RuntimeError(f"Did not redirect to login host. Ended up at: {page.url}")

        # Small wait for JS to settle
        page.wait_for_timeout(2000)

        # Step 2.5: Dismiss cookie consent popup if present (blocks clicks otherwise)
        cookie_dismiss_selectors = [
            '#cmpwelcomebtnyes',           # "Akceptuję" / accept all
            'button:has-text("Akceptuję")',
            'button:has-text("Zgadzam")',
            'button:has-text("Accept")',
            '#cmpbntyestxt',               # text inside accept button
            '.cmpboxbtnyes',               # generic consent accept class
        ]
        for sel in cookie_dismiss_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue

        # If the overlay is still there, try to remove it via JS
        try:
            page.evaluate("""
                document.querySelectorAll('#cmpbox, #cmpbox2, .cmpboxBG').forEach(el => el.remove());
            """)
        except Exception:
            pass

        page.wait_for_timeout(500)

        # Step 3: Fill login form
        # Variant A: 1-step (username + password on same screen)
        # Variant B: 2-step (username first, then password after "Dalej"/"Next")

        # Username selectors (try multiple)
        user_selectors = [
            'input[name="Input.Username"]',
            'input#Input_Username',
            'input[autocomplete="username"]',
            'input[name="username"]',
            'input[type="text"]',
            'input[type="email"]',
        ]

        pass_selectors = [
            'input[name="Input.Password"]',
            'input#Input_Password',
            'input[autocomplete="current-password"]',
            'input[name="password"]',
            'input[type="password"]',
        ]

        def _fill_first(selectors, value, description="field"):
            for sel in selectors:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(value)
                    return True
            return False

        def _find_submit():
            """Find and click submit button."""
            for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Zaloguj")', 'button:has-text("Dalej")', 'button:has-text("Next")', 'button:has-text("Login")']:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    return True
            # Fallback: Enter key
            page.keyboard.press("Enter")
            return True

        # Try to fill username
        ok_user = _fill_first(user_selectors, username, "username")
        if not ok_user:
            if debug_path:
                page.screenshot(path=str(debug_path / "no-username-field.png"), full_page=True)
                (debug_path / "login-page.html").write_text(page.content())
                (debug_path / "login-url.txt").write_text(page.url)
            browser.close()
            raise RuntimeError(f"Username field not found on login page: {page.url}")

        # Check if password field is visible (1-step) or not (2-step)
        ok_pass = _fill_first(pass_selectors, password, "password")

        if ok_pass:
            # 1-step: both fields visible, submit
            _find_submit()
        else:
            # 2-step: submit username first, wait for password field
            _find_submit()

            # Wait for password field to appear
            try:
                page.wait_for_selector('input[type="password"]', timeout=15000, state="visible")
            except Exception:
                if debug_path:
                    page.screenshot(path=str(debug_path / "no-password-after-username.png"), full_page=True)
                    (debug_path / "step2.html").write_text(page.content())
                browser.close()
                raise RuntimeError("Password field did not appear after submitting username (2-step login)")

            page.wait_for_timeout(500)
            ok_pass = _fill_first(pass_selectors, password, "password")
            if not ok_pass:
                if debug_path:
                    page.screenshot(path=str(debug_path / "password-visible-but-cant-fill.png"), full_page=True)
                    (debug_path / "step2-fill.html").write_text(page.content())
                browser.close()
                raise RuntimeError("Password field appeared but could not be filled")

            _find_submit()

        # Step 4: Wait for token capture
        # Strategy A: response intercept may already have caught /connect/token
        # Strategy B: wait for portal to load, then extract tokens from localStorage/sessionStorage
        # Strategy C: trigger an API call to force a token refresh and capture it

        # Wait a bit for redirect chain to complete and portal to load
        start = time.time()
        while time.time() - start < min(timeout_sec, 30):
            if tokens:
                break
            page.wait_for_timeout(500)

        # If intercept didn't catch it, try extracting from browser storage
        if not tokens:
            try:
                storage_tokens = page.evaluate("""() => {
                    const result = {};
                    // Check localStorage
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        const val = localStorage.getItem(key);
                        if (val && (key.toLowerCase().includes('token') || key.toLowerCase().includes('oidc') || key.toLowerCase().includes('auth'))) {
                            result['ls_' + key] = val;
                        }
                        // Also try parsing JSON values
                        try {
                            const parsed = JSON.parse(val);
                            if (parsed && (parsed.access_token || parsed.accessToken)) {
                                result['ls_parsed_' + key] = parsed;
                            }
                        } catch(e) {}
                    }
                    // Check sessionStorage
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        const val = sessionStorage.getItem(key);
                        if (val && (key.toLowerCase().includes('token') || key.toLowerCase().includes('oidc') || key.toLowerCase().includes('auth'))) {
                            result['ss_' + key] = val;
                        }
                        try {
                            const parsed = JSON.parse(val);
                            if (parsed && (parsed.access_token || parsed.accessToken)) {
                                result['ss_parsed_' + key] = parsed;
                            }
                        } catch(e) {}
                    }
                    return result;
                }""")
                if debug_path:
                    (debug_path / "storage-dump.json").write_text(json.dumps(storage_tokens, ensure_ascii=False, indent=2))

                # Try to find tokens in extracted storage
                for key, val in storage_tokens.items():
                    if isinstance(val, dict):
                        at = val.get("access_token") or val.get("accessToken")
                        rt = val.get("refresh_token") or val.get("refreshToken")
                        if at:
                            tokens = {
                                "access_token": at,
                                "refresh_token": rt or "",
                                "expires_in": val.get("expires_in") or val.get("expiresIn"),
                                "scope": val.get("scope"),
                                "token_type": val.get("token_type") or "Bearer",
                                "captured_at": int(time.time()),
                            }
                            break
                    elif isinstance(val, str) and len(val) > 100:
                        # Could be a raw JWT access token
                        try:
                            parsed = json.loads(val)
                            at = parsed.get("access_token") or parsed.get("accessToken")
                            if at:
                                tokens = {
                                    "access_token": at,
                                    "refresh_token": parsed.get("refresh_token") or parsed.get("refreshToken") or "",
                                    "expires_in": parsed.get("expires_in"),
                                    "scope": parsed.get("scope"),
                                    "token_type": "Bearer",
                                    "captured_at": int(time.time()),
                                }
                                break
                        except Exception:
                            pass
            except Exception as e:
                if debug_path:
                    (debug_path / "storage-error.txt").write_text(str(e))

        # Strategy C: If still no tokens, navigate to a known API endpoint to trigger a token refresh
        if not tokens:
            try:
                # Trigger any API call — the portal's JS will use its stored token in Authorization header
                # We capture it from outgoing requests
                captured_auth = {}

                def on_request(req):
                    auth = req.headers.get("authorization") or ""
                    if auth.startswith("Bearer ") and len(auth) > 50:
                        captured_auth["access_token"] = auth.split(" ", 1)[1]

                page.on("request", on_request)

                # Navigate to a page that triggers API calls
                page.goto("https://online24.medicover.pl/home", wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(3000)

                if captured_auth.get("access_token"):
                    tokens = {
                        "access_token": captured_auth["access_token"],
                        "refresh_token": "",  # We'll get this from cookie/storage if possible
                        "expires_in": None,
                        "scope": None,
                        "token_type": "Bearer",
                        "captured_at": int(time.time()),
                    }
            except Exception as e:
                if debug_path:
                    (debug_path / "api-trigger-error.txt").write_text(str(e))

        if not tokens and debug_path:
            page.screenshot(path=str(debug_path / "no-tokens.png"), full_page=True)
            (debug_path / "after-login.html").write_text(page.content())
            (debug_path / "after-login-url.txt").write_text(page.url)

            # Dump all cookies for debugging
            cookies = context.cookies()
            (debug_path / "cookies.json").write_text(json.dumps(cookies, ensure_ascii=False, indent=2))

        browser.close()

    if not tokens:
        raise RuntimeError("Could not capture tokens within timeout. Possible MFA/captcha or changed flow. Check debug_auth/ for screenshots.")

    return tokens


def save_tokens(username: str, tokens: dict):
    path = _cache_path(username)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tokens, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return str(path)


def load_tokens(username: str):
    path = _cache_path(username)
    if not path.exists():
        return None
    return json.loads(path.read_text())
