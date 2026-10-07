# CIAC Ball Machine Padlock PIN Automation

App that polls a Gmail account for ball-machine booking emails, matches the booking name to a member, generates a short-lived igloohome algoPIN (`PIN_VALID_HOURS`) through the API, stores it in SQLite, and emails the member. Runs on a Linux VM via a systemd timer.

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

GDPR data minimisation: only `member_id`, `full_name`, `email`, and
`membership_expires_on` (used to cap PIN validity) are retained, plus a one-way
`dedupe_hash` (SHA-256 of name/address/DOB/booking-PIN) used solely to tell
apart distinct members sharing a name. Address, date of birth, the
booking-system PIN, password and all other CSV columns are never stored. Only
active members are imported; members absent from the next active export are
deleted. Generated padlock PINs are stored on the member record as
`padlock_pin` and `padlock_pin_valid_until`; only the current PIN is retained.

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
starting `Court Booking Confirmation:`. ebookingonline sends some as plain text
and some as HTML only, and may change which, so every email is fetched in raw
form (Gmail's "Show original") and its body reduced to one canonical text
(`src/email_reader.py`): the plain-text part if present, otherwise the HTML
with table rows as lines and cells separated by a tab. Both formats come out as
`Label<TAB>value` lines, and all parsing runs on that text.

This mailbox receives two kinds of confirmation:

| Kind | How it's recognised | Outcome |
|---|---|---|
| **Accessory booking** (the club's ball machine) | greeting is not `Hi Ball`, no `Ball Machine` player, and a `Cost of Booking` entry | PIN issued to Player 1 |
| **Ball Machine user booking** (a member's own machine) | greeted `Hi Ball`, or `Ball Machine` / `Ball M` listed as a player | info log only, no PIN (`skipped_ball_machine_user`) |

An email that looks like an accessory booking but has no `Cost of Booking`
entry is flagged as wrong: no PIN, an admin email containing the message, and
an info log (`flagged_missing_cost`). Cancellations use the subject
`Court Cancellation Confirmation` and are excluded by the subject filter.

A typical accessory booking:

```text
Hi Jane,
Date:<TAB>10:30 - 11:00 am , Sunday 13th September 2026
Player 1:<TAB>Jane Doe
Cost of Booking<TAB>€4.00
```

Player, Date and Cost labels tolerate a missing colon and any spacing. The
`Date` value is parsed into `booking_start`/`booking_end` (am/pm inferred for
ranges like `11:30 - 1:00 pm`). Tests use anonymised real emails saved in
`tests/fixtures/*.eml`.

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
- Issues a **new PIN for every booking**, even if the member still holds a
  valid one. Nothing is reused, so a PIN can never outlive the booking it was
  issued for.
- A new PIN starts at the whole hour before the booking (21:30 → 21:00; a
  21:00 booking starts at 20:00 so the member can get in early), falling back
  to the booking's own hour if that hour has already passed.
- It stays valid for `PIN_VALID_HOURS` (1–24; the app refuses to start outside
  that range). Staying at or below 24 hours matters: algoPINs that last longer
  must be **activated** on the lock within their first 24 hours, so members
  would have to use them or lose them. Short PINs need no activation.
- The PIN is shortened to 23:59 on the member's membership expiry day when that
  falls inside the window.
- **Membership never blocks a PIN.** If the membership has already expired, is
  unreadable, or is missing entirely, the member still gets their PIN and the
  admin receives a warning email instead (`sent_pin_membership_warning`). This is
  checked each time a PIN is issued.
  `CHECK_MEMBERSHIP_EXPIRY=false` ignores the membership completely: no
  shortening and no warning.
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
