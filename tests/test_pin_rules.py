import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.db import _create_tables, next_variance
from src.handler import booking_period, pin_validity_end
from src.member_repo import AmbiguousMemberError, MemberRepository
from src.models import Booking, Member

TZ = ZoneInfo("Europe/Dublin")
NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def _local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def _future_booking_message(message_id="m1", days_ahead=30):
    """An accessory booking email dated in the future, so the PIN window is valid."""
    from datetime import timedelta

    from email_builder import make_email

    when = datetime.now(TZ) + timedelta(days=days_ahead)
    date_line = f"9:00 - 10:00 am , {when:%A} {when.day} {when:%B} {when.year}"
    text = (
        f"Hi Dave,\n\nDate: {date_line}\nPlayer 1: Dave Dennehy\n"
        "Cost of Booking\t€4.00\n"
    )
    return make_email("Court Booking Confirmation: x", text, message_id=message_id)


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _add_member(conn, member_id, full_name, dedupe_hash):
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, dedupe_hash)
           VALUES (?, ?, ?, ?)""",
        (member_id, full_name, f"{member_id}@example.com", dedupe_hash),
    )
    conn.commit()


def test_pin_validity_end_is_start_plus_pin_valid_days():
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, "2099-12-31", TZ)
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"
    assert warning is None


def test_pin_validity_end_shortened_when_membership_expires_mid_window():
    # 7 days would reach 20 June, but the membership ends on the 17th
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, "2026-06-17", TZ)
    assert end.isoformat() == "2026-06-17T23:59:00+01:00"
    assert "shortened" in warning and "2026-06-17" in warning


def test_pin_validity_end_not_capped_when_membership_is_later():
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, "2026-06-30", TZ)
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"
    assert warning is None


def test_expired_membership_still_gets_a_full_pin_with_a_warning():
    """The membership never blocks a PIN; the admin is warned instead."""
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, "2026-01-01", TZ)
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"  # full PIN_VALID_DAYS
    assert "expired on 2026-01-01" in warning
    assert "beyond the end of the membership" in warning


def test_missing_membership_expiry_date_warns():
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, None, TZ)
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"
    assert warning == "No membership expiry date found for this member."


def test_unreadable_membership_expiry_date_warns():
    end, warning = pin_validity_end(_local(2026, 6, 13, 9), 7, "31/12/2026", TZ)
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"
    assert "could not be read" in warning


def test_pin_validity_end_membership_check_can_be_disabled():
    # false = ignore the membership entirely: no shortening, no warning
    end, warning = pin_validity_end(
        _local(2026, 6, 13, 9), 7, "2026-01-01", TZ, check_membership_expiry=False
    )
    assert end.isoformat() == "2026-06-20T09:00:00+01:00"
    assert warning is None


def test_booking_period_from_parsed_times():
    booking = Booking(
        message_hash="h", thread_id="t", requester_name="X", raw_subject="s",
        booking_start="2026-06-13T09:00:00", booking_end="2026-06-13T10:00:00",
    )
    start, end = booking_period(booking, NOW, TZ)
    assert start == _local(2026, 6, 13, 9)
    assert end == _local(2026, 6, 13, 10)


def test_booking_period_falls_back_to_now_when_unparsed():
    booking = Booking(
        message_hash="h", thread_id="t", requester_name="X", raw_subject="s",
    )
    start, end = booking_period(booking, NOW, TZ)
    assert start == NOW and end == NOW


def test_padlock_pin_covers_period():
    member = Member(
        member_id="1", full_name="X", email="x@y",
        padlock_pin="123456",
        padlock_pin_valid_from="2026-06-01T00:00:00+01:00",
        padlock_pin_valid_until="2026-07-01T00:00:00+01:00",
    )
    # booking inside the window -> covered
    assert member.padlock_pin_covers(_local(2026, 6, 13, 9), _local(2026, 6, 13, 10))
    # booking after the window -> not covered (new PIN needed)
    assert not member.padlock_pin_covers(_local(2026, 7, 2, 9), _local(2026, 7, 2, 10))
    # booking before the PIN starts -> not covered
    assert not member.padlock_pin_covers(_local(2026, 5, 30, 9), _local(2026, 5, 30, 10))


def test_find_by_name_duplicate_names_distinct_people():
    conn = _in_memory_db()
    _add_member(conn, "1", "John Murphy", "hash-a")
    _add_member(conn, "2", "John Murphy", "hash-b")
    with pytest.raises(AmbiguousMemberError):
        MemberRepository(conn).find_by_name("John Murphy", 90)


def test_find_by_name_duplicate_rows_same_person():
    conn = _in_memory_db()
    _add_member(conn, "1", "John Murphy", "hash-a")
    _add_member(conn, "2", "John Murphy", "hash-a")
    member = MemberRepository(conn).find_by_name("John Murphy", 90)
    assert member.full_name == "John Murphy"


def test_dry_run_does_not_persist_placeholder_pin():
    """A stored DRY-RUN-PIN would be reused by later real runs."""
    from types import SimpleNamespace

    from src.handler import process_message
    from src.member_repo import MemberRepository

    conn = _in_memory_db()
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, membership_expires_on,
                                dedupe_hash)
           VALUES ('1', 'Dave Dennehy', 'dave@example.com', '2099-12-31', 'h')"""
    )
    conn.commit()

    cfg = SimpleNamespace(
        admin_email="admin@x", fuzzy_name_threshold=90, lock_id="DEV1",
        club_timezone="Europe/Dublin", dry_run=True,
        pin_valid_days=7, check_membership_expiry=True,
    )
    gmail = SimpleNamespace(send_email=lambda **kw: None, mark_read=lambda *a, **k: None)

    def igloo_must_not_be_called(**kwargs):
        raise AssertionError("dry run must not call the igloohome API")

    igloo = SimpleNamespace(create_monthly_algopin=igloo_must_not_be_called)

    msg = _future_booking_message()
    status, _, _ = process_message(cfg, gmail, igloo, MemberRepository(conn), msg, conn)
    assert status == "sent_pin"
    stored = conn.execute("SELECT padlock_pin FROM members WHERE member_id='1'").fetchone()
    assert stored["padlock_pin"] is None


