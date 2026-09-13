from hashlib import sha256

from email_builder import fixture_body, load_fixture, make_email

from src.booking_parser import BookingKind, classify_booking, parse_booking, parse_period
from src.handler import reply_subject

BOOKING_BODY = (
    "Hi Jane,\n\n"
    "This is to confirm your court booking at CIAC as follows:\n\n"
    "\t\tRef: \t\t\t\t\t181973\n\n"
    "\t\tSport: \t\t\t\t\tTennis\n\n"
    "\t\tCourt: \t\t\t\t\tCourt 5\n\n"
    "\tDate: \t\t\t\t\t10:30 - 11:00 am , Sunday 13th September 2026\n\n"
    "\tPlayer 1:\t\t\t\tJane Doe\n\n"
    "\tCost of Booking\t\t\t€4.00\n\n"
    "\tYour account has been debited by: \t€4.00\n\n"
    "\tYour current balance is:\t\t\t€14.93\n"
)

SUBJECT = "Court Booking Confirmation: 10:30 - 11:00 am , Sunday 13th September 2026"


# --- classification of real emails (anonymised fixtures) ----------------------


def test_real_accessory_booking_plain_text():
    email = load_fixture("accessory_plain.eml")
    assert classify_booking(email) is BookingKind.ACCESSORY_BOOKING
    booking = parse_booking(email)
    assert booking.requester_name == "Jane Doe"
    assert booking.booking_start == "2026-09-13T10:30:00"
    assert booking.booking_end == "2026-09-13T11:00:00"
    assert booking.cost == 4.0


def test_real_ball_machine_user_bookings_html():
    for name in (
        "ball_machine_user_html.eml",
        "ball_machine_user_html_three_players.eml",
        "ball_machine_user_html_no_end_time.eml",
    ):
        assert classify_booking(load_fixture(name)) is BookingKind.BALL_MACHINE_USER, name


def test_real_html_booking_fields_are_readable():
    # Even though no PIN is issued, the HTML reads cleanly into the same fields.
    booking = parse_booking(load_fixture("ball_machine_user_html.eml"))
    assert booking.requester_name == "John Smith"
    assert booking.booking_start == "2026-09-13T11:30:00"
    assert booking.booking_end == "2026-09-13T12:30:00"


def test_accessory_booking_sent_as_html_is_recognised():
    """Guards against ebookingonline switching accessory emails to HTML.

    Built from the real HTML template: greeting changed to the member, the Ball
    Machine player row removed and a Cost of Booking row added.
    """
    subject, _, html = fixture_body("ball_machine_user_html.eml")
    html = html.replace("\r\n", "\n").replace("<p>Hi Ball,</p>", "<p>Hi John,</p>")
    start = html.index("Player\n2")
    row_start = html.rfind("<tr>", 0, start)
    row_end = html.index("</tr>", start) + len("</tr>")
    cost_row = "<tr><td>Cost of\nBooking</td><td>&euro;4.00</td></tr>"
    html = html[:row_start] + cost_row + html[row_end:]

    email = make_email(subject, html, subtype="html")
    assert classify_booking(email) is BookingKind.ACCESSORY_BOOKING
    booking = parse_booking(email)
    assert booking.requester_name == "John Smith"
    assert booking.booking_start == "2026-09-13T11:30:00"
    assert booking.cost == 4.0


def test_accessory_booking_without_cost_is_flagged():
    subject, _, body = fixture_body("accessory_plain.eml")
    body = "\n".join(line for line in body.splitlines() if "Cost of Booking" not in line)
    email = make_email(subject, body)
    assert classify_booking(email) is BookingKind.ACCESSORY_MISSING_COST
    # still parseable, so the flagged record keeps the member and period
    assert parse_booking(email).requester_name == "Jane Doe"


# --- classification rules -----------------------------------------------------


def test_hi_ball_greeting_alone_marks_ball_machine_user():
    body = BOOKING_BODY.replace("Hi Jane,", "Hi Ball,")
    assert classify_booking(make_email(SUBJECT, body)) is BookingKind.BALL_MACHINE_USER


def test_ball_machine_player_alone_marks_ball_machine_user():
    body = BOOKING_BODY + "\tPlayer 3:\t\t\t\tBall M\n"
    assert classify_booking(make_email(SUBJECT, body)) is BookingKind.BALL_MACHINE_USER


def test_ball_machine_player_detected_without_colon_or_with_nbsp():
    for line in ("\tPlayer 2\t\tBall Machine\n", "\tPlayer 2:\xa0Ball M\n"):
        email = make_email(SUBJECT, BOOKING_BODY + line)
        assert classify_booking(email) is BookingKind.BALL_MACHINE_USER, repr(line)


def test_member_named_ball_is_not_mistaken_for_ball_machine_user():
    body = BOOKING_BODY.replace("Hi Jane,", "Hi Ballantine,")
    assert classify_booking(make_email(SUBJECT, body)) is BookingKind.ACCESSORY_BOOKING


def test_cancellation_is_not_a_booking_confirmation():
    email = make_email(
        "Court Cancellation Confirmation",
        "Hi Jane,\n\nThis is to confirm that the following court booking at CIAC "
        "has been\nCANCELLED:\n\nPlayer 1: \tJane Doe\n",
    )
    assert classify_booking(email) is BookingKind.NOT_BOOKING_CONFIRMATION


