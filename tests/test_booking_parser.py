import base64
from hashlib import sha256

from src.booking_parser import is_ball_machine_booking, parse_booking, parse_period
from src.handler import reply_subject


def gmail_body(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_message(subject, body, message_id="abc"):
    return {
        "id": message_id,
        "threadId": "thr",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<orig@serverc.ebookingonline.net>"},
            ],
            "body": {"data": gmail_body(body)},
        },
    }


BOOKING_BODY = (
    "Hi Ball,\n\n"
    "This is to confirm your court booking at CIAC as follows:\n\n"
    "\t\tRef: \t\t\t\t\t171392,171393\n\n"
    "\t\tSport: \t\t\t\t\tTennis\n\n"
    "\t\tCourt: \t\t\t\t\tCourt 6\n\n"
    "\tDate: \t\t\t\t\t9:00 - 10:00 am , Saturday 13th June 2026\n\n"
    "\tPlayer 1:\t\t\t\tElle Marie Meñosa\n"
    "\tPlayer 2:\t\t\t\tBall Machine\n\n"
    "\tYour account has been debited by: \t€0.00\n"
)


def test_parse_real_booking_confirmation():
    msg = make_message(
        "Court Booking Confirmation: 9:00 - 10:00 am , Saturday 13th June 2026",
        BOOKING_BODY,
    )
    assert is_ball_machine_booking(msg)
    booking = parse_booking(msg)
    assert booking.requester_name == "Elle Marie Meñosa"
    assert booking.message_hash == sha256(b"abc").hexdigest()
    assert booking.message_id_header == "<orig@serverc.ebookingonline.net>"
    assert booking.booking_period == "9:00 - 10:00 am , Saturday 13th June 2026"
    assert booking.booking_start == "2026-06-13T09:00:00"
    assert booking.booking_end == "2026-06-13T10:00:00"


def test_cancellation_is_not_ball_machine_booking():
    msg = make_message(
        "Court Cancellation Confirmation",
        "This is to confirm that the following court booking at CIAC has been\n"
        "CANCELLED:\n\nPlayer 1: \tBen Finnan\nPlayer 2: \tBall Machine\n",
    )
    assert not is_ball_machine_booking(msg)


def test_booking_without_ball_machine_player_is_skipped():
    msg = make_message(
        "Court Booking Confirmation: 9:00 - 10:00 am , Saturday 13th June 2026",
        BOOKING_BODY.replace("Ball Machine", "Joe Bloggs"),
    )
    assert not is_ball_machine_booking(msg)


def test_ball_machine_as_player_3_or_4():
    body = BOOKING_BODY.replace("Player 2:\t\t\t\tBall Machine", "Player 2:\t\t\t\tJoe Bloggs")
    body += "\tPlayer 3:\t\t\t\tBall Machine\n"
    msg = make_message("Court Booking Confirmation: foo", body)
    assert is_ball_machine_booking(msg)


def test_parse_period_evening():
    start, end = parse_period("8:30 - 10:00 pm , Tuesday 9th June 2026")
    assert start.isoformat() == "2026-06-09T20:30:00"
    assert end.isoformat() == "2026-06-09T22:00:00"


def test_parse_period_crossing_noon():
    start, end = parse_period("11:30 - 1:00 pm , Friday 12th June 2026")
    assert start.isoformat() == "2026-06-12T11:30:00"
    assert end.isoformat() == "2026-06-12T13:00:00"


def test_parse_period_noon_boundary():
    start, end = parse_period("11:00 - 12:00 pm , Friday 12th June 2026")
    assert start.isoformat() == "2026-06-12T11:00:00"
    assert end.isoformat() == "2026-06-12T12:00:00"


def test_parse_period_single_time():
    start, end = parse_period("9:30 pm, Tuesday 9th June 2026")
    assert start.isoformat() == "2026-06-09T21:30:00"
    assert end is None


def test_parse_booking_with_crlf_line_endings():
    msg = make_message(
        "Court Booking Confirmation: 9:00 - 10:00 am , Saturday 13th June 2026",
        BOOKING_BODY.replace("\n", "\r\n"),
    )
    assert is_ball_machine_booking(msg)
    booking = parse_booking(msg)
    assert booking.requester_name == "Elle Marie Meñosa"
    assert booking.booking_start == "2026-06-13T09:00:00"


def test_parse_period_no_marker_is_ambiguous():
    start, end = parse_period("6:00 , Saturday 6th June 2026")
    assert start is None
    assert end is None


def test_align_to_days():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from src.igloohome_client import align_to_days

    tz = ZoneInfo("Europe/Dublin")
    # 11 June 14:30 UTC = 15:30 Dublin; +31 days ends mid-day so it rounds up
    start, end = align_to_days(
        datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc),
        tz,
    )
    assert start.isoformat() == "2026-06-11T00:00:00+01:00"
    assert end.isoformat() == "2026-07-13T00:00:00+01:00"

    # an end already at local midnight is not rounded further
    _, end = align_to_days(
        datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 11, 23, 0, tzinfo=timezone.utc),  # 00:00 Dublin on 12th
        tz,
    )
    assert end.isoformat() == "2026-07-12T00:00:00+01:00"


def test_member_pin_email_renders_template():
    from datetime import datetime

    from src.handler import member_pin_email

    body = member_pin_email("Dave Dennehy", "1928374", datetime(2026, 7, 12, 0, 0))
    assert "Hi Dave," in body
    assert "1928374" in body
    assert "11 July 2026" in body  # exclusive midnight boundary -> last valid day


def test_reply_subject():
    assert reply_subject("Ball machine booking") == "Re: Ball machine booking"
    assert reply_subject("RE: Ball machine booking") == "RE: Ball machine booking"
    assert reply_subject("") == "Re: Ball machine booking"