def test_stored_dry_run_pin_is_replaced_on_real_run():
    """A DRY-RUN-PIN left in the database must not be emailed to a member."""
    from datetime import timedelta
    from types import SimpleNamespace

    from src.handler import DRY_RUN_PIN, process_message
    from src.member_repo import MemberRepository

    # Placeholder stored with a window wide enough to cover the booking, so only
    # the placeholder check can stop it being reused.
    conn = _in_memory_db()
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, membership_expires_on,
                                dedupe_hash, padlock_pin,
                                padlock_pin_valid_from, padlock_pin_valid_until)
           VALUES ('1', 'Dave Dennehy', 'dave@example.com', '2099-12-31', 'h', ?, ?, ?)""",
        (
            DRY_RUN_PIN,
            (datetime.now(TZ) - timedelta(days=1)).isoformat(),
            (datetime.now(TZ) + timedelta(days=365)).isoformat(),
        ),
    )
    conn.commit()

    cfg = SimpleNamespace(
        admin_email="admin@x", fuzzy_name_threshold=90, lock_id="DEV1",
        club_timezone="Europe/Dublin", dry_run=False,
        pin_valid_days=7, check_membership_expiry=True,
    )
    sent = []
    gmail = SimpleNamespace(
        send_email=lambda **kw: sent.append(kw), mark_read=lambda *a, **k: None
    )
    igloo = SimpleNamespace(
        create_monthly_algopin=lambda **kw: SimpleNamespace(
            code="987654321",
            valid_from=kw["valid_from"],
            valid_until=kw["valid_until"],
        )
    )

    msg = _future_booking_message()
    status, _, _ = process_message(cfg, gmail, igloo, MemberRepository(conn), msg, conn)
    assert status == "sent_pin"
    stored = conn.execute("SELECT padlock_pin FROM members WHERE member_id='1'").fetchone()
    assert stored["padlock_pin"] == "987654321"
    assert DRY_RUN_PIN not in sent[-1]["body"]
    assert "987654321" in sent[-1]["body"]


def test_next_variance_cycles():
    conn = _in_memory_db()
    assert [next_variance(conn) for _ in range(5)] == [1, 2, 3, 1, 2]


def test_algopin_endpoint_selection(monkeypatch, tmp_path):
    import json
    from datetime import timedelta

    from src.igloohome_client import IgloohomeClient

    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"client_id": "id", "client_secret": "secret"}))
    client = IgloohomeClient(
        base_url="http://unused", credentials_path=str(creds),
        timezone_name="Europe/Dublin",
    )
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((path, kwargs["json"]))
        return {"pin": "1234567", "pinId": "X"}

    monkeypatch.setattr(client, "_request", fake_request)

    # PIN_VALID_DAYS is capped at 11, so real runs always take this path.
    client.create_monthly_algopin("dev", "Member", NOW, NOW + timedelta(days=7))
    path, payload = calls[-1]
    assert path.endswith("/algopin/hourly")
    assert payload["startDate"] == "2026-06-11T13:00:00+01:00"  # 12:00 UTC
    assert payload["endDate"] == "2026-06-18T13:00:00+01:00"

    # the daily endpoint is still selected for 29+ day windows
    client.create_monthly_algopin("dev", "Member", NOW, NOW + timedelta(days=30))
    path, payload = calls[-1]
    assert path.endswith("/algopin/daily")
    assert payload["startDate"] == "2026-06-11T13:00:00+01:00"
    assert payload["endDate"] == "2026-07-11T13:00:00+01:00"


def test_pin_start_is_the_hour_before_the_booking():
    from src.igloohome_client import pin_start_for_booking

    well_before = _local(2026, 8, 20)  # now, comfortably before the booking

    # on the half hour -> top of that hour
    assert pin_start_for_booking(
        _local(2026, 8, 22, 21, 30), well_before, TZ
    ) == _local(2026, 8, 22, 21, 0)

    # exactly on the hour -> a full hour earlier
    assert pin_start_for_booking(
        _local(2026, 8, 22, 21, 0), well_before, TZ
    ) == _local(2026, 8, 22, 20, 0)

    assert pin_start_for_booking(
        _local(2026, 8, 22, 6, 45), well_before, TZ
    ) == _local(2026, 8, 22, 6, 0)


def test_pin_start_never_precedes_now():
    from src.igloohome_client import pin_start_for_booking

    booking = _local(2026, 8, 22, 21, 0)
    # now is inside the hour before the booking, so 20:00 has passed:
    # fall back to the booking's own hour rather than starting in the past
    assert pin_start_for_booking(
        booking, _local(2026, 8, 22, 20, 30), TZ
    ) == _local(2026, 8, 22, 21, 0)


def _fake_gmail():
    from types import SimpleNamespace

    sent = []
    return SimpleNamespace(send_email=lambda **kw: sent.append(kw)), sent


def _igloo_must_not_be_called():
    from types import SimpleNamespace

    def fail(**kwargs):
        raise AssertionError("no PIN may be generated for this email")

    return SimpleNamespace(create_monthly_algopin=fail)


def test_parse_failure_alert_contains_message_text_not_hash():
    from types import SimpleNamespace

    from email_builder import make_email

    from src.booking_parser import hash_message_id
    from src.handler import process_message

    email = make_email(
        "Court Booking Confirmation: 9:00",
        "Hi Dave,\r\n\r\nDate: 9:00 - 10:00 am , Sunday 13th September 2026\r\n"
        "Cost of Booking\t€4.00\r\n",
        message_id="m-parse-fail",
    )
    gmail, sent = _fake_gmail()
    cfg = SimpleNamespace(admin_email="admin@x")

    status, _, _ = process_message(cfg, gmail, _igloo_must_not_be_called(), None, email)

    assert status == "manual_review_parse_failed"
    alert = sent[-1]["body"]
    assert "Subject: Court Booking Confirmation: 9:00" in alert
    assert "From: CIAC <noreply@ebookingonline.net>" in alert
    assert "Hi Dave," in alert and "Date: 9:00 - 10:00 am" in alert
    assert hash_message_id("m-parse-fail") not in alert


def test_ball_machine_user_booking_is_logged_only(caplog):
    import logging
    from types import SimpleNamespace

    from email_builder import load_fixture

    from src.handler import process_message

    gmail, sent = _fake_gmail()
    cfg = SimpleNamespace(admin_email="admin@x")
    with caplog.at_level(logging.INFO, logger="src.handler"):
        status, booking, member = process_message(
            cfg, gmail, _igloo_must_not_be_called(), None,
            load_fixture("ball_machine_user_html.eml"),
        )

    assert status == "skipped_ball_machine_user"
    assert sent == []  # no admin or member email
    assert "Ball Machine user found, not an accessory booking" in caplog.text


def test_accessory_booking_without_cost_is_flagged_to_admin(caplog):
    import logging
    from types import SimpleNamespace

    from email_builder import fixture_body, make_email

    from src.handler import process_message

    subject, _, body = fixture_body("accessory_plain.eml")
    body = "\n".join(line for line in body.splitlines() if "Cost of Booking" not in line)
    gmail, sent = _fake_gmail()
    cfg = SimpleNamespace(admin_email="admin@x")

    with caplog.at_level(logging.INFO, logger="src.handler"):
        status, booking, member = process_message(
            cfg, gmail, _igloo_must_not_be_called(), None, make_email(subject, body)
        )

    assert status == "flagged_missing_cost"
    assert booking.requester_name == "Jane Doe" and member is None
    assert [m["to"] for m in sent] == ["admin@x"]  # admin only, never the member
    assert "no Cost of Booking" in sent[0]["subject"]
    assert "Player 1:\tJane Doe" in sent[0]["body"]
    assert "no Cost of Booking entry; flagged as wrong" in caplog.text


def test_message_as_text_flags_empty_body():
    from src.handler import message_as_text
    from src.models import InboundEmail

    email = InboundEmail(
        id="m", thread_id="t", subject="x", sender="s", date="d",
        message_id_header=None, text="",
    )
    assert "no readable text" in message_as_text(email)


def _process_booking_for_member(membership_expires_on, caplog=None):
    """Run a real accessory booking for a member with the given expiry date."""
    import logging
    from types import SimpleNamespace

    from src.handler import process_message
    from src.member_repo import MemberRepository

    conn = _in_memory_db()
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, membership_expires_on,
                                dedupe_hash)
           VALUES ('1', 'Dave Dennehy', 'dave@example.com', ?, 'h')""",
        (membership_expires_on,),
    )
    conn.commit()
    cfg = SimpleNamespace(
        admin_email="admin@x", fuzzy_name_threshold=90, lock_id="DEV1",
        club_timezone="Europe/Dublin", dry_run=False,
        pin_valid_days=7, check_membership_expiry=True,
    )
    sent = []
    gmail = SimpleNamespace(
        send_email=lambda **kw: sent.append(kw), mark_read=lambda *a, **k: None
    )
    igloo = SimpleNamespace(
        create_monthly_algopin=lambda **kw: SimpleNamespace(
            code="987654321", valid_from=kw["valid_from"], valid_until=kw["valid_until"]
        )
    )
    context = caplog.at_level(logging.INFO, logger="src.handler") if caplog else None
    if context:
        with context:
            result = process_message(
                cfg, gmail, igloo, MemberRepository(conn), _future_booking_message(), conn
            )
    else:
        result = process_message(
            cfg, gmail, igloo, MemberRepository(conn), _future_booking_message(), conn
        )
    return result[0], sent


