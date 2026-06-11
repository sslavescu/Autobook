#!/usr/bin/env python3
import logging
import sys

from src.handler import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    result = run()
    processed = result.get("processed", [])
    if processed:
        for entry in processed:
            logging.getLogger(__name__).info(
                "%s → %s", entry["message_hash"][:12], entry["status"]
            )
    else:
        logging.getLogger(__name__).info("No new booking emails")
    sys.exit(0)
