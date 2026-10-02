from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
import logging

from .booking_parser import BookingKind, classify_booking, hash_message_id, parse_booking
from .config import Config, load_config
from .db import connect, next_variance
from .gmail_client import GmailClient, load_gmail_credentials
from .igloohome_client import IgloohomeClient, pin_start_for_booking
from .member_repo import AmbiguousMemberError, MemberRepository
from .models import Booking, InboundEmail, Member
from .processed_repo import ProcessedEmailRepository

logger = logging.getLogger(__name__)

# Placeholder used instead of calling igloohome when DRY_RUN is set. It is never
# persisted, and any legacy copy found in the database is replaced on a real run.
DRY_RUN_PIN = "DRY-RUN-PIN"


def run(cfg: Config | None = None) -> dict:
    if cfg is None:
        cfg = load_config()

    conn = connect(cfg.db_path)

    creds = load_gmail_credentials(cfg.gmail_credentials_path, cfg.gmail_token_path)
    gmail = GmailClient(creds, redirect_to=cfg.email_redirect_to or None)

    igloo = IgloohomeClient(
        base_url=cfg.igloohome_base_url,
        credentials_path=cfg.igloohome_credentials_path,
        auth_url=cfg.igloohome_auth_url,
        timezone_name=cfg.club_timezone,
    )

    members = MemberRepository(conn)
    processed = ProcessedEmailRepository(conn)

    messages = gmail.search_booking_messages(
        subject_filter=cfg.booking_subject_filter,
        sender_filter=cfg.booking_sender_filter,
        max_results=10,
    )

    results = []
    for message in messages:
        message_id = message.id
        message_hash = hash_message_id(message_id)
        if processed.seen(message_hash, cfg.max_process_attempts):
            continue
        processed_at = datetime.now(timezone.utc).isoformat()
        try:
            result, booking, member = process_message(
                cfg, gmail, igloo, members, message, conn
            )
            processed.mark(message_hash, result, booking, member, processed_at)
            gmail.mark_read(message_id)
            results.append({"message_hash": message_hash, "status": result})
        except Exception:
            logger.exception("Failed to process Gmail message hash %s", message_hash)
            attempts = processed.record_failure(message_hash, processed_at)
            if attempts >= cfg.max_process_attempts:
                _alert_admin_failure(cfg, gmail, message_hash, attempts)
                try:
                    gmail.mark_read(message_id)
                except Exception:
                    logger.exception("Failed to mark message hash %s read", message_hash)
            results.append(
                {"message_hash": message_hash, "status": f"error (attempt {attempts})"}
            )
    return {"processed": results}


def _alert_admin_failure(cfg, gmail, message_hash: str, attempts: int) -> None:
    try:
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking processing failed",
            body=(
                f"Processing Gmail message hash {message_hash} failed "
                f"{attempts} times and will not be retried.\n"
                "Check the pingen logs and handle this booking manually."
            ),
        )
    except Exception:
        logger.exception("Failed to alert admin about message hash %s", message_hash)