def test_expired_membership_still_sends_the_pin_and_warns_the_admin(caplog):
    status, sent = _process_booking_for_member("2020-01-01", caplog)

    assert status == "sent_pin_membership_warning"
    # the member is served first and exactly as usual
    member_email, admin_email = sent
    assert member_email["to"] == "dave@example.com"
    assert "987654321" in member_email["body"]
    # the admin is warned, without the PIN in the warning
    assert admin_email["to"] == "admin@x"
    assert "membership warning" in admin_email["subject"]
    assert "expired on 2020-01-01" in admin_email["body"]
    assert "987654321" not in admin_email["body"]
    assert "membership warning for Dave Dennehy" in caplog.text


def test_missing_membership_expiry_date_still_sends_the_pin_and_warns():
    status, sent = _process_booking_for_member(None)
    assert status == "sent_pin_membership_warning"
    assert [m["to"] for m in sent] == ["dave@example.com", "admin@x"]
    assert "No membership expiry date found" in sent[1]["body"]


def test_valid_membership_sends_the_pin_with_no_warning():
    status, sent = _process_booking_for_member("2099-12-31")
    assert status == "sent_pin"
    assert [m["to"] for m in sent] == ["dave@example.com"]


def test_stored_pin_membership_warning_cases():
    from src.handler import stored_pin_membership_warning

    window = (_local(2026, 6, 13, 9), _local(2026, 6, 20, 9))

    # membership outlasts the reused PIN -> nothing to say
    assert stored_pin_membership_warning(*window, "2026-07-31", TZ) is None
    # membership ends inside the window: the PIN can't be shortened now
    assert "before the reused PIN stops working" in stored_pin_membership_warning(
        *window, "2026-06-17", TZ
    )
    # lapsed since the PIN was issued
    assert "runs beyond the end of the membership" in stored_pin_membership_warning(
        *window, "2026-01-01", TZ
    )
    assert stored_pin_membership_warning(*window, None, TZ).startswith("No membership")
    # the flag still switches the whole check off
    assert stored_pin_membership_warning(
        *window, "2026-01-01", TZ, check_membership_expiry=False
    ) is None


