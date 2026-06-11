import sqlite3
from datetime import datetime
from typing import Optional

from rapidfuzz import process

from .models import Member


class AmbiguousMemberError(Exception):
    """Several distinct members share the matched name."""

    def __init__(self, name: str, count: int):
        super().__init__(f"{count} distinct members named {name!r}")
        self.name = name
        self.count = count


class MemberRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_members(self) -> list[Member]:
        # Only active members are imported, so the whole table is eligible.
        rows = self.conn.execute("SELECT * FROM members").fetchall()
        return [self._to_member(row) for row in rows]

    def find_by_name(self, name: str, threshold: int) -> Optional[Member]:
        """Fuzzy-match a name to one active member.

        Raises AmbiguousMemberError when the matched name belongs to several
        distinct members (different dedupe_hash), so the caller can escalate
        to the admin instead of guessing who should receive the PIN.
        """
        by_name: dict[str, list[Member]] = {}
        for member in self.list_members():
            by_name.setdefault(member.full_name, []).append(member)
        match = process.extractOne(name, list(by_name.keys()), score_cutoff=threshold)
        if not match:
            return None
        candidates = by_name[match[0]]
        distinct = {c.dedupe_hash or c.member_id for c in candidates}
        if len(distinct) > 1:
            raise AmbiguousMemberError(match[0], len(candidates))
        return candidates[0]

    def save_padlock_pin(self, member_id: str, pin: str, valid_until: datetime) -> None:
        self.conn.execute(
            """UPDATE members
               SET padlock_pin = ?, padlock_pin_valid_until = ?
               WHERE member_id = ?""",
            (pin, valid_until.isoformat(), member_id),
        )
        self.conn.commit()

    @staticmethod
    def _to_member(row: sqlite3.Row) -> Member:
        return Member(
            member_id=row["member_id"],
            full_name=row["full_name"],
            email=row["email"],
            membership_expires_on=row["membership_expires_on"],
            padlock_pin=row["padlock_pin"],
            padlock_pin_valid_until=row["padlock_pin_valid_until"],
            dedupe_hash=row["dedupe_hash"],
        )
