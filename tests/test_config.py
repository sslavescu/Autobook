import pytest

from src.config import MAX_PIN_VALID_DAYS, ConfigError, _pin_valid_days


@pytest.mark.parametrize("value, expected", [("1", 1), ("7", 7), ("11", 11)])
def test_pin_valid_days_accepts_1_to_11(monkeypatch, value, expected):
    monkeypatch.setenv("PIN_VALID_DAYS", value)
    assert _pin_valid_days() == expected


@pytest.mark.parametrize("value", ["0", "-1", "12", "30"])
def test_pin_valid_days_rejects_out_of_range(monkeypatch, value):
    monkeypatch.setenv("PIN_VALID_DAYS", value)
    with pytest.raises(ConfigError, match="between 1 and 11"):
        _pin_valid_days()


def test_pin_valid_days_rejects_non_integer(monkeypatch):
    monkeypatch.setenv("PIN_VALID_DAYS", "seven")
    with pytest.raises(ConfigError, match="whole number"):
        _pin_valid_days()


def test_pin_valid_days_defaults_to_7(monkeypatch):
    monkeypatch.delenv("PIN_VALID_DAYS", raising=False)
    assert _pin_valid_days() == 7


def test_max_covers_booking_horizon():
    # bookings open 10 days ahead; the PIN may need to reach one day past that
    assert MAX_PIN_VALID_DAYS == 11