def test_reused_pin_for_lapsed_member_warns_without_calling_igloohome():
    from datetime import timedelta
    from types import SimpleNamespace

    from src.handler import process_message
    from src.member_repo import MemberRepository

    # a PIN issued while the membership was valid, still covering the booking
    conn = _in_memory_db()
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, membership_expires_on,
                                dedupe_hash, padlock_pin,
                                padlock_pin_valid_from, padlock_pin_valid_until)
           VALUES ('1', 'Dave Dennehy', 'dave@example.com', '2026-01-01', 'h',
                   '111222333', ?, ?)""",
        (
            (datetime.now(TZ) - timedelta(days=1)).isoformat(),
            (datetime.now(TZ) + timedelta(days=365)).isoformat(),
        ),
    )
    conn.commit()
    cfg = SimpleNamespace(
        admin_email="admin@x", fuzzy_name_threshold=90, lock_id="DEV1",
        club_timezone="Europe/Dublin", dry_run=False,
        pin_valid_days=7, check_membership_expiry=True,
    )
    sent = []
    gmail = SimpleNamespace(
        send_email=lambda **kw: sent.append(kw), mark_read=lambda *a, **k: None
    )

    status, _, _ = process_message(
        cfg, gmail, _igloo_must_not_be_called(), MemberRepository(conn),
        _future_booking_message(), conn,
    )

    assert status == "sent_pin_membership_warning"
    assert [m["to"] for m in sent] == ["dave@example.com", "admin@x"]
    assert "111222333" in sent[0]["body"]  # the member still gets the stored PIN
    assert "expired on 2026-01-01" in sent[1]["body"]
