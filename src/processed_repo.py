import sqlite3

from .models import Booking, Member


class ProcessedEmailRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def seen(self, message_hash: str, max_attempts: int) -> bool:
        """True when the message is done: succeeded, escalated, or out of retries."""
        row = self.conn.execute(
            "SELECT status, attempts FROM processed_emails WHERE message_hash = ?",
            (message_hash,),
        ).fetchone()
        if row is None:
            return False
        if row["status"] == "error":
            return row["attempts"] >= max_attempts
        return True

    def record_failure(self, message_hash: str, processed_at: str) -> int:
        """Increment the failure count for a message and return the new count."""
        row = self.conn.execute(
            "SELECT attempts FROM processed_emails WHERE message_hash = ?",
            (message_hash,),
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        self.conn.execute(
            """INSERT OR REPLACE INTO processed_emails
               (message_hash, status, processed_at, attempts)
               VALUES (?, 'error', ?, ?)""",
            (message_hash, processed_at, attempts),
        )
        self.conn.commit()
        return attempts

    def mark(
        self,
        message_hash: str,
        status: str,
        booking: Booking | None = None,
        member: Member | None = None,
        processed_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO processed_emails
               (message_hash, status, member_name, member_id,
                booking_period, booking_start, booking_end, processed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_hash,
                status,
                booking.requester_name if booking else None,
                member.member_id if member else None,
                booking.booking_period if booking else None,
                booking.booking_start if booking else None,
                booking.booking_end if booking else None,
                processed_at,
            ),
        )
        self.conn.commit()
