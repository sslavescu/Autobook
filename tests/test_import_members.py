import sqlite3

from scripts.import_members import item_from_row, save_member
from src.db import _create_tables


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def test_item_from_exported_members_csv_row():
    item = item_from_row(
        {
            "User ID": "1001",
            "First Name": "Ralph",
            "Last Name": "McMahon",
            "PIN": "1001",
            "Email Address": "ralph@example.com",
            "Active": "1",
            "Renewal Date": "31/12/2099",
        }
    )

    dedupe_hash = item.pop("dedupe_hash")
    assert len(dedupe_hash) == 64
    assert item == {
        "member_id": "1001",
        "full_name": "Ralph McMahon",
        "email": "ralph@example.com",
        "status": "active",
        "pin": "1001",
        "membership_expires_on": "2099-12-31",
    }


def test_item_from_row_without_email_raises_concise_error():
    import pytest

    with pytest.raises(ValueError) as exc_info:
        item_from_row(
            {
                "User ID": "1207",
                "First Name": "M",
                "Last Name": "Kehoe",
                "Password": "$2y$10$secret-hash",
                "PIN": "1207",
                "Email Address": "",
                "Active": "1",
            }
        )
    assert "missing email for member 1207" in str(exc_info.value)
    # the error must not leak other row fields such as the password hash
    assert "secret-hash" not in str(exc_info.value)


def test_save_member_inserts_new_member_with_all_fields():
    conn = _in_memory_db()
    save_member(
        conn,
        {
            "member_id": "1001",
            "full_name": "Ralph McMahon",
            "email": "ralph@example.com",
            "status": "active",
            "pin": "1001",
            "membership_expires_on": "2099-12-31",
            "dedupe_hash": "abc123",
        },
    )
    row = conn.execute("SELECT * FROM members WHERE member_id = '1001'").fetchone()
    assert row["full_name"] == "Ralph McMahon"
    assert row["padlock_pin"] is None
    # regression: new members must keep optional fields, not just the
    # four columns in the INSERT
    assert row["pin"] == "1001"
    assert row["membership_expires_on"] == "2099-12-31"
    assert row["dedupe_hash"] == "abc123"


def test_save_member_updates_without_overwriting_padlock_pin():
    conn = _in_memory_db()
    conn.execute(
        """INSERT INTO members (member_id, full_name, email, status, padlock_pin)
           VALUES ('1001', 'Ralph McMahon', 'old@example.com', 'active', '9999')"""
    )
    conn.commit()

    save_member(
        conn,
        {
            "member_id": "1001",
            "full_name": "Ralph McMahon",
            "email": "ralph@example.com",
            "status": "active",
            "pin": "1001",
        },
    )
    row = conn.execute("SELECT * FROM members WHERE member_id = '1001'").fetchone()
    assert row["email"] == "ralph@example.com"
    assert row["padlock_pin"] == "9999"
