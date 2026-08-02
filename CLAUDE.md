# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Linux VM app that polls a Gmail account for ball-machine booking emails, fuzzy-matches the booker's name to a club member in SQLite, generates a one-month igloohome algoPIN via their API, and emails the PIN to the member. Runs on a systemd timer every 10 minutes. Unmatched or unparseable bookings are forwarded to an admin email for manual review. Deployment (Oracle Cloud free-tier VM) is documented in `DEPLOY.md`.

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
python scripts/import_members.py --csv members.csv          # upsert
python scripts/import_members.py --csv members.csv --reset  # wipe DB and rebuild
```

### Utility scripts
```bash
python scripts/generate_gmail_token.py   # one-time interactive Gmail OAuth
python scripts/generate_test_pin.py      # issue a real test algoPIN (no Gmail/DB)
python scripts/mock_igloohome.py         # local mock of the igloohome API
```

To run end-to-end against the mock, set in `.env`:
`IGLOOHOME_BASE_URL=http://localhost:9876/igloohome` and
`IGLOOHOME_AUTH_URL=http://localhost:9876/oauth2/token`.

## Architecture

```
systemd timer (every 10 min) -> run.py -> Gmail API -> SQLite -> igloohome API -> Gmail send
```

### Entry point

`run.py` calls `src/handler.py:run()` which orchestrates the full flow:
1. Loads config from `.env` file via python-dotenv (`src/config.py`)
2. Opens SQLite database, creating tables and applying idempotent migrations on first run (`src/db.py`)
3. Loads Gmail OAuth credentials from local files; runs consent flow on first run (`src/gmail_client.py`)
4. Loads igloohome OAuth2 client credentials (`src/igloohome_client.py`)
5. Searches Gmail for unread booking emails (`src/gmail_client.py`)
6. For each email, checks it is a ball-machine booking (subject `Court Booking Confirmation`, any of Player 1–4 is "Ball Machine"/"Ball M") and parses Player 1 plus the `Date:` line (`src/booking_parser.py`). Real emails use \r\n line endings and names may contain non-ASCII characters.
7. Fuzzy-matches name against SQLite members using rapidfuzz (`src/member_repo.py`)
8. Generates or reuses an igloohome algoPIN (`src/igloohome_client.py`)
9. Emails the PIN to the member using `templates/pin_email.txt` (placeholders `{first_name}`, `{pin}`, `{expiry}`); records a privacy-preserving audit hash (`src/processed_repo.py`)

After a successful run, `run.py` pings `HEALTHCHECK_URL` (dead-man's switch, e.g. healthchecks.io) — a crash skips the ping so the monitor alerts.

### Secrets and config

Config is in `.env` (see `.env.example`). Secrets are local files under `secrets/`:
- `gmail_credentials.json` — OAuth client config from Google Cloud Console
- `gmail_token.json` — auto-generated on first run, refreshed automatically
- `igloohome_credentials.json` — `{"client_id": "...", "client_secret": "..."}`; full-line `//` comments are tolerated

Test-mode env vars: `DRY_RUN=true` skips igloohome calls; `EMAIL_REDIRECT_TO=<addr>` reroutes ALL outgoing email (member PINs and admin alerts) to one address. Both must be off for go-live (see the checklist in `DEPLOY.md`).

### Key design decisions

- **No full email storage**: only a SHA-256 hash of the Gmail message ID is persisted in `processed_emails`, along with extracted booking metadata.
- **PIN reuse**: a stored `padlock_pin` is reused only if it *covers* the booking's parsed `[start, end]` period (`Member.padlock_pin_covers`); otherwise a new PIN is generated. Both `padlock_pin_valid_from` and `padlock_pin_valid_until` are stored because a PIN can start in the future (at the booking time).
- **New PIN validity**: from the booking's start time to the end of the booking's calendar month, capped at the member's renewal date (local midnight, so the PIN dies before the renewal day). A past booking start is clamped to now + `START_BUFFER_MINUTES` by the igloohome client. Duration 29+ days → daily algoPIN endpoint (midnight-aligned); under 29 days → hourly endpoint (hour-aligned). If the renewal date can't cover the booking → no PIN, admin alert. Variance cycles 1→2→3 (stored in `app_state`).
- **Cost gate**: the confirmation must contain `Cost of Booking:` with a non-zero amount before any PIN is issued (reused or new). Zero cost → `skipped_zero_cost`, no email. Missing line → admin alert (`manual_review_cost_missing`).
- **Duplicate names**: members get a `dedupe_hash` (name/address/DOB/booking-PIN) at import; if a booked name matches several distinct members, the booking goes to admin review instead of guessing.
- **Fuzzy matching**: `member_repo.py` scans all active members and uses `rapidfuzz.process.extractOne` with a configurable `FUZZY_NAME_THRESHOLD` (default 90).
- **Import script upserts**: `scripts/import_members.py` upserts members without overwriting existing `padlock_pin` fields; members absent from the export are deleted; the ball machine's own booking account is excluded.
- **Gmail OAuth**: on first run, the app starts an interactive OAuth consent flow via `run_local_server` on port 8765 (headless: SSH-tunnel the port and open the printed URL, or run `scripts/generate_gmail_token.py`). Token is saved and auto-refreshed on subsequent runs.
- **Reply threading**: the PIN email is sent as a reply to the booking email — `Re:` subject plus `In-Reply-To`/`References` headers and the Gmail `threadId` — addressed to the member only.
- **Retries**: transient failures are retried on subsequent runs up to `MAX_PROCESS_ATTEMPTS` (default 3); after the final failure the message is marked read and the admin is alerted.

### Data models

`src/models.py` defines frozen dataclasses: `Booking`, `Member`, `GeneratedPin`. The `Member.has_valid_padlock_pin()` method checks the stored PIN has not expired.

### SQLite tables (in `data/pingen.db`, WAL mode)

- **members** — key: `member_id`. Fields: `full_name`, `email`, `membership_expires_on`, `padlock_pin`, `padlock_pin_valid_from`, `padlock_pin_valid_until`, `dedupe_hash`. GDPR minimised: no status (only active members are imported; departed members are deleted on re-import), no address/DOB/booking-PIN (they feed the one-way `dedupe_hash` only). There is only one padlock, so no lock id is stored per member.
- **processed_emails** — key: `message_hash`. Deduplicates processed Gmail messages; tracks `status` and `attempts`.
- **app_state** — key/value; currently holds the algoPIN variance cycle.

Schema migrations are inline in `src/db.py:_create_tables` as try/except `ALTER TABLE` statements applied on every connect.

## Tech stack

- Python 3.12 on Linux VM
- SQLite (via stdlib `sqlite3`)
- google-api-python-client, google-auth-oauthlib, requests, rapidfuzz, python-dateutil, python-dotenv
- Tests: pytest; Linting: ruff
- Scheduling: systemd timer (`systemd/pingen.timer`)

## igloohome API

`src/igloohome_client.py` authenticates with OAuth2 client credentials: HTTP Basic auth against `https://auth.igloohome.co/oauth2/token` returns a short-lived bearer token, cached until near expiry. PINs are created with `POST {base_url}/devices/{lock_id}/algopin/{daily|hourly}` on `https://api.igloodeveloper.co/igloohome`. Daily algoPINs require whole local days (29–367), identical start/end hour, and `hh:00:00` timestamps — midnight-to-midnight satisfies all; hourly PINs have no minimum but need whole hours. `scripts/mock_igloohome.py` validates these same constraints locally.