def process_message(
    cfg, gmail, igloo, members, message: InboundEmail, conn=None
) -> tuple[str, Booking | None, Member | None]:
    message_hash = hash_message_id(message.id)
    kind = classify_booking(message)

    if kind is BookingKind.NOT_BOOKING_CONFIRMATION:
        logger.info(
            "%s: not a booking confirmation, skipping (%s)", message_hash[:12], message.subject
        )
        return "skipped_not_booking_confirmation", None, None

    if kind is BookingKind.BALL_MACHINE_USER:
        logger.info(
            "%s: booking for the Ball Machine user found, not an accessory booking; "
            "no PIN issued (%s)",
            message_hash[:12],
            message.subject,
        )
        return "skipped_ball_machine_user", None, None

    booking = parse_booking(message)

    if kind is BookingKind.ACCESSORY_MISSING_COST:
        logger.info(
            "%s: accessory booking has no Cost of Booking entry; flagged as wrong, "
            "no PIN issued (%s)",
            message_hash[:12],
            message.subject,
        )
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking flagged - no Cost of Booking",
            body=(
                "This looks like a ball machine accessory booking, but it has no "
                "Cost of Booking entry, so no PIN was issued. Please check the "
                "booking:\n\n" + message_as_text(message)
            ),
        )
        return "flagged_missing_cost", booking, None

    if not booking:
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking requires manual review",
            body=(
                "Could not extract a member name (the Player line) from this "
                "booking confirmation:\n\n" + message_as_text(message)
            ),
        )
        return "manual_review_parse_failed", None, None

    try:
        member = members.find_by_name(booking.requester_name, cfg.fuzzy_name_threshold)
    except AmbiguousMemberError as exc:
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking matches several members",
            body=(
                f"Booking by {booking.requester_name!r} matches {exc.count} distinct "
                f"members named {exc.name!r}. Issue the PIN manually.\n"
                f"Booking: {booking.booking_period}\nMessage hash: {message_hash}"
            ),
        )
        return "manual_review_duplicate_member", booking, None
    if not member:
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking member not found",
            body=(
                f"Booking requester name not matched: {booking.requester_name}\n"
                f"Message hash: {message_hash}"
            ),
        )
        return "manual_review_member_not_found", booking, None

    now = datetime.now(timezone.utc)
    tz = ZoneInfo(cfg.club_timezone)
    period_start, period_end = booking_period(booking, now, tz)

    logger.debug(
        "%s: member %s (%s), booking %s -> %s, stored PIN valid %s -> %s",
        message_hash[:12],
        member.full_name,
        member.member_id,
        period_start.isoformat(),
        period_end.isoformat(),
        member.padlock_pin_valid_from,
        member.padlock_pin_valid_until,
    )
    membership_warning = None
    reusable = member.padlock_pin_covers(period_start, period_end)
    if reusable and member.padlock_pin == DRY_RUN_PIN and not cfg.dry_run:
        # Left over from a dry run against this database: it is not a real PIN,
        # so replace it with one generated by igloohome.
        logger.info(
            "Stored PIN for %s is the dry-run placeholder; generating a real PIN",
            member.full_name,
        )
        reusable = False

    if reusable:
        pin = member.padlock_pin
        valid_from = datetime.fromisoformat(
            member.padlock_pin_valid_from.replace("Z", "+00:00")
        )
        valid_until = datetime.fromisoformat(
            member.padlock_pin_valid_until.replace("Z", "+00:00")
        )
        membership_warning = stored_pin_membership_warning(
            valid_from,
            valid_until,
            member.membership_expires_on,
            tz,
            cfg.check_membership_expiry,
        )
    else:
        # The PIN opens on the hour before the booking and lasts PIN_VALID_DAYS.
        # The membership can shorten it but never blocks it; the admin is warned
        # instead (see pin_validity_end).
        valid_from = pin_start_for_booking(period_start, now, tz)
        valid_until, membership_warning = pin_validity_end(
            valid_from,
            cfg.pin_valid_days,
            member.membership_expires_on,
            tz,
            cfg.check_membership_expiry,
        )
        if cfg.dry_run:
            pin = DRY_RUN_PIN
        else:
            generated = igloo.create_monthly_algopin(
                lock_id=cfg.lock_id,
                member_name=member.full_name,
                valid_from=valid_from,
                valid_until=valid_until,
                variance=next_variance(conn) if conn is not None else 1,
            )
            pin = generated.code
            # The client aligns both ends to whole hours.
            valid_from = generated.valid_from
            valid_until = generated.valid_until
        if not cfg.dry_run:
            # Never persist the dry-run placeholder: a stored DRY-RUN-PIN looks
            # like a valid PIN and would be reused by later real runs.
            members.save_padlock_pin(member.member_id, pin, valid_from, valid_until)

    gmail.send_email(
        to=member.email,
        subject=reply_subject(booking.raw_subject),
        body=member_pin_email(member.full_name, pin, valid_until),
        thread_id=booking.thread_id,
        in_reply_to=booking.message_id_header,
    )

    if membership_warning:
        logger.info(
            "%s: membership warning for %s - %s",
            message_hash[:12],
            member.full_name,
            membership_warning,
        )
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine PIN issued - membership warning",
            body=(
                f"{membership_warning}\n\n"
                f"Member: {member.full_name} (member {member.member_id})\n"
                f"Booking: {booking.booking_period}\n"
                f"PIN valid: {valid_from.isoformat()} to {valid_until.isoformat()}\n\n"
                "The PIN was sent to the member as usual. Follow up on the "
                "membership if needed."
            ),
        )
        return "sent_pin_membership_warning", booking, member

    return "sent_pin", booking, member


