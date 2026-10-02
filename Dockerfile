# Runs the booking->PIN job once per container start, then exits.
# Scheduling is external (host systemd timer, cron, or a container scheduler)
# so each run is a fresh process and runs cannot overlap.
FROM python:3.12-slim

# Non-root. Fixed UID/GID so the mounted data/secrets volumes can be chowned
# to a matching owner on the host.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "$APP_GID" pingen \
 && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home --shell /usr/sbin/nologin pingen

WORKDIR /app

# Dependencies first so code edits don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes=false -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY templates/ ./templates/
COPY run.py ./

# Mount points for state that must survive between runs:
#   /app/data    SQLite database
#   /app/secrets OAuth + API credentials (gmail_token.json is rewritten on refresh)
RUN mkdir -p /app/data /app/secrets && chown -R pingen:pingen /app

USER pingen

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/pingen.db \
    SECRETS_DIR=/app/secrets

ENTRYPOINT ["python", "run.py"]
