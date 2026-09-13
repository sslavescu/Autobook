"""Build InboundEmail objects for tests through the production raw-email reader.

Fixtures in tests/fixtures/ are real ebookingonline confirmations with member
details anonymised, saved byte-for-byte as Gmail's format=raw returns them.
"""

from email.message import EmailMessage
from pathlib import Path

from src.email_reader import parse_raw_email
from src.models import InboundEmail

FIXTURES = Path(__file__).parent / "fixtures"


def make_email(
    subject: str,
    body: str,
    *,
    subtype: str = "plain",
    cte: str = "8bit",
    message_id: str = "abc",
    thread_id: str = "thr",
) -> InboundEmail:
    message = EmailMessage()
    message["From"] = "CIAC <noreply@ebookingonline.net>"
    message["Subject"] = subject
    message["Date"] = "Sun, 13 Sep 2026 10:00:00 +0100"
    message["Message-ID"] = "<orig@serverc.ebookingonline.net>"
    message.set_content(body, subtype=subtype, charset="utf-8", cte=cte)
    return parse_raw_email(message.as_bytes(), message_id, thread_id)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load_fixture(name: str, message_id: str = "fixture") -> InboundEmail:
    return parse_raw_email(fixture_bytes(name), message_id, "thr-" + message_id)


def fixture_body(name: str) -> tuple[str, str, str]:
    """(subject, content subtype, decoded body) of a fixture, for building variants."""
    from email import message_from_bytes, policy

    message = message_from_bytes(fixture_bytes(name), policy=policy.default)
    part = message.get_body(preferencelist=("plain", "html"))
    return str(message["Subject"]), part.get_content_subtype(), part.get_content()
