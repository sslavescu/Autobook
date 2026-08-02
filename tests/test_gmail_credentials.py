import json
from types import SimpleNamespace

from google.auth.exceptions import RefreshError

import src.gmail_client as gmail_client
from src.gmail_client import load_gmail_credentials


class _FakeCreds:
    def __init__(self, valid, expired=False, refresh_token=None, refresh_raises=False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._refresh_raises = refresh_raises
        self.refreshed = False

    def refresh(self, request):
        if self._refresh_raises:
            raise RefreshError("token has been expired or revoked")
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return json.dumps({"token": "fake"})


def _patch_consent(monkeypatch, returns):
    """Make the consent flow return a fresh valid credential and record the call."""
    calls = []

    def fake_flow(credentials_path):
        calls.append(credentials_path)
        return SimpleNamespace(run_local_server=lambda **kw: returns)

    monkeypatch.setattr(
        gmail_client.InstalledAppFlow,
        "from_client_secrets_file",
        staticmethod(lambda credentials_path, scopes: fake_flow(credentials_path)),
    )
    return calls


def test_dead_refresh_token_falls_back_to_consent(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"token": "stale"}))

    stale = _FakeCreds(valid=False, expired=True, refresh_token="rt", refresh_raises=True)
    monkeypatch.setattr(
        gmail_client.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path, scopes: stale),
    )
    fresh = _FakeCreds(valid=True)
    consent_calls = _patch_consent(monkeypatch, fresh)

    result = load_gmail_credentials("creds.json", str(token_file))

    assert result is fresh  # did not crash; produced a new credential
    assert consent_calls == ["creds.json"]  # consent flow was invoked
    assert json.loads(token_file.read_text()) == {"token": "fake"}  # new token saved


def test_valid_token_is_returned_without_refresh(monkeypatch, tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text("{}")
    good = _FakeCreds(valid=True)
    monkeypatch.setattr(
        gmail_client.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path, scopes: good),
    )

    def fail(*a, **k):
        raise AssertionError("consent flow should not run for a valid token")

    monkeypatch.setattr(
        gmail_client.InstalledAppFlow, "from_client_secrets_file", staticmethod(fail)
    )

    assert load_gmail_credentials("creds.json", str(token_file)) is good
    assert good.refreshed is False
