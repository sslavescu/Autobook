#!/usr/bin/env python3
"""Generate a Gmail OAuth token interactively.

Run once to authorise the app.  Prints a URL — open it in any browser,
sign in to the target Gmail account, consent, and paste the code back.
The resulting token is saved to the configured path (default:
secrets/gmail_token.json) and refreshed automatically on future runs.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gmail_client import load_gmail_credentials


def main():
    parser = argparse.ArgumentParser(description="Generate Gmail OAuth token")
    parser.add_argument(
        "--credentials",
        default="secrets/gmail_credentials.json",
        help="Path to OAuth client credentials JSON from Google Cloud Console",
    )
    parser.add_argument(
        "--token",
        default="secrets/gmail_token.json",
        help="Path where the token will be saved",
    )
    args = parser.parse_args()

    if not Path(args.credentials).exists():
        print(f"Error: credentials file not found at {args.credentials}")
        print(
            "Download it from Google Cloud Console → APIs & Services → Credentials "
            "→ OAuth 2.0 Client IDs → Download JSON"
        )
        sys.exit(1)

    creds = load_gmail_credentials(args.credentials, args.token)
    if creds and creds.valid:
        print(f"Token saved to {args.token}")
    else:
        print("Failed to obtain valid credentials")
        sys.exit(1)


if __name__ == "__main__":
    main()