# --- field parsing ------------------------------------------------------------


def test_parse_accessory_booking_confirmation():
    email = make_email(SUBJECT, BOOKING_BODY)
    assert classify_booking(email) is BookingKind.ACCESSORY_BOOKING
    booking = parse_booking(email)
    assert booking.requester_name == "Jane Doe"
    assert booking.message_hash == sha256(b"abc").hexdigest()
    assert booking.message_id_header == "<orig@serverc.ebookingonline.net>"
    assert booking.booking_period == "10:30 - 11:00 am , Sunday 13th September 2026"
    assert booking.booking_start == "2026-09-13T10:30:00"
    assert booking.booking_end == "2026-09-13T11:00:00"


def test_player_name_tolerates_separator_variations():
    player_line = "\tPlayer 1:\t\t\t\tJane Doe\n"
    for variant in (
        "\tPlayer 1\t\t\t\tJane Doe\n",  # no colon
        "\tPlayer 1:\xa0\xa0\xa0Jane Doe\n",  # non-breaking spaces
        "\tPlayer 1 :\tJane Doe\n",  # space before colon
        "Player1 Jane Doe\n",  # no space, no colon
    ):
        email = make_email(SUBJECT, BOOKING_BODY.replace(player_line, variant))
        assert parse_booking(email).requester_name == "Jane Doe", repr(variant)


def test_player_label_without_name_does_not_read_next_line():
    body = BOOKING_BODY.replace("\tPlayer 1:\t\t\t\tJane Doe\n", "\tPlayer 1:\t\t\n")
    assert parse_booking(make_email(SUBJECT, body)) is None


def test_date_line_tolerates_missing_colon():
    body = BOOKING_BODY.replace("\tDate: \t", "\tDate\t")
    assert parse_booking(make_email(SUBJECT, body)).booking_start == "2026-09-13T10:30:00"


def test_parse_booking_captures_cost():
    assert parse_booking(make_email(SUBJECT, BOOKING_BODY)).cost == 4.0


def test_extract_cost():
    from src.booking_parser import extract_cost

    # accessory format: no colon after the label
    assert extract_cost("\tCost of Booking\t\t\t€4.00") == 4.0
    assert extract_cost("Cost of Booking: €5.50") == 5.5
    assert extract_cost("Cost of Booking €0.00") == 0.0
    assert extract_cost("Cost of Booking €1,234.50") == 1234.5
    # the debit and balance lines must not be read as the booking cost
    assert extract_cost("Your account has been debited by: \t€4.00") is None
    assert extract_cost("Your current balance is:\t\t\t€14.93") is None
    assert extract_cost("no cost line here") is None
    # a label with no amount on its line is not a cost entry
    assert extract_cost("Cost of Booking\nYour balance: €4.00") is None


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
    email = make_email(SUBJECT, BOOKING_BODY.replace("\n", "\r\n"))
    assert classify_booking(email) is BookingKind.ACCESSORY_BOOKING
    booking = parse_booking(email)
    assert booking.requester_name == "Jane Doe"
    assert booking.booking_start == "2026-09-13T10:30:00"


def test_parse_period_no_marker_is_ambiguous():
    start, end = parse_period("6:00 , Saturday 6th June 2026")
    assert start is None
    assert end is None


def test_align_to_hours_floors_start_to_the_hour():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.igloohome_client import align_to_hours

    tz = ZoneInfo("Europe/Dublin")

    # 21:30 booking -> PIN starts 21:00 on the same date
    start, end = align_to_hours(
        datetime(2026, 8, 22, 21, 30, tzinfo=tz),
        datetime(2026, 9, 1, 0, 0, tzinfo=tz),
        tz,
    )
    assert start.isoformat() == "2026-08-22T21:00:00+01:00"
    assert end.isoformat() == "2026-09-01T00:00:00+01:00"

    # 21:00 booking -> PIN starts 21:00 (already on the hour, not moved back)
    start, _ = align_to_hours(
        datetime(2026, 8, 22, 21, 0, tzinfo=tz),
        datetime(2026, 9, 1, 0, 0, tzinfo=tz),
        tz,
    )
    assert start.isoformat() == "2026-08-22T21:00:00+01:00"

    # a mid-hour end is ceiled so the booking stays covered
    _, end = align_to_hours(
        datetime(2026, 8, 22, 21, 0, tzinfo=tz),
        datetime(2026, 8, 22, 22, 30, tzinfo=tz),
        tz,
    )
    assert end.isoformat() == "2026-08-22T23:00:00+01:00"


def test_member_pin_email_renders_template():
    from datetime import datetime

    from src.handler import member_pin_email

    body = member_pin_email("Dave Dennehy", "1928374", datetime(2026, 7, 12, 0, 0))
    assert "Hi Dave," in body
    assert "1928374" in body
    # {expiry} is optional in the template; render must not leave placeholders
    assert "{" not in body


def test_reply_subject():
    assert reply_subject("Ball machine booking") == "Re: Ball machine booking"
    assert reply_subject("RE: Ball machine booking") == "RE: Ball machine booking"
    assert reply_subject("") == "Re: Ball machine booking"
