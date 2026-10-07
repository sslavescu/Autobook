from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class InboundEmail:
    """A Gmail message read in raw form, with its body reduced to canonical text."""

    id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    message_id_header: Optional[str]
    text: str


@dataclass(frozen=True)
class Booking:
    message_hash: str
    thread_id: str
    requester_name: str
    raw_subject: str
    message_id_header: Optional[str] = None
    booking_period: Optional[str] = None
    booking_start: Optional[str] = None
    booking_end: Optional[str] = None
    cost: Optional[float] = None


@dataclass(frozen=True)
class Member:
    member_id: str
    full_name: str
    email: str
    membership_expires_on: Optional[str] = None
    padlock_pin: Optional[str] = None
    padlock_pin_valid_from: Optional[str] = None
    padlock_pin_valid_until: Optional[str] = None
    dedupe_hash: Optional[str] = None


@dataclass(frozen=True)
class GeneratedPin:
    code: str
    valid_from: datetime
    valid_until: datetime
    provider_access_id: Optional[str] = None
