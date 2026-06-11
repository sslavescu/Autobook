from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
import logging

from .booking_parser import hash_message_id, is_ball_machine_booking, parse_booking
from .config import Config, load_config
from .db import connect, next_variance
from .gmail_client import GmailClient, load_gmail_credentials
from .igloohome_client import IgloohomeClient
from .member_repo import AmbiguousMemberError, MemberRepository
from .models import Booking, Member
from .processed_repo import ProcessedEmailRepository

logger = logging.getLogger(__name__)


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
        message_id = message["id"]
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
    cfg, gmail, igloo, members, message: dict, conn=None
) -> tuple[str, Booking | None, Member | None]:
    message_hash = hash_message_id(message["id"])
    if not is_ball_machine_booking(message):
        return "skipped_not_ball_machine", None, None

    booking = parse_booking(message)
    if not booking:
        gmail.send_email(
            to=cfg.admin_email,
            subject="Ball machine booking requires manual review",
            body=f"Could not extract a member name from Gmail message hash {message_hash}.",
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
    if member.has_valid_padlock_pin(now):
        pin = member.padlock_pin
        valid_until = datetime.fromisoformat(
            member.padlock_pin_valid_until.replace("Z", "+00:00")
        )
    else:
        valid_until = pin_validity_end(
            now, cfg.pin_valid_days, member.membership_expires_on, tz
        )
        if valid_until is None:
            gmail.send_email(
                to=cfg.admin_email,
                subject="Ball machine booking - membership lapsed",
                body=(
                    f"{member.full_name} (member {member.member_id}) booked the ball "
                    f"machine but their membership renewal date "
                    f"({member.membership_expires_on}) has passed. "
                    "No PIN was issued; handle manually or ask them to renew."
                ),
            )
            return "manual_review_membership_lapsed", booking, member
        if cfg.dry_run:
            pin = "DRY-RUN-PIN"
        else:
            generated = igloo.create_monthly_algopin(
                lock_id=cfg.lock_id,
                member_name=member.full_name,
                valid_from=now,
                valid_until=valid_until,
                variance=next_variance(conn) if conn is not None else 1,
            )
            pin = generated.code
            # The API aligns the validity to whole local days.
            valid_until = generated.valid_until
        members.save_padlock_pin(member.member_id, pin, valid_until)

    gmail.send_email(
        to=member.email,
        subject=reply_subject(booking.raw_subject),
        body=member_pin_email(member.full_name, pin, valid_until),
        thread_id=booking.thread_id,
        in_reply_to=booking.message_id_header,
    )
    return "sent_pin", booking, member


def pin_validity_end(
    now: datetime, pin_valid_days: int, membership_expires_on: str | None, tz: ZoneInfo
) -> datetime | None:
    """End of validity for a new PIN: the earliest of now + pin_valid_days or
    the membership renewal date (midnight, so the PIN dies before renewal day).

    Returns None when the membership has already lapsed.
    """
    end = now + relativedelta(days=pin_valid_days)
    if membership_expires_on:
        try:
            renewal = datetime.strptime(membership_expires_on, "%Y-%m-%d")
        except ValueError:
            renewal = None
        if renewal is not None:
            cap = renewal.replace(tzinfo=tz)
            if cap < end:
                end = cap
    if end <= now:
        return None
    return end


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
