import re
from datetime import datetime, time
from enum import Enum
from hashlib import sha256
from typing import Optional

from .models import Booking, InboundEmail


# Parsing works on InboundEmail.text: the body reduced to canonical text by
# email_reader, one "Label<TAB>value" line per field, whether ebookingonline
# sent plain text or HTML.
#
# This mailbox receives two kinds of Court Booking Confirmation:
#
# * Accessory bookings - the club's ball machine booked as an accessory. These
#   get a PIN. The email greets the member and has a Cost of Booking entry:
#
#     Hi Jane,
#     Date<TAB>10:30 - 11:00 am , Sunday 13th September 2026
#     Player 1<TAB>Jane Doe
#     Cost of Booking<TAB>€4.00
#
# * Ball Machine user bookings - a member bringing their own ball machine books
#   the "Ball Machine" user as a player. These get no PIN. The email greets
#   "Hi Ball" and lists Ball Machine (older account name "Ball M") as a player.
#
# Cancellations use the subject "Court Cancellation Confirmation" and are
# excluded by the subject prefix.

BOOKING_SUBJECT_PREFIX = "court booking confirmation"


class BookingKind(str, Enum):
    NOT_BOOKING_CONFIRMATION = "not_booking_confirmation"
    BALL_MACHINE_USER = "ball_machine_user"
    ACCESSORY_BOOKING = "accessory_booking"
    ACCESSORY_MISSING_COST = "accessory_missing_cost"


# Gaps between a label and its value are horizontal whitespace only, never a
# line break, so a label with no value can't pull in the next line.
_GAP = r"[^\S\n]*"
_PLAYER_LABEL = rf"^{_GAP}Player{_GAP}\d+{_GAP}:?{_GAP}"

GREETING_PATTERN = re.compile(r"^Hi\s+(?P<name>[^,\n]+)", re.I | re.M)
PLAYER_PATTERN = re.compile(_PLAYER_LABEL + r"(?P<name>[^\s:][^\n]*)", re.I | re.M)
BALL_MACHINE_PLAYER_PATTERN = re.compile(
    _PLAYER_LABEL + rf"Ball{_GAP}M(?:achine)?{_GAP}$", re.I | re.M
)
DATE_LINE_PATTERN = re.compile(
    rf"^{_GAP}Date{_GAP}:?{_GAP}(?P<period>[^\s:][^\n]*)", re.I | re.M
)
# "Cost of Booking<TAB>€4.00". The separate "debited by" and "current balance"
# lines are deliberately not matched.
COST_PATTERN = re.compile(
    rf"Cost{_GAP}of{_GAP}Booking{_GAP}:?{_GAP}[€£$]?{_GAP}"
    r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)",
    re.I,
)

# "9:00 - 10:00 am , Saturday 13th June 2026" — the am/pm marker may appear
# after either time or only after the end time; the end time may be absent.
PERIOD_PATTERN = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<ap1>am|pm)?"
    r"(?:\s*-\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<ap2>am|pm)?)?"
    r"\s*,\s*\w+\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})",
    re.I,
)


def classify_booking(email: InboundEmail) -> BookingKind:
    """Decide which kind of confirmation this is, and so whether it gets a PIN."""
    if not email.subject.strip().lower().startswith(BOOKING_SUBJECT_PREFIX):
        return BookingKind.NOT_BOOKING_CONFIRMATION
    if _greets_ball_machine_user(email.text) or BALL_MACHINE_PLAYER_PATTERN.search(
        email.text
    ):
        return BookingKind.BALL_MACHINE_USER
    if extract_cost(email.text) is None:
        return BookingKind.ACCESSORY_MISSING_COST
    return BookingKind.ACCESSORY_BOOKING


def _greets_ball_machine_user(text: str) -> bool:
    greeting = GREETING_PATTERN.search(text)
    return bool(greeting) and re.match(r"Ball\b", greeting.group("name").strip(), re.I) is not None


def parse_booking(email: InboundEmail) -> Optional[Booking]:
    """Extract the booker (first Player line), booking period and cost.

    Returns None if no player line is found. Accessory bookings list a single
    player: the member who booked the machine.
    """
    match = PLAYER_PATTERN.search(email.text)
    if not match:
        return None
    requester_name = " ".join(match.group("name").split())

    booking_period = None
    booking_start = None
    booking_end = None
    date_match = DATE_LINE_PATTERN.search(email.text)
    if date_match:
        booking_period = " ".join(date_match.group("period").split())
        start, end = parse_period(booking_period)
        booking_start = start.isoformat() if start else None
        booking_end = end.isoformat() if end else None

    return Booking(
        message_hash=hash_message_id(email.id),
        thread_id=email.thread_id,
        requester_name=requester_name,
        raw_subject=email.subject,
        message_id_header=email.message_id_header,
        booking_period=booking_period,
        booking_start=booking_start,
        booking_end=booking_end,
        cost=extract_cost(email.text),
    )


def extract_cost(text: str) -> Optional[float]:
    """The booking cost from the confirmation email, or None if not stated."""
    match = COST_PATTERN.search(text)
    if not match:
        return None
    try:
        return float(match.group("amount").replace(",", ""))
    except ValueError:
        return None


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
