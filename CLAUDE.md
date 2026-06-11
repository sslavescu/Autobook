# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Linux VM app that polls a Gmail account for ball-machine booking emails, fuzzy-matches the booker's name to a club member in SQLite, generates a one-month igloohome algoPIN via their API, and emails the PIN to the member. Runs on a systemd timer every 5 minutes. Unmatched or unparseable bookings are forwarded to an admin email for manual review.

## Commands

### Install dependencies
```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### Run the app
```bash
python run.py
```

### Run tests
```bash
pytest
pytest tests/test_booking_parser.py            # single file
pytest tests/test_booking_parser.py::test_parse_booking_name_from_body  # single test
```

### Lint
```bash
ruff check src/ tests/ scripts/
ruff format --check src/ tests/ scripts/
```

### Import members
```bash
python scripts/import_members.py --csv members.csv
```

## Architecture

```
systemd timer (every 5 min) -> run.py -> Gmail API -> SQLite -> igloohome API -> Gmail send
```

### Entry point

`run.py` calls `src/handler.py:run()` which orchestrates the full flow:
1. Loads config from `.env` file via python-dotenv (`src/config.py`)
2. Opens SQLite database, creating tables on first run (`src/db.py`)
3. Loads Gmail OAuth credentials from local files; runs consent flow on first run (`src/gmail_client.py`)
4. Reads igloohome API key from a local file
5. Searches Gmail for unread booking emails (`src/gmail_client.py`)
6. For each email, checks it is a ball-machine booking (subject `Court Booking Confirmation`, any of Player 1–4 is "Ball Machine"/"Ball M") and parses Player 1 plus the `Date:` line (`src/booking_parser.py`). Real emails use \r\n line endings and names may contain non-ASCII characters.
7. Fuzzy-matches name against SQLite members using rapidfuzz (`src/member_repo.py`)
8. Generates or reuses an igloohome algoPIN (`src/igloohome_client.py`)
9. Emails the PIN to the member; records a privacy-preserving audit hash (`src/processed_repo.py`)

### Secrets and config

Config is in `.env` (see `.env.example`). Secrets are local files under `secrets/`:
- `gmail_credentials.json` — OAuth client config from Google Cloud Console
- `gmail_token.json` — auto-generated on first run, refreshed automatically
- `igloohome_api_key` — plain text file

### Key design decisions

- **No full email storage**: only a SHA-256 hash of the Gmail message ID is persisted in `processed_emails`, along with extracted booking metadata.
- **PIN reuse**: if a member already has a valid `padlock_pin`, it is reused rather than generating a new one.
- **PIN validity**: earliest of `PIN_VALID_DAYS` or the member's renewal date. 29+ days → daily algoPIN endpoint (midnight-aligned); under 29 days → hourly endpoint (hour-aligned). Lapsed membership → no PIN, admin alert. Variance cycles 1→2→3 (stored in `app_state`).
- **Duplicate names**: members get a `dedupe_hash` (name/address/DOB/booking-PIN) at import; if a booked name matches several distinct members, the booking goes to admin review instead of guessing.
- **Fuzzy matching**: `member_repo.py` scans all active members and uses `rapidfuzz.process.extractOne` with a configurable `FUZZY_NAME_THRESHOLD` (default 90).
- **DRY_RUN mode**: set `DRY_RUN=true` env var to skip igloohome API calls and use a placeholder PIN.
- **Import script upserts**: `scripts/import_members.py` upserts members without overwriting existing `padlock_pin` fields.
- **Gmail OAuth**: on first run, the app starts an interactive OAuth consent flow via `run_local_server` on port 8765 (headless: SSH-tunnel the port and open the printed URL). Token is saved and auto-refreshed on subsequent runs.
- **Reply threading**: the PIN email is sent as a reply to the booking email — `Re:` subject plus `In-Reply-To`/`References` headers and the Gmail `threadId` — addressed to the member only.
- **Retries**: transient failures are retried on subsequent runs up to `MAX_PROCESS_ATTEMPTS` (default 3); after the final failure the message is marked read and the admin is alerted.

### Data models

`src/models.py` defines frozen dataclasses: `Booking`, `Member`, `GeneratedPin`. The `Member.has_valid_padlock_pin()` method checks the stored PIN has not expired.

### SQLite tables (in `data/pingen.db`)

- **members** — key: `member_id`. Fields: `full_name`, `email`, `membership_expires_on`, `padlock_pin`, `padlock_pin_valid_until`, `dedupe_hash`. GDPR minimised: no status (only active members are imported; departed members are deleted on re-import), no address/DOB/booking-PIN (they feed the one-way `dedupe_hash` only). There is only one padlock, so no lock id is stored per member.
- **processed_emails** — key: `message_hash`. Deduplicates processed Gmail messages.

## Tech stack

- Python 3.12 on Linux VM
- SQLite (via stdlib `sqlite3`)
- google-api-python-client, google-auth-oauthlib, requests, rapidfuzz, python-dateutil, python-dotenv
- Tests: pytest; Linting: ruff
- Scheduling: systemd timer (`systemd/pingen.timer`)

## igloohome API

The endpoint in `src/igloohome_client.py` (`POST /locks/{lock_id}/algopins`) is a placeholder. It must be replaced with the exact endpoint from the igloohome API account documentation.
