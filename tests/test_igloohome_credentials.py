import pytest

from src.igloohome_client import _load_credentials


def test_loads_plain_json(tmp_path):
    f = tmp_path / "creds.json"
    f.write_text('{"client_id": "abc", "client_secret": "def"}')
    assert _load_credentials(str(f)) == {"client_id": "abc", "client_secret": "def"}


def test_ignores_full_line_comments(tmp_path):
    f = tmp_path / "creds.json"
    f.write_text(
        "// old credentials kept for reference\n"
        '//   {"client_id": "old", "client_secret": "stale"}\n'
        "\n"
        '{"client_id": "new", "client_secret": "fresh"}\n'
    )
    assert _load_credentials(str(f))["client_id"] == "new"


def test_invalid_json_raises_clear_error(tmp_path):
    f = tmp_path / "creds.json"
    f.write_text("not json at all")
    with pytest.raises(ValueError, match="not valid JSON"):
        _load_credentials(str(f))


def test_missing_field_raises_clear_error(tmp_path):
    f = tmp_path / "creds.json"
    f.write_text('{"client_id": "abc"}')
    with pytest.raises(ValueError, match="missing required field"):
        _load_credentials(str(f))
