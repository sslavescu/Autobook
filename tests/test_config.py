import pytest

from src.config import MAX_PIN_VALID_HOURS, ConfigError, _pin_valid_hours


@pytest.mark.parametrize("value, expected", [("1", 1), ("12", 12), ("24", 24)])
def test_pin_valid_hours_accepts_1_to_24(monkeypatch, value, expected):
    monkeypatch.setenv("PIN_VALID_HOURS", value)
    assert _pin_valid_hours() == expected


@pytest.mark.parametrize("value", ["0", "-1", "25", "168"])
def test_pin_valid_hours_rejects_out_of_range(monkeypatch, value):
    monkeypatch.setenv("PIN_VALID_HOURS", value)
    with pytest.raises(ConfigError, match="between 1 and 24"):
        _pin_valid_hours()


def test_pin_valid_hours_rejects_non_integer(monkeypatch):
    monkeypatch.setenv("PIN_VALID_HOURS", "twelve")
    with pytest.raises(ConfigError, match="whole number"):
        _pin_valid_hours()


def test_pin_valid_hours_defaults_to_12(monkeypatch):
    monkeypatch.delenv("PIN_VALID_HOURS", raising=False)
    assert _pin_valid_hours() == 12


def test_max_keeps_pins_free_of_lock_activation():
    # algoPINs lasting MORE than 24 hours must be activated on the lock
    assert MAX_PIN_VALID_HOURS == 24
