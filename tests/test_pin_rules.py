import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.db import _create_tables, next_variance
from src.handler import pin_validity_end
from src.member_repo import AmbiguousMemberError, MemberRepository

TZ = ZoneInfo("Europe/Dublin")
NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _add_member(conn, member_id, full_name, dedupe_hash):
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, status, dedupe_hash)
           VALUES (?, ?, ?, 'active', ?)""",
        (member_id, full_name, f"{member_id}@example.com", dedupe_hash),
    )
    conn.commit()


def test_pin_validity_end_uncapped():
    end = pin_validity_end(NOW, 31, "2099-12-31", TZ)
    assert end == NOW.replace(month=7, day=12)


def test_pin_validity_end_capped_by_renewal():
    end = pin_validity_end(NOW, 31, "2026-07-10", TZ)
    assert end.isoformat() == "2026-07-10T00:00:00+01:00"


def test_pin_validity_end_renewal_soon_gives_short_pin():
    end = pin_validity_end(NOW, 31, "2026-06-20", TZ)
    assert end.isoformat() == "2026-06-20T00:00:00+01:00"


def test_pin_validity_end_lapsed_membership():
    assert pin_validity_end(NOW, 31, "2026-01-01", TZ) is None


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
    client.create_monthly_algopin("dev", "Member", NOW, NOW + timedelta(days=30))
    path, payload = calls[-1]
    assert path.endswith("/algopin/daily")
    assert payload["startDate"] == "2026-06-11T00:00:00+01:00"

    # 8 days (renewal-capped) -> hourly endpoint, hour-aligned
    client.create_monthly_algopin("dev", "Member", NOW, NOW + timedelta(days=8))
    path, payload = calls[-1]
    assert path.endswith("/algopin/hourly")
    assert payload["startDate"] == "2026-06-11T13:00:00+01:00"  # 12:00 UTC floored
    assert payload["endDate"] == "2026-06-19T13:00:00+01:00"
