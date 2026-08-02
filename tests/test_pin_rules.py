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
    """A ball-machine booking email dated in the future, so the PIN window is valid."""
    import base64
    from datetime import timedelta

    when = datetime.now(TZ) + timedelta(days=days_ahead)
    date_line = f"9:00 - 10:00 am , {when:%A} {when.day} {when:%B} {when.year}"
    body = base64.urlsafe_b64encode(
        f"Date: {date_line}\nPlayer 1: Dave Dennehy\nPlayer 2: Ball Machine\n".encode()
    ).decode().rstrip("=")
    return {
        "id": message_id, "threadId": "t1",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Court Booking Confirmation: x"},
                {"name": "Message-ID", "value": "<x@ebookingonline.net>"},
            ],
            "body": {"data": body},
        },
    }


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


def test_pin_validity_end_is_end_of_booking_month():
    # booking on 13 June -> PIN valid until 1 July midnight (covers all of June)
    end = pin_validity_end(_local(2026, 6, 13, 9), "2099-12-31", TZ)
    assert end.isoformat() == "2026-07-01T00:00:00+01:00"


def test_pin_validity_end_uses_bookings_own_month():
    # a booking in July -> end of July, even if run in June
    end = pin_validity_end(_local(2026, 7, 2, 10), "2099-12-31", TZ)
    assert end.isoformat() == "2026-08-01T00:00:00+01:00"


def test_pin_validity_end_capped_by_renewal():
    end = pin_validity_end(_local(2026, 6, 13, 9), "2026-06-20", TZ)
    assert end.isoformat() == "2026-06-20T00:00:00+01:00"


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

    # 30 days -> daily endpoint, midnight-aligned
    client.create_monthly_algopin(
        "dev", "Member", NOW, NOW + timedelta(days=30), now=NOW
    )
    path, payload = calls[-1]
    assert path.endswith("/algopin/daily")
    assert payload["startDate"] == "2026-06-11T00:00:00+01:00"

    # 8 days (renewal-capped) -> hourly endpoint, hour-aligned
    client.create_monthly_algopin(
        "dev", "Member", NOW, NOW + timedelta(days=8), now=NOW
    )
    path, payload = calls[-1]
    assert path.endswith("/algopin/hourly")
    assert payload["startDate"] == "2026-06-11T13:00:00+01:00"  # 12:00 UTC floored
    assert payload["endDate"] == "2026-06-19T13:00:00+01:00"


def test_algopin_past_start_clamped_to_now(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        client, "_request", lambda m, p, **k: calls.append((p, k["json"])) or {"pin": "1"}
    )

    # Booking start a week in the past, end still well in the future.
    past_start = NOW - timedelta(days=7)
    client.create_monthly_algopin(
        "dev", "Member", past_start, NOW + timedelta(days=30), now=NOW
    )
    _, payload = calls[-1]
    # Start aligns to NOW's day (11 June), not the past start's day (4 June).
    assert payload["startDate"] == "2026-06-11T00:00:00+01:00"
