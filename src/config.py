import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# A padlock PIN is short-lived by design; anything longer is assumed to be a
# configuration mistake and stops the application at startup.
MAX_PIN_VALID_DAYS = 10


class ConfigError(Exception):
    """Configuration is invalid; the application must not run."""


@dataclass(frozen=True)
class Config:
    db_path: str
    gmail_credentials_path: str
    gmail_token_path: str
    igloohome_base_url: str
    igloohome_auth_url: str
    igloohome_credentials_path: str
    club_timezone: str
    lock_id: str
    booking_sender_filter: str
    booking_subject_filter: str
    pin_valid_days: int
    check_membership_expiry: bool
    fuzzy_name_threshold: int
    max_process_attempts: int
    admin_email: str
    email_redirect_to: str
    dry_run: bool


def _pin_valid_days() -> int:
    raw = os.getenv("PIN_VALID_DAYS", "7")
    try:
        days = int(raw)
    except ValueError:
        raise ConfigError(f"PIN_VALID_DAYS must be a whole number, got {raw!r}") from None
    if days < 1 or days >= MAX_PIN_VALID_DAYS:
        raise ConfigError(
            f"PIN_VALID_DAYS is {days}; it must be between 1 and "
            f"{MAX_PIN_VALID_DAYS - 1} days. Update the value in .env."
        )
    return days


def load_config(env_path: str | None = None) -> Config:
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    base = Path(os.getenv("SECRETS_DIR", "secrets"))

    return Config(
        db_path=os.getenv("DB_PATH", "data/pingen.db"),
        gmail_credentials_path=os.getenv(
            "GMAIL_CREDENTIALS_PATH", str(base / "gmail_credentials.json")
        ),
        gmail_token_path=os.getenv(
            "GMAIL_TOKEN_PATH", str(base / "gmail_token.json")
        ),
        igloohome_base_url=os.getenv(
            "IGLOOHOME_BASE_URL", "https://api.igloodeveloper.co/igloohome"
        ),
        igloohome_auth_url=os.getenv(
            "IGLOOHOME_AUTH_URL", "https://auth.igloohome.co/oauth2/token"
        ),
        igloohome_credentials_path=os.getenv(
            "IGLOOHOME_CREDENTIALS_PATH", str(base / "igloohome_credentials.json")
        ),
        club_timezone=os.getenv("CLUB_TIMEZONE", "Europe/Dublin"),
        lock_id=os.environ["LOCK_ID"],
        booking_sender_filter=os.getenv("BOOKING_SENDER_FILTER", "ebookingonline.net"),
        booking_subject_filter=os.getenv(
            "BOOKING_SUBJECT_FILTER", "Court Booking Confirmation"
        ),
        pin_valid_days=_pin_valid_days(),
        check_membership_expiry=os.getenv("CHECK_MEMBERSHIP_EXPIRY", "true").lower()
        == "true",
        fuzzy_name_threshold=int(os.getenv("FUZZY_NAME_THRESHOLD", "90")),
        max_process_attempts=int(os.getenv("MAX_PROCESS_ATTEMPTS", "3")),
        admin_email=os.environ["ADMIN_EMAIL"],
        email_redirect_to=os.getenv("EMAIL_REDIRECT_TO", ""),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    )
