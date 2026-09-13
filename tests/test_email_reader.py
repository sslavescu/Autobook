from email import message_from_bytes, policy

from email_builder import fixture_body, fixture_bytes, load_fixture, make_email

from src.email_reader import html_to_text, normalise_text, parse_raw_email


def test_html_email_reads_as_label_tab_value_lines():
    lines = load_fixture("ball_machine_user_html.eml").text.splitlines()
    # hard-wrapped source ("Ben\nFinnan", "Player\n1") comes out as rendered
    for expected in (
        "Hi Ball,",
        "Court\tCourt 4",
        "Date\t11:30 - 12:30 pm , Sunday 13th September 2026",
        "Player 1\tJohn Smith",
        "Player 2\tBall Machine",
    ):
        assert expected in lines, expected


def test_html_scripts_and_inline_tags_do_not_leak_or_split_words():
    text = load_fixture("ball_machine_user_html.eml").text
    assert "@context" not in text and "startDate" not in text  # JSON-LD script dropped
    assert "eBookingOnline Booking Confirmation" in text  # <span> inside a word


def test_plain_email_reads_as_label_tab_value_lines():
    lines = load_fixture("accessory_plain.eml").text.splitlines()
    for expected in (
        "Hi Jane,",
        "Date:\t10:30 - 11:00 am , Sunday 13th September 2026",
        "Player 1:\tJane Doe",
        "Cost of Booking\t€4.00",
    ):
        assert expected in lines, expected


def test_headers_are_read():
    email = load_fixture("accessory_plain.eml", message_id="m1")
    assert email.id == "m1"
    assert email.subject.startswith("Court Booking Confirmation: 10:30 - 11:00 am")
    assert email.sender == "CIAC <noreply@ebookingonline.net>"
    assert email.message_id_header == "<fixture-1@serverc.ebookingonline.example>"


def test_transfer_encoding_does_not_change_the_text():
    subject, subtype, body = fixture_body("ball_machine_user_html.eml")
    reference = make_email(subject, body, subtype=subtype, cte="8bit").text
    for cte in ("quoted-printable", "base64"):
        assert make_email(subject, body, subtype=subtype, cte=cte).text == reference, cte


def test_plain_text_part_is_preferred_when_both_exist():
    message = message_from_bytes(fixture_bytes("accessory_plain.eml"), policy=policy.default)
    plain = message.get_content()
    message.set_content(plain, charset="utf-8")
    message.add_alternative("<p>Hi Other,</p><p>Player 1 Someone Else</p>", subtype="html")
    email = parse_raw_email(message.as_bytes(), "m", "t")
    assert "Player 1:\tJane Doe" in email.text.splitlines()
    assert "Someone Else" not in email.text


def test_email_without_text_body_reads_as_empty():
    message = message_from_bytes(
        b"Subject: x\nContent-Type: application/pdf\n\n%PDF-1.4", policy=policy.default
    )
    assert parse_raw_email(message.as_bytes(), "m", "t").text == ""


def test_normalise_text_rules():
    raw = "\tPlayer 1:\xa0\xa0\xa0Jane  Doe \r\n\r\n  Date:  \t 9:00 - 10:00 am  \r\n"
    assert normalise_text(raw) == "Player 1:\tJane\tDoe\nDate:\t9:00 - 10:00 am"


def test_html_to_text_entities_and_breaks():
    text = normalise_text(html_to_text("<p>Cost of&nbsp;Booking</p><table><tr><td>A</td><td>&euro;4.00<br>x</td></tr></table>"))
    assert text.splitlines() == ["Cost of Booking", "A\t€4.00", "x"]
