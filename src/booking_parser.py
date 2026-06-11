import re
from datetime import datetime, time
from hashlib import sha256
from typing import Optional

from .models import Booking


# Booking confirmations from ebookingonline.net look like:
#
#   Subject: Court Booking Confirmation: 9:00 - 10:00 am , Saturday 13th June 2026
#
#   Hi Ball,
#   This is to confirm your court booking at CIAC as follows:
#       Ref:        171392,171393
#       Sport:      Tennis
#       Court:      Court 6
#       Date:       9:00 - 10:00 am , Saturday 13th June 2026
#       Player 1:   Elle Marie Meñosa
#       Player 2:   Ball Machine
#
# A booking is for the ball machine when any of Player 1-4 is "Ball Machine".
# The PIN goes to Player 1. Cancellations use the subject
# "Court Cancellation Confirmation" and are excluded by the subject prefix.

BOOKING_SUBJECT_PREFIX = "court booking confirmation"

# "Ball M" is the pre-renaming form of the booking-system account name,
# seen in confirmations up to May 2026.
BALL_MACHINE_PLAYER_PATTERN = re.compile(
    r"^\s*Player\s*[1-4]\s*:\s*Ball\s+M(?:achine)?\s*$", re.I | re.M
)
# No trailing $: real emails use \r\n line endings and $ does not match
# before \r. [^\r\n]* already stops at the end of the line.
PLAYER1_PATTERN = re.compile(r"^\s*Player\s*1\s*:[ \t]*(?P<name>\S[^\r\n]*)", re.I | re.M)
DATE_LINE_PATTERN = re.compile(r"^\s*Date\s*:[ \t]*(?P<period>\S[^\r\n]*)", re.I | re.M)

# "9:00 - 10:00 am , Saturday 13th June 2026" — the am/pm marker may appear
# after either time or only after the end time; the end time may be absent.
PERIOD_PATTERN = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<ap1>am|pm)?"
    r"(?:\s*-\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<ap2>am|pm)?)?"
    r"\s*,\s*\w+\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})",
    re.I,
)


def extract_text(payload: dict) -> str:
    """Extract text from a Gmail API message payload."""
    parts = payload.get("parts", [])
    if not parts:
        body = payload.get("body", {}).get("data")
        return _decode_gmail_body(body) if body else ""

    texts: list[str] = []
    for part in parts:
        mime_type = part.get("mimeType")
        if mime_type == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                texts.append(_decode_gmail_body(data))
        elif "parts" in part:
            texts.append(extract_text(part))
    return "\n".join(t for t in texts if t)


def _decode_gmail_body(data: str) -> str:
    import base64

    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")


def header_value(payload: dict, name: str) -> str:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def is_ball_machine_booking(message: dict) -> bool:
    """True when the email is a booking confirmation with Ball Machine as a player."""
    payload = message.get("payload", {})
    subject = header_value(payload, "Subject").strip().lower()
    if not subject.startswith(BOOKING_SUBJECT_PREFIX):
        return False
    return BALL_MACHINE_PLAYER_PATTERN.search(extract_text(payload)) is not None


def parse_booking(message: dict) -> Optional[Booking]:
    """Extract Player 1 and the booking period. Returns None if Player 1 is missing."""
    payload = message.get("payload", {})
    subject = header_value(payload, "Subject")
    body = extract_text(payload)

    match = PLAYER1_PATTERN.search(body)
    if not match:
        return None
    requester_name = " ".join(match.group("name").split())

    booking_period = None
    booking_start = None
    booking_end = None
    date_match = DATE_LINE_PATTERN.search(body)
    if date_match:
        booking_period = " ".join(date_match.group("period").split())
        start, end = parse_period(booking_period)
        booking_start = start.isoformat() if start else None
        booking_end = end.isoformat() if end else None

    return Booking(
        message_hash=hash_message_id(message["id"]),
        thread_id=message.get("threadId", message["id"]),
        requester_name=requester_name,
        raw_subject=subject,
        message_id_header=header_value(payload, "Message-ID") or None,
        booking_period=booking_period,
        booking_start=booking_start,
        booking_end=booking_end,
    )


def hash_message_id(message_id: str) -> str:
    return sha256(message_id.encode("utf-8")).hexdigest()


def parse_period(period: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Parse '9:00 - 10:00 am , Saturday 13th June 2026' into naive local datetimes.

    Returns (start, end); either may be None when the text is ambiguous
    (e.g. no am/pm marker at all). When only the end time carries the marker,
    the start inherits it unless that would put the start after the end, in
    which case it flips to the other half of the day ('11:30 - 1:00 pm').
    """
    match = PERIOD_PATTERN.search(period)
    if not match:
        return None, None
    try:
        date = datetime.strptime(
            f"{int(match['day'])} {match['month']} {match['year']}", "%d %B %Y"
        ).date()
    except ValueError:
        return None, None

    marker_start = match["ap1"] or match["ap2"]
    if not marker_start:
        return None, None
    start_hour = _to_24h(int(match["h1"]), marker_start)
    start = datetime.combine(date, time(start_hour, int(match["m1"])))

    if match["h2"] is None:
        return start, None

    marker_end = match["ap2"] or match["ap1"]
    end_hour = _to_24h(int(match["h2"]), marker_end)
    end = datetime.combine(date, time(end_hour, int(match["m2"])))
    if start >= end:
        flipped = "am" if marker_start.lower() == "pm" else "pm"
        start = datetime.combine(
            date, time(_to_24h(int(match["h1"]), flipped), int(match["m1"]))
        )
    if start >= end:
        return None, None
    return start, end


def _to_24h(hour: int, marker: str) -> int:
    marker = marker.lower()
    if hour == 12:
        return 0 if marker == "am" else 12
    return hour + 12 if marker == "pm" else hour
