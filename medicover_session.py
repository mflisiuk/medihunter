import base64
import hashlib
import json
import os
import pickle
import re
import urllib.parse
from collections import namedtuple, deque
from datetime import datetime, timedelta

import appdirs
import requests
from bs4 import BeautifulSoup

from medicover_browser_auth import load_tokens, login_and_capture_tokens, save_tokens

from fake_useragent import UserAgent

# NOTE(2026-02): Medicover moved auth to login-online24 + oauth.medicover.pl (OIDC Code + PKCE).
# Portal UI runs on online24.
BASE_HOST = "online24.medicover.pl"
BASE_URL = "https://" + BASE_HOST

# OIDC / OAuth broker
BASE_OAUTH_URL = "https://oauth.medicover.pl"

# New login UI host (also hosts some user/profile APIs)
BASE_LOGIN_HOST = "login-online24.medicover.pl"
BASE_LOGIN_URL = "https://" + BASE_LOGIN_HOST

# API Gateway for appointments search
BASE_API_GATEWAY = "https://api-gateway-online24.medicover.pl"

ua = UserAgent()
USER_AGENT = ua.random

Appointment = namedtuple(
    "Appointment",
    ["doctor_name", "clinic_name", "specialization_name", "appointment_datetime", "is_phone_consultation"],
)


