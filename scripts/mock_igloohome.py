#!/usr/bin/env python3
"""Local mock of the igloohome API for end-to-end testing.

Imitates the token endpoint and the daily algoPIN endpoint, validating
requests against the documented constraints (Basic auth on the token call,
variance 1-3, YYYY-MM-DDThh:00:00+hh:mm dates, matching start/end hour,
29-367 day duration). Returns a fixed fake PIN.

Usage:
    python scripts/mock_igloohome.py [--port 9876]

Then point the app at it in .env:
    IGLOOHOME_BASE_URL=http://localhost:9876/igloohome
    IGLOOHOME_AUTH_URL=http://localhost:9876/oauth2/token
"""
import argparse
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN_PATH = "/oauth2/token"
ALGOPIN_RE = re.compile(
    r"^/igloohome/devices/(?P<device>[^/]+)/algopin/(?P<kind>daily|hourly)$"
)
DEVICES_PATH = "/igloohome/devices"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T(\d{2}):00:00[+-]\d{2}:\d{2}$")

MOCK_TOKEN = "mock-access-token"
MOCK_PIN = "1928374"
MOCK_DEVICE = {
    "deviceId": "MOCKDEVICE01",
    "deviceName": "Mock Padlock",
    "type": "Padlock",
}


class MockIgloohome(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == DEVICES_PATH:
            if not self._check_bearer():
                return
            return self._send(200, {"nextCursor": "", "payload": [MOCK_DEVICE]})
        self._send(404, {"error": f"unknown path {self.path}"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if self.path == TOKEN_PATH:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Basic "):
                return self._send(401, {"error": "expected Basic auth on token endpoint"})
            return self._send(
                200,
                {"access_token": MOCK_TOKEN, "expires_in": 86400, "token_type": "Bearer"},
            )

        match = ALGOPIN_RE.match(self.path)
        if match:
            if not self._check_bearer():
                return
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON body"})
            errors = self._validate_algopin(body, match["kind"])
            if errors:
                return self._send(400, {"errors": errors, "received": body})
            print(
                f"{match['kind']} algoPIN issued for device {match['device']}: "
                f"{json.dumps(body)}"
            )
            return self._send(200, {"pin": MOCK_PIN, "pinId": "MOCKPINID0001"})

        self._send(404, {"error": f"unknown path {self.path}"})

    @staticmethod
    def _validate_algopin(body: dict, kind: str) -> list[str]:
        errors = []
        if body.get("variance") not in (1, 2, 3):
            errors.append("variance must be 1, 2 or 3")
        start_match = DATE_RE.match(str(body.get("startDate", "")))
        end_match = DATE_RE.match(str(body.get("endDate", "")))
        if not start_match:
            errors.append("startDate must match YYYY-MM-DDThh:00:00+hh:mm")
        if not end_match:
            errors.append("endDate must match YYYY-MM-DDThh:00:00+hh:mm")
        if start_match and end_match:
            start = datetime.fromisoformat(body["startDate"])
            end = datetime.fromisoformat(body["endDate"])
            days = (end.date() - start.date()).days
            if kind == "daily":
                if start_match.group(1) != end_match.group(1):
                    errors.append("hour on startDate and endDate must be the same")
                if not 29 <= days <= 367:
                    errors.append(f"daily duration must be 29-367 days, got {days}")
            else:  # hourly has no same-hour rule and covers short validity
                if end <= start:
                    errors.append("endDate must be after startDate")
                if days >= 29:
                    errors.append(f"hourly duration must be under 29 days, got {days}")
        if not body.get("accessName"):
            errors.append("accessName is required")
        return errors

    def _check_bearer(self) -> bool:
        if self.headers.get("Authorization", "") != f"Bearer {MOCK_TOKEN}":
            self._send(401, {"error": "missing or wrong bearer token"})
            return False
        return True

    def _send(self, code: int, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print(f"mock-igloohome: {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="Mock igloohome API server")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()
    server = HTTPServer(("localhost", args.port), MockIgloohome)
    print(f"Mock igloohome API listening on http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