def booking_period(
    booking: Booking, now: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """The [start, end] a PIN must cover, as timezone-aware datetimes.

    Falls back to `now` when the email's booking times could not be parsed.
    """
    start = _parse_local(booking.booking_start, tz) or now
    end = _parse_local(booking.booking_end, tz) or start
    return start, end


def _parse_local(iso: str | None, tz: ZoneInfo) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _membership_cap(
    membership_expires_on: str | None, tz: ZoneInfo
) -> tuple[datetime | None, str | None]:
    """(cap, warning): the moment a membership ends, or why it can't be compared."""
    if not membership_expires_on:
        return None, "No membership expiry date found for this member."
    try:
        expiry_day = datetime.strptime(membership_expires_on, "%Y-%m-%d")
    except ValueError:
        return None, (
            f"Membership expiry date could not be read ({membership_expires_on!r})."
        )
    return expiry_day.replace(hour=23, minute=59, tzinfo=tz), None


def pin_validity_end(
    pin_start: datetime,
    pin_valid_days: int,
    membership_expires_on: str | None,
    tz: ZoneInfo,
    check_membership_expiry: bool = True,
) -> tuple[datetime, str | None]:
    """Work out when a new PIN should stop working, and whether to warn the admin.

    The PIN runs for PIN_VALID_DAYS, shortened to the end of the membership
    expiry day (23:59 local) when that falls inside the window. The membership
    never blocks a PIN: if it has already expired the full window is issued
    anyway and the warning says the PIN runs beyond the membership.

    Returns (valid_until, warning); warning is None when the membership
    comfortably covers the PIN. CHECK_MEMBERSHIP_EXPIRY=false ignores the
    membership entirely - no shortening and no warning.
    """
    end = pin_start + relativedelta(days=pin_valid_days)
    if not check_membership_expiry:
        return end, None
    cap, warning = _membership_cap(membership_expires_on, tz)
    if cap is None:
        return end, warning
    if cap <= pin_start:
        return end, (
            f"Membership expired on {membership_expires_on}. The PIN was issued "
            "anyway and runs beyond the end of the membership."
        )
    if cap < end:
        return cap, (
            f"Membership expires on {membership_expires_on}, so the PIN was "
            "shortened to end then."
        )
    return end, None


def stored_pin_membership_warning(
    valid_from: datetime,
    valid_until: datetime,
    membership_expires_on: str | None,
    tz: ZoneInfo,
    check_membership_expiry: bool = True,
) -> str | None:
    """Whether reusing an already-issued PIN warrants a warning.

    The window was fixed when the PIN was issued, so it can't be shortened now;
    a membership that has lapsed since then is only reportable.
    """
    if not check_membership_expiry:
        return None
    cap, warning = _membership_cap(membership_expires_on, tz)
    if cap is None:
        return warning
    if cap <= valid_from:
        return (
            f"Membership expired on {membership_expires_on}. The PIN being reused "
            "runs beyond the end of the membership."
        )
    if cap < valid_until:
        return (
            f"Membership expires on {membership_expires_on}, before the reused "
            "PIN stops working."
        )
    return None


def message_as_text(message: InboundEmail) -> str:
    """Key headers plus the body as read (canonical text), for admin alerts."""
    body = message.text or "(no readable text found in the email body)"
    return (
        f"From: {message.sender}\nDate: {message.date}\nSubject: {message.subject}"
        f"\n\n{body}"
    )


def reply_subject(original_subject: str) -> str:
    subject = original_subject.strip() or "Ball machine booking"
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


PIN_EMAIL_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "pin_email.txt"


def member_pin_email(full_name: str, pin: str, valid_until: datetime) -> str:
    first_name = full_name.split()[0]
    # valid_until is an exclusive midnight boundary; show the last valid day.
    expiry = (valid_until - timedelta(minutes=1)).strftime("%d %B %Y")
    return PIN_EMAIL_TEMPLATE.read_text().format(
        first_name=first_name, pin=pin, expiry=expiry
    )
