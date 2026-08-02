from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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

    def padlock_pin_covers(self, period_start: datetime, period_end: datetime) -> bool:
        """True when the stored PIN spans the whole [start, end] booking period."""
        if (
            not self.padlock_pin
            or not self.padlock_pin_valid_from
            or not self.padlock_pin_valid_until
        ):
            return False
        try:
            valid_from = datetime.fromisoformat(
                self.padlock_pin_valid_from.replace("Z", "+00:00")
            )
            valid_until = datetime.fromisoformat(
                self.padlock_pin_valid_until.replace("Z", "+00:00")
            )
        except ValueError:
            return False
        return valid_from <= period_start and valid_until >= period_end


@dataclass(frozen=True)
class GeneratedPin:
    code: str
    valid_from: datetime
    valid_until: datetime
    provider_access_id: Optional[str] = None
