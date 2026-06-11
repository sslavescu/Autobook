#!/usr/bin/env python3
import argparse
import csv
from datetime import datetime
from hashlib import sha256
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "y", "active"}

# The booking-system account for the machine itself must never become a
# member record, or PINs could be issued to it.
BALL_MACHINE_NAMES = {"ball machine", "ball m"}


def value(row: dict, *field_names: str) -> str:
    for field_name in field_names:
        if field_name in row and row[field_name] is not None:
            return row[field_name].strip()
    return ""


def status_from_row(row: dict) -> str:
    status = value(row, "status")
    if status:
        return status
    active = value(row, "Active")
    return "active" if active.lower() in TRUE_VALUES else "inactive"


def full_name_from_row(row: dict) -> str:
    full_name = value(row, "full_name")
    if full_name:
        return full_name
    return " ".join(
        part for part in [value(row, "First Name"), value(row, "Last Name")] if part
    )


def date_from_row(row: dict, *field_names: str) -> str:
    raw_date = value(row, *field_names)
    if not raw_date:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_date, fmt).date().isoformat()
        except ValueError:
            pass
    return raw_date


def optional_fields(row: dict) -> dict:
    fields = {
        "pin": value(row, "pin", "PIN"),
        "membership_expires_on": date_from_row(
            row, "membership_expires_on", "Renewal Date"
        ),
        "padlock_pin": value(row, "padlock_pin"),
        "padlock_pin_valid_until": value(row, "padlock_pin_valid_until"),
    }
    return {key: item for key, item in fields.items() if item}


def dedupe_hash_from_row(row: dict, full_name: str, pin: str) -> str:
    """Identity hash distinguishing different people who share a name.

    Built from name, address, date of birth and the booking-system PIN; only
    the hash is stored, not the underlying personal data.
    """
    parts = [
        full_name,
        value(row, "Address1"),
        value(row, "Address2"),
        value(row, "Address3"),
        value(row, "Address4"),
        value(row, "Postcode"),
        value(row, "Date of Birth"),
        pin,
    ]
    normalized = "|".join(" ".join(part.lower().split()) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def item_from_row(row: dict) -> dict:
    item = {
        "member_id": value(row, "member_id", "User ID"),
        "full_name": full_name_from_row(row),
        "email": value(row, "email", "Email Address"),
        "status": status_from_row(row),
    }
    item.update(optional_fields(row))
    item["dedupe_hash"] = dedupe_hash_from_row(
        row, item["full_name"], item.get("pin", "")
    )
    missing = [key for key in ("member_id", "full_name", "email") if not item[key]]
    if missing:
        who = item["member_id"] or item["full_name"] or "unknown row"
        raise ValueError(f"missing {', '.join(missing)} for member {who}")
    return item


def save_member(conn, item: dict) -> None:
    """Upsert a member without overwriting padlock_pin fields.

    Insert must come first: updating after ensures new members get all
    fields, not just the four in the INSERT.
    """
    conn.execute(
        """INSERT OR IGNORE INTO members (member_id, full_name, email, status)
           VALUES (?, ?, ?, ?)""",
        (item["member_id"], item["full_name"], item["email"], item["status"]),
    )
    fields = {k: v for k, v in item.items() if k != "member_id"}
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [item["member_id"]]
    conn.execute(
        f"UPDATE members SET {set_clause} WHERE member_id = ?",
        values,
    )
    conn.commit()


def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.db import connect

    parser = argparse.ArgumentParser(description="Import members CSV into SQLite")
    parser.add_argument("--db", default="data/pingen.db")
    parser.add_argument("--csv", default="members.csv")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the database first and rebuild from scratch "
        "(wipes issued PINs and the processed-emails history)",
    )
    args = parser.parse_args()

    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(args.db + suffix).unlink(missing_ok=True)
        print(f"Deleted {args.db}, rebuilding from scratch")

    conn = connect(args.db)
    imported = 0
    skipped = []
    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                item = item_from_row(row)
            except ValueError as exc:
                skipped.append(str(exc))
                continue
            if item["full_name"].lower() in BALL_MACHINE_NAMES:
                print(f"Excluded ball machine account: {item['full_name']}")
                continue
            save_member(conn, item)
            imported += 1
            print(f"Imported {item['member_id']} {item['full_name']}")

    for reason in skipped:
        print(f"Skipped: {reason} (member cannot receive PINs; bookings will go to admin review)")
    print(f"\nDone: {imported} imported, {len(skipped)} skipped")


if __name__ == "__main__":
    main()
