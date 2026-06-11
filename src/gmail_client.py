import base64
import logging
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def load_gmail_credentials(credentials_path: str, token_path: str) -> Credentials:
    """Load or create Gmail OAuth credentials.

    On first run (no token file), launches the OAuth consent flow in console
    mode so it works on headless machines.  On subsequent runs, silently
    refreshes the access token using the stored refresh token.
    """
    token_file = Path(token_path)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired Gmail access token")
        creds.refresh(Request())
    else:
        logger.info("No valid token found — starting OAuth consent flow")
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        # run_console() was removed from google-auth-oauthlib 1.0 (Google shut
        # down the OOB flow). run_local_server on a fixed port still works
        # headless: tunnel with `ssh -L 8765:localhost:8765 <vm>` and open the
        # printed URL in a local browser.
        creds = flow.run_local_server(
            port=8765,
            open_browser=False,
            authorization_prompt_message=(
                "Open this URL in a browser to authorise Gmail access.\n"
                "On a headless machine, first run: ssh -L 8765:localhost:8765 <vm>\n"
                "{url}"
            ),
        )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    return creds


class GmailClient:
    def __init__(self, creds: Credentials, redirect_to: str | None = None):
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        # When set, every outgoing email goes to this address instead of the
        # real recipient (test mode); the intended recipient is noted in the body.
        self.redirect_to = redirect_to

    def search_booking_messages(self, subject_filter: str, sender_filter: str = "", max_results: int = 10) -> list[dict]:
        query_parts = ["is:unread"]
        if subject_filter:
            query_parts.append(f'subject:"{subject_filter}"')
        if sender_filter:
            query_parts.append(f"from:{sender_filter}")
        response = self.service.users().messages().list(
            userId="me", q=" ".join(query_parts), maxResults=max_results
        ).execute()
        messages = response.get("messages", [])
        return [self.get_message(m["id"]) for m in messages]

    def get_message(self, message_id: str) -> dict:
        return self.service.users().messages().get(userId="me", id=message_id, format="full").execute()

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> None:
        if self.redirect_to:
            body = f"[TEST MODE] Intended recipient: {to}\n\n{body}"
            to = self.redirect_to
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        # Gmail only threads a message as a reply when the RFC 2822 headers
        # reference the original Message-ID, in addition to threadId.
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        self.service.users().messages().send(userId="me", body=payload).execute()

    def mark_read(self, message_id: str) -> None:
        self.service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
