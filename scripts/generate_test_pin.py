#!/usr/bin/env python3
"""Generate a daily algoPIN for the lock in the LOCK_ID env var.

Standalone test for the igloohome integration — does not touch Gmail or the
database. Reads LOCK_ID (and optional IGLOOHOME_* overrides) from .env.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from src.igloohome_client import IgloohomeClient


def main():
    parser = argparse.ArgumentParser(description="Generate a test daily algoPIN")
    parser.add_argument("--days", type=int, default=31, help="Validity in days (29-367)")
    parser.add_argument("--name", default="pingen test", help="accessName for the PIN")
    args = parser.parse_args()

    load_dotenv()
    lock_id = os.getenv("LOCK_ID")
    if not lock_id:
        print("Error: LOCK_ID is not set. Add it to .env (the device Bluetooth ID).")
        sys.exit(1)

    client = IgloohomeClient(
        base_url=os.getenv("IGLOOHOME_BASE_URL", "https://api.igloodeveloper.co/igloohome"),
        credentials_path=os.getenv(
            "IGLOOHOME_CREDENTIALS_PATH", "secrets/igloohome_credentials.json"
        ),
        auth_url=os.getenv("IGLOOHOME_AUTH_URL", "https://auth.igloohome.co/oauth2/token"),
        timezone_name=os.getenv("CLUB_TIMEZONE", "Europe/Dublin"),
    )

    now = datetime.now(timezone.utc)
    generated = client.create_monthly_algopin(
        lock_id=lock_id,
        member_name=args.name,
        valid_from=now,
        valid_until=now + relativedelta(days=args.days),
    )
    print(f"PIN:         {generated.code}")
    print(f"Valid from:  {generated.valid_from.isoformat()}")
    print(f"Valid until: {generated.valid_until.isoformat()}")
    print(f"pinId:       {generated.provider_access_id}")


if __name__ == "__main__":
    main()
