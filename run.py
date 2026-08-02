#!/usr/bin/env python3
import logging
import os
import sys

from dotenv import load_dotenv

from src.handler import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def ping_healthcheck() -> None:
    """Ping a dead-man's-switch URL (e.g. healthchecks.io) after a successful run.

    Only reached when run() completed without raising, so a crash (dead OAuth
    token, config error, API auth failure) skips the ping and the monitor alerts.
    """
    url = os.getenv("HEALTHCHECK_URL")
    if not url:
        return
    try:
        import requests

        requests.get(url, timeout=10)
    except Exception:
        logger.warning("Healthcheck ping failed", exc_info=True)


if __name__ == "__main__":
    load_dotenv()
    result = run()
    processed = result.get("processed", [])
    if processed:
        for entry in processed:
            logger.info("%s → %s", entry["message_hash"][:12], entry["status"])
    else:
        logger.info("No new booking emails")
    ping_healthcheck()
    sys.exit(0)
