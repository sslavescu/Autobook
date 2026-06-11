# CIAC Ball Machine Padlock PIN Automation

App that polls a Gmail account for ball-machine booking emails, matches the booking name to a member, generates a one-month igloohome algoPIN through the API, stores it in SQLite, and emails the member. Runs on a Linux VM via a systemd timer.

## Architecture

```text
systemd timer (every 5 min) → run.py → Gmail API → SQLite → igloohome API → Gmail send
```

No Bridge is assumed. PINs are algoPINs.

## Project layout

```text
src/                  Application code
tests/                Unit tests
scripts/              Utility scripts (member import)
systemd/              systemd service + timer unit files
run.py                CLI entry point
members.example.csv   Import format
.env.example          Configuration template
```

## Setup

### 1. Install Python and create virtualenv

```bash
sudo apt install python3.12 python3.12-venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure secrets

Create a `secrets/` directory with:

- `gmail_credentials.json` — OAuth client credentials from Google Cloud Console
- `igloohome_api_key` — plain text file with your igloohome API key

On first run the app starts the Gmail OAuth consent flow via a local server on
port 8765 and saves `secrets/gmail_token.json` automatically. On a headless
machine, tunnel first (`ssh -L 8765:localhost:8765 <vm>`), then open the
printed URL in a local browser.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your LOCK_ID, ADMIN_EMAIL, etc.
```

### 4. Import members

```bash
python scripts/import_members.py --csv members.csv
```

The importer accepts the exported `members.csv` format used by the club system:

```csv
User ID,First Name,Last Name,PIN,Email Address,Active,Renewal Date
1001,Ralph,McMahon,1001,ralph@example.com,1,31/12/2099
```

It stores the fields needed by the app as `member_id`, `full_name`, `email`,
`status`, `pin`, and `membership_expires_on`. The source CSV `PIN` column is
retained as `pin` because it is the booking-system access PIN. Generated padlock
PINs are stored separately on the member record as `padlock_pin` and
`padlock_pin_valid_until`. Only the current padlock PIN is retained.

Re-running the import upserts members without touching issued PINs. Use
`--reset` to delete the database first and rebuild from scratch (wipes issued
PINs and the processed-emails history — useful after test runs).

### 5. Run manually or install systemd timer

```bash
# Manual run
python run.py

# Install systemd timer
sudo cp systemd/pingen.service systemd/pingen.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pingen.timer
```

## Booking email parsing

Booking confirmations come from `noreply@ebookingonline.net` with subjects
starting `Court Booking Confirmation:`. The body looks like:

```text
Hi Ball,

This is to confirm your court booking at CIAC as follows:

        Ref:        171392,171393
        Sport:      Tennis
        Court:      Court 6
        Date:       9:00 - 10:00 am , Saturday 13th June 2026
        Player 1:   Elle Marie Meñosa
        Player 2:   Ball Machine
```

A booking is treated as a ball-machine booking when any of `Player 1`–`Player 4`
is `Ball Machine` (or the older account name `Ball M`). The PIN is issued to
`Player 1`. The `Date:` line is parsed into `booking_start`/`booking_end`
(am/pm inferred for ranges like `11:30 - 1:00 pm`). Cancellation emails use the
subject `Court Cancellation Confirmation` and are excluded by the subject
filter. Booking confirmations without a ball-machine player are skipped
silently (status `skipped_not_ball_machine`).

## Stored booking data

The full Gmail email is not stored. The app stores a SHA-256 hash of the Gmail
message ID as `message_hash`, plus the matched member and booking fields:

```text
message_hash
member_name
member_id
booking_period
booking_start
booking_end
status
processed_at
```

`booking_start` and `booking_end` are only populated when the parser can identify
separate start/end values. Otherwise `booking_period` keeps the extracted booking
period string.

## igloohome API endpoint

`src/igloohome_client.py` contains a placeholder endpoint:

```text
POST /locks/{lock_id}/algopins
```

Replace this path and payload with the exact endpoint from your igloohome API account documentation.

## Safety behaviour

- Ignores already-processed Gmail messages using the stored `message_hash`.
- Reuses an existing valid padlock PIN until `padlock_pin_valid_until`.
- New PINs expire at the earliest of `PIN_VALID_DAYS` or the member's renewal
  date (midnight, so the PIN dies before the renewal day). PINs of 29+ days
  use the daily algoPIN endpoint; shorter ones use the hourly endpoint.
- Lapsed memberships (renewal date in the past) get no PIN; admin is alerted.
- algoPIN variance cycles 1 → 2 → 3 across PIN creations.
- Members sharing a name with another distinct member (identity hash from
  name/address/DOB/PIN) are never guessed; the admin is asked to issue manually.
- The ball machine's own booking account is excluded at import.
- Sends ambiguous or unparseable bookings to the admin email.
- Marks processed Gmail messages as read.
- Only processes emails from `BOOKING_SENDER_FILTER` (default `ebookingonline.com`).
- Sends the PIN as a threaded reply to the booking email (To: the member only).
- Retries failed messages on later runs, up to `MAX_PROCESS_ATTEMPTS` (default 3),
  then alerts the admin email and stops retrying.
- Supports `DRY_RUN=true` for testing without calling igloohome.
