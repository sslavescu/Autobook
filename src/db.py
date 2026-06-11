import sqlite3
from pathlib import Path


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and initialise) the SQLite database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS members (
            member_id   TEXT PRIMARY KEY,
            full_name   TEXT NOT NULL,
            email       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'active',
            pin         TEXT,
            membership_expires_on TEXT,
            padlock_pin TEXT,
            padlock_pin_valid_until TEXT,
            dedupe_hash TEXT
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processed_emails (
            message_hash TEXT PRIMARY KEY,
            status       TEXT NOT NULL,
            member_name  TEXT,
            member_id    TEXT,
            booking_period TEXT,
            booking_start  TEXT,
            booking_end    TEXT,
            processed_at   TEXT,
            attempts     INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # Migrate databases created before these columns existed.
    for statement in (
        "ALTER TABLE processed_emails ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE members ADD COLUMN dedupe_hash TEXT",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def next_variance(conn: sqlite3.Connection) -> int:
    """Cycle the algoPIN variance 1 -> 2 -> 3 -> 1 across PIN creations."""
    row = conn.execute("SELECT value FROM app_state WHERE key = 'variance'").fetchone()
    last = int(row["value"]) if row else 0
    variance = last % 3 + 1
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES ('variance', ?)",
        (str(variance),),
    )
    conn.commit()
    return variance
