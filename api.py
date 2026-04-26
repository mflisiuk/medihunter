"""Medicover Online24 API client — pure requests-based."""

from datetime import datetime
from urllib.parse import quote

import requests

from auth import get_valid_token

API_BASE = "https://api-gateway-online24.medicover.pl"
PORTAL_URL = "https://online24.medicover.pl"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class MedicoverAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": PORTAL_URL,
            "Referer": f"{PORTAL_URL}/home",
            "User-Agent": USER_AGENT,
        })

    def _headers(self) -> dict:
        token = get_valid_token()
        return {**self.session.headers, "Authorization": f"Bearer {token}"}

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list:
        url = f"{API_BASE}{endpoint}"
        resp = self.session.get(url, params=params, headers=self._headers(), timeout=30)
        if resp.status_code == 401:
            # Token expired mid-session, force refresh
            from auth import _load_tokens, refresh_access_token, _save_tokens
            tokens = _load_tokens()
            if tokens and tokens.get("refresh_token"):
                new = refresh_access_token(tokens["refresh_token"])
                _save_tokens(new)
                resp = self.session.get(url, params=params, headers=self._headers(), timeout=30)

        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "raw": resp.text[:500]}

    def search_slots(
        self,
        region_id: int = 204,
        specialty_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        page_size: int = 100,
        slot_search_type: str = "Standard",
    ) -> dict:
        """Search available appointment slots.

        Returns {"items": [...], "totalItems": N, ...} or raw list.
        """
        params = {
            "RegionIds": region_id,
            "SlotSearchType": slot_search_type,
            "Page": page,
            "PageSize": page_size,
        }
        if specialty_id is not None:
            params["SpecialtyIds"] = specialty_id
        if start_date:
            params["StartTime"] = start_date
        if end_date:
            params["EndTime"] = end_date
        params["isOverbookingSearchDisabled"] = "false"

        return self._get("/appointments/api/v2/search-appointments/slots", params=params)

    def book_appointment(self, booking_string: str) -> dict:
        """Book an appointment using the bookingString from a slot."""
        endpoint = (
            f"/appointments/api/v2/search-appointments/book-appointment"
            f"?bookingString={quote(booking_string)}"
            f"&source=direct"
            f"&searchTypeToUse=Standard"
        )
        return self._get(endpoint)

    def get_my_visits(self, state: str = "Planned", page: int = 1, page_size: int = 20) -> dict:
        """Get your appointments."""
        return self._get(
            "/appointments/api/v2/person-appointments/appointments",
            params={"AppointmentState": state, "Page": page, "PageSize": page_size},
        )

    def get_personal_data(self) -> dict:
        """Get personal data."""
        return self._get("/personal-data/api/personal")

    def get_keywords(self) -> dict:
        """Get all specializations/keywords."""
        return self._get("/service-selector-configurator-os/api/keywords")

    def get_filters(self, region_id: int = 204, specialty_id: int = 9) -> dict:
        """Get filters (doctors, clinics, languages) for a specialty in a region."""
        return self._get(
            "/appointments/api/v2/search-appointments/filters",
            params={"RegionIds": region_id, "SlotSearchType": "Standard", "SpecialtyIds": specialty_id},
        )

    def get_prescriptions(self, page_size: int = 10) -> dict:
        return self._get("/prescriptions/api/e-prescription", params={"PageSize": page_size})

    def get_referrals(self, page_size: int = 10) -> dict:
        return self._get("/referrals/api/referrals", params={"PageSize": page_size})

    def get_examination_results(self, page_size: int = 10) -> dict:
        return self._get("/medical-documents/api/v3/examinations-results", params={"PageSize": page_size})


def filter_slots(
    slots: list[dict],
    doctor_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
) -> list[dict]:
    """Filter slot results by doctor name, date range, and time range.

    All parameters are optional — only applied if provided.
    doctor_name: case-insensitive substring match
    date_from/date_to: YYYY-MM-DD
    time_from/time_to: HH:MM
    """
    results = []
    for s in slots:
        appt_date = s.get("appointmentDate", "")
        if not appt_date:
            continue

        dt = datetime.fromisoformat(appt_date)

        # Doctor filter
        if doctor_name:
            doc_name = s.get("doctor", {}).get("name", "")
            if doctor_name.lower() not in doc_name.lower():
                continue

        # Date range filter
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            if dt.date() < df.date():
                continue
        if date_to:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            if dt.date() > dt_to.date():
                continue

        # Time range filter
        if time_from:
            tf = datetime.strptime(time_from, "%H:%M")
            if dt.hour < tf.hour or (dt.hour == tf.hour and dt.minute < tf.minute):
                continue
        if time_to:
            tt = datetime.strptime(time_to, "%H:%M")
            if dt.hour > tt.hour or (dt.hour == tt.hour and dt.minute > tt.minute):
                continue

        results.append(s)

    return results
