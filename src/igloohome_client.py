import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .models import GeneratedPin

logger = logging.getLogger(__name__)

DEFAULT_AUTH_URL = "https://auth.igloohome.co/oauth2/token"


def align_to_days(
    valid_from: datetime, valid_until: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Expand a period to whole local days, as the daily algoPIN endpoint requires.

    The start is floored to local midnight, the end ceiled to the next local
    midnight, so the PIN covers the entire requested period.
    """
    start = valid_from.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = valid_until.astimezone(tz)
    end_floor = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if end != end_floor:
        end_floor += timedelta(days=1)
    return start, end_floor


def align_to_hours(
    valid_from: datetime, valid_until: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Expand a period to whole local hours, as the hourly algoPIN endpoint requires.

    The start is floored to the top of the hour, the end ceiled to the next.
    """
    start = valid_from.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    end = valid_until.astimezone(tz)
    end_floor = end.replace(minute=0, second=0, microsecond=0)
    if end != end_floor:
        end_floor += timedelta(hours=1)
    return start, end_floor


class IgloohomeClient:
    """igloohome developer API client.

    Authenticates with OAuth2 client credentials: the token endpoint takes
    HTTP Basic auth (base64 of client_id:client_secret) and returns a
    short-lived bearer token, cached here until shortly before expiry.
    """

    def __init__(
        self,
        base_url: str,
        credentials_path: str,
        auth_url: str = DEFAULT_AUTH_URL,
        timezone_name: str = "Europe/Dublin",
    ):
        with open(credentials_path) as f:
            creds = json.load(f)
        self.client_id = creds["client_id"]
        self.client_secret = creds["client_secret"]
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.tz = ZoneInfo(timezone_name)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token
        logger.info("Fetching igloohome access token")
        response = requests.post(
            self.auth_url,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        self._token = data["access_token"]
        self._token_expires_at = now + int(data.get("expires_in", 3600))
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json",
            },
            timeout=20,
            **kwargs,
        )
        if not response.ok:
            logger.error(
                "igloohome %s %s -> %s: %s",
                method,
                path,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        return response.json() if response.content else {}

    def list_devices(self) -> list[dict]:
        data = self._request("GET", "/devices")
        if isinstance(data, dict):
            return data.get("payload") or data.get("devices") or []
        return data

    def create_monthly_algopin(
        self,
        lock_id: str,
        member_name: str,
        valid_from: datetime,
        valid_until: datetime,
        variance: int = 1,
    ) -> GeneratedPin:
        if variance not in (1, 2, 3):
            raise ValueError(f"variance must be 1, 2 or 3, got {variance}")
        # Daily algoPINs require whole days (29-367), hh:00:00 timestamps, and
        # the same hour on start and end — midnight-to-midnight satisfies all.
        # Shorter validity (e.g. capped by a membership renewal date) uses the
        # hourly endpoint, which has no 29-day minimum.
        start, end = align_to_days(valid_from, valid_until, self.tz)
        duration_days = (end.date() - start.date()).days
        if duration_days > 367:
            raise ValueError(
                f"algoPIN duration must be at most 367 days, got {duration_days} "
                "(check PIN_VALID_DAYS)"
            )
        if duration_days >= 29:
            kind = "daily"
        else:
            kind = "hourly"
            start, end = align_to_hours(valid_from, valid_until, self.tz)
            if end <= start:
                raise ValueError(f"algoPIN validity is empty: {start} -> {end}")
        payload = {
            "variance": variance,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "accessName": f"Ball machine - {member_name}",
        }
        data = self._request("POST", f"/devices/{lock_id}/algopin/{kind}", json=payload)
        return GeneratedPin(
            code=str(data.get("pin") or data.get("code") or data["algoPIN"]),
            valid_from=start,
            valid_until=end,
            provider_access_id=str(data.get("pinId") or data.get("id") or "") or None,
        )
