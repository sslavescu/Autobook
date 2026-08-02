#!/usr/bin/env python3
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env before configuring logging so LOG_LEVEL can be set there.
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# The Google client libraries are extremely chatty at DEBUG; keep them at INFO
# unless LOG_HTTP is on, so app-level debug output stays readable.
for noisy in ("googleapiclient.discovery", "google_auth_httplib2", "google.auth"):
    logging.getLogger(noisy).setLevel(logging.INFO)

if os.getenv("LOG_HTTP", "false").lower() == "true":
    # Full HTTP wire logging, including request/response headers.
    # WARNING: this prints Authorization headers, OAuth tokens and PINs.
    # Use only for local debugging, never on a shared or production host.
    import http.client

    http.client.HTTPConnection.debuglevel = 1
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    logging.getLogger("googleapiclient.discovery").setLevel(logging.DEBUG)
    logger.warning("LOG_HTTP is on: logs will contain credentials and PINs")

from src.handler import run  # noqa: E402  (import after logging is configured)


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
    result = run()
    processed = result.get("processed", [])
    if processed:
        for entry in processed:
            logger.info("%s → %s", entry["message_hash"][:12], entry["status"])
    else:
        logger.info("No new booking emails")
    ping_healthcheck()
    sys.exit(0)