class MedicoverSession:
    """Creating (log_in) and killing (log_out) session."""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self.cookies_path = appdirs.user_cache_dir("medihunter", "medihunter") + "/cookies-" + username

    def save_cookies(self):
        os.makedirs(os.path.dirname(self.cookies_path), exist_ok=True)
        with open(self.cookies_path, 'wb') as f:
            pickle.dump(self.session.cookies, f)

    def load_cookies(self):
        try:
            with open(self.cookies_path, 'rb') as f:
                self.session.cookies.update(pickle.load(f))
        except:
            pass

    def extract_data_from_login_form(self, page_text: str):
        """Extract values from input fields and prepare data for login request.

        New login UI (login-online24) expects form fields like:
        - Input.Username, Input.Password, __RequestVerificationToken
        - Input.ReturnUrl, Input.LoginType, Input.Button
        """
        soup = BeautifulSoup(page_text, "html.parser")

        def _get_input(name: str):
            tag = soup.find("input", attrs={"name": name})
            return tag.get("value") if tag else None

        return_url = _get_input("Input.ReturnUrl") or _get_input("ReturnUrl")
        vtoken = _get_input("__RequestVerificationToken")

        data = {
            "Input.ReturnUrl": return_url or "",
            "Input.LoginType": "FullLogin",
            "Input.Username": self.username,
            "Input.Password": self.password,
            "Input.Button": "login",
            "__RequestVerificationToken": vtoken or "",
            "Input.IsSimpleAccessRegulationAccepted": "false",
        }
        return data

    def extract_data_from_mfa_form(self, page_text: str, code: str):
        """ Extract values from mfa form fields. """
        data = {"Code": code, "IsDeviceTrusted": "true"}
        soup = BeautifulSoup(page_text, "html.parser")
        for input_tag in soup.find_all("input"):
            if input_tag["name"] == "__RequestVerificationToken":
                data["__RequestVerificationToken"] = input_tag["value"]
        return data

    def form_to_dict(self, page_text):
        """ Extract values from input fields. """
        data = {}
        soup = BeautifulSoup(page_text, "html.parser")
        for input_tag in soup.find_all("input"):
            if input_tag["name"] == "code":
                data["code"] = input_tag["value"]
            elif input_tag["name"] == "id_token":
                data["id_token"] = input_tag["value"]
            elif input_tag["name"] == "scope":
                data["scope"] = input_tag["value"]
            elif input_tag["name"] == "state":
                data["state"] = input_tag["value"]
            elif input_tag["name"] == "session_state":
                data["session_state"] = input_tag["value"]
        return data

    def _pkce_verifier(self, nbytes: int = 32) -> str:
        # RFC 7636: code_verifier is high-entropy cryptographic random string.
        return base64.urlsafe_b64encode(os.urandom(nbytes)).decode().rstrip("=")

    def _pkce_challenge_s256(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _extract_return_url_from_login_url(self, login_url: str) -> str:
        parsed = urllib.parse.urlparse(login_url)
        qs = urllib.parse.parse_qs(parsed.query)
        return qs.get("ReturnUrl", [""])[0]

    def oauth_sign_in(self, page_text):
        """Legacy helper (kept for compatibility)."""
        soup = BeautifulSoup(page_text, "html.parser")
        return soup.form["action"] if soup.form else None

    def log_in(self):
        """Login to Medicover online24 (request-only).

        Current flow (2026): OIDC Code + PKCE handled by login-online24.
        We emulate browser navigation to obtain authenticated cookies on online24.
        """
        self.load_cookies()

        # Fast path: if we already have a refresh_token, try refreshing access token.
        # (This won't validate API permissions but avoids unnecessary logins.)
        if self.refresh_token:
            try:
                self._refresh_access_token()
                return self.session.get(
                    f"{BASE_LOGIN_URL}/api/v4/available-profiles/me",
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT, **self._token_headers()},
                    timeout=15,
                )
            except Exception:
                pass

        # Preferred (stable) path: use real browser (Playwright) once to capture refresh_token.
        cached = load_tokens(self.username)
        if cached and cached.get("refresh_token"):
            self.refresh_token = cached.get("refresh_token")
            try:
                self._refresh_access_token()
                me = self.session.get(
                    f"{BASE_LOGIN_URL}/api/v4/available-profiles/me",
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Origin": BASE_URL, "Referer": BASE_URL + "/home", **self._token_headers()},
                    timeout=20,
                )
                me.raise_for_status()
                return me
            except Exception:
                pass

        # No cached token or refresh failed -> interactive browser login (headless by default)
        tokens = login_and_capture_tokens(self.username, self.password, headless=True, timeout_sec=180)
        self.access_token = tokens.get("access_token")
        self.refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in")
        if expires_in:
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in) - 10)
        save_tokens(self.username, tokens)

        me = self.session.get(
            f"{BASE_LOGIN_URL}/api/v4/available-profiles/me",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Origin": BASE_URL, "Referer": BASE_URL + "/home", **self._token_headers()},
            timeout=20,
        )
        me.raise_for_status()
        return me

    def _token_headers(self):
        if not self.access_token:
            return {}
        return {
            "Authorization": f"Bearer {self.access_token}",
        }

    def _exchange_code_for_token(self, code: str, code_verifier: str, redirect_uri: str):
        data = {
            "grant_type": "authorization_code",
            "client_id": "web",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        resp = self.session.post(
            f"{BASE_OAUTH_URL}/connect/token",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            timeout=20,
        )
        resp.raise_for_status()
        tok = resp.json()
        self.access_token = tok.get("access_token")
        self.refresh_token = tok.get("refresh_token")
        # expires_in is seconds
        expires_in = tok.get("expires_in")
        if expires_in:
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in) - 10)
        return tok

    def _refresh_access_token(self):
        if not self.refresh_token:
            raise RuntimeError("No refresh_token available")
        data = {
            "grant_type": "refresh_token",
            "client_id": "web",
            "refresh_token": self.refresh_token,
        }
        resp = self.session.post(
            f"{BASE_OAUTH_URL}/connect/token",
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            timeout=20,
        )
        resp.raise_for_status()
        tok = resp.json()
        self.access_token = tok.get("access_token")
        # some servers rotate refresh_token
        self.refresh_token = tok.get("refresh_token") or self.refresh_token
        expires_in = tok.get("expires_in")
        if expires_in:
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in) - 10)
        return tok

    def _ensure_token(self):
        if self.access_token and self.token_expires_at and datetime.utcnow() < self.token_expires_at:
            return
        if self.refresh_token:
            self._refresh_access_token()

    def _parse_search_results(self, result):
        """
        take search results in json format end transporm it to list of namedtuples
        """

        result = result.json()
        result = result["items"]
        appointments = []

        for r in result:
            appointments.append(self.convert_search_result_to_appointment(r))

        return appointments

    def convert_search_result_to_appointment(self, r):
        appointment = Appointment(
            doctor_name=r["doctorName"],
            clinic_name=r["clinicName"],
            specialization_name=r["specializationName"].strip(),
            appointment_datetime=r["appointmentDate"],
            is_phone_consultation=r["isPhoneConsultation"],
        )
        return appointment

    def search_appointment_filters(self, region_id: int, specialty_id: int, slot_search_type: str = "Standard"):
        """Fetch filters data for appointments search (clinics/doctors/languages/etc)."""
        self._ensure_token()
        url = f"{BASE_API_GATEWAY}/appointments/api/v2/search-appointments/filters"
        resp = self.session.get(
            url,
            params={
                "RegionIds": int(region_id),
                "SlotSearchType": slot_search_type,
                "SpecialtyIds": int(specialty_id),
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": BASE_URL,
                "Referer": BASE_URL + "/home",
                "User-Agent": USER_AGENT,
                **self._token_headers(),
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def search_appointments(self, *args, **kwargs):
        """Search free appointment slots (NEW API gateway).

        This replaces legacy /api/MyVisits/SearchFreeSlotsToBook.

        Required kwargs: region, specialization, start_date (YYYY-mm-dd)
        Optional: page, page_size
        """
        self._ensure_token()

        region_id = int(kwargs.get("region")) if kwargs.get("region") is not None else None
        specialty_id = int(kwargs.get("specialization")) if kwargs.get("specialization") is not None else None
        start_date = kwargs.get("start_date")
        if not region_id or not specialty_id or not start_date:
            raise RuntimeError("search_appointments requires region, specialization, start_date")

        url = f"{BASE_API_GATEWAY}/appointments/api/v2/search-appointments/slots"
        page = int(kwargs.get("page") or 1)
        page_size = int(kwargs.get("page_size") or 5000)
        resp = self.session.get(
            url,
            params={
                "Page": page,
                "PageSize": page_size,
                "RegionIds": region_id,
                "SlotSearchType": "Standard",
                "SpecialtyIds": specialty_id,
                "StartTime": start_date,
                "isOverbookingSearchDisabled": "false",
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": BASE_URL,
                "Referer": BASE_URL + "/home",
                "User-Agent": USER_AGENT,
                **self._token_headers(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def load_search_form(self):
        return self.session.get(
            BASE_URL + "/MyVisits",
            params={"bookingTypeId": 2, "mex": "True", "pfm": 1},
        )

    def log_out(self):
        """Logout from Medicover website"""
        next_url = BASE_URL+"/Users/Account/LogOff"
        self.headers.update({"Referer": BASE_URL + "/"})
        self.headers.update(self.session.headers)
        response = self.session.get(next_url, headers=self.headers)
        self.session.close()
        self.save_cookies()
        return response

    def get_plan(self):
        """Download Medicover plan"""
        output = ""
        medical_services_website = self.session.get(
            BASE_URL + "/Medicover.MedicalServices/MedicalServices", headers={
                "Host": BASE_HOST,
                "Origin": BASE_URL,
                "User-Agent": USER_AGENT,
            }
        )
        medical_services_website.raise_for_status()
        soup = BeautifulSoup(medical_services_website.content, "lxml")
        drop_down = soup.find("select")
        drop_down_options = drop_down.find_all("option")
        for option in drop_down_options:
            option_id = option["value"]
            if option_id == "":
                continue
            option_html = self.session.get(
                f"{BASE_URL}/MedicalServices/MedicalServices/ShowResults?serviceId={option_id}"
                , headers={
                    "Host": BASE_HOST,
                    "Origin": BASE_URL,
                    "User-Agent": USER_AGENT,
                }
            )
            option_html.raise_for_status()
            soup2 = BeautifulSoup(option_html.content, "lxml")
            option_header = soup2.find("h4").text
            option_header = option_header.replace("\r\n", "").replace("\n", "")
            option_texts = []
            for p_tag in soup2.find_all("p"):
                option_texts.append(
                    p_tag.text.strip().replace("\r\n", "").replace("\n", "")
                )
            option_text = "\t|".join(option_texts)
            option_result = f"{option_id}\t{option_header}\t{option_text}"
            print(option_result)
            output = output + option_result + "\n"

        return output

    def get_appointments(self, not_before):
        """Download all past and future appointments."""
        appointments = deque()
        page = 1
        while True:
            response = self.session.post(
                BASE_URL + "/api/MyVisits/SearchVisitsToView",
                headers={
                    # Makes the response come as json.
                    "X-Requested-With": "XMLHttpRequest",
                    "Host": BASE_HOST,
                    "Origin": BASE_URL,
                    "User-Agent": USER_AGENT,
                },
                data={
                    "Page": page,
                    "PageSize": 12,
                },
            )
            response.raise_for_status()
            response_json = response.json()
            finish = False
            for r in response_json["items"]:
                appointment = self.convert_search_result_to_appointment(r)
                if datetime.strptime(appointment.appointment_datetime, "%Y-%m-%dT%H:%M:%S") < not_before:
                    finish = True
                    break
                appointments.appendleft(appointment)
            if finish:
                break
            if len(appointments) >= response_json["totalCount"]:
                break
            # Just in case the condition above fails for some reason.
            if not len(response_json["items"]):
                break
            page += 1
        return list(appointments)

    def load_available_regions(self):
        """Download available region names and ids.

        NOTE: online24 UI no longer exposes the old /api/MyVisits/... JSON endpoints reliably.
        Regions list can be derived from appointments filters endpoint if needed.
        For now we keep legacy behavior by raising a clear error.
        """
        raise RuntimeError("load_available_regions: legacy endpoint deprecated; use appointments gateway filters")

    def load_available_specializations(self, region, bookingtype):
        raise RuntimeError("legacy filters deprecated; use appointments gateway")

    def load_available_clinics(self, region, bookingtype, specialization):
        raise RuntimeError("legacy filters deprecated; use appointments gateway")

    def load_available_doctors(self, region, bookingtype, specialization, clinic):
        raise RuntimeError("legacy filters deprecated; use appointments gateway")

    def _get_filters_data(self, *args, **kwargs):
        raise RuntimeError("legacy filters deprecated; use appointments gateway")
