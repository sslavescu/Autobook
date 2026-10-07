# Deploying to a Debian 12 VPS

The app runs on a Debian 12 server under a dedicated, unprivileged `pingen`
system user, fired by a systemd timer. You administer the box as `sorin` over
SSH with sudo; `pingen` has no password and no login shell, and exists only to
own the app's files and run it.

Commands below are run as `sorin` on the VPS unless they say otherwise.

## 0. Server prerequisites

The server should already be hardened before anything is deployed:

- a `sorin` account with your SSH public key in `~/.ssh/authorized_keys` and
  membership of the `sudo` group;
- `PermitRootLogin no` and `PasswordAuthentication no` in
  `/etc/ssh/sshd_config.d/99-hardening.conf`;
- `unattended-upgrades` installed, and `ufw` allowing only `OpenSSH` inbound.

The app needs **no inbound ports** of its own — it only makes outbound HTTPS
calls to Gmail and igloohome.

```bash
ssh -i ~/.ssh/vpshosting sorin@YOUR_SERVER_IP
```

## 1. Packages

Debian 12 ships Python 3.11, which is what the app needs (3.10 or newer).

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

## 2. Create the service user

`--system` gives an account with no password and no login shell, so it cannot be
used to log in. It also creates `/opt/pingen`, owned by `pingen` — and for system
users `adduser` does not copy `/etc/skel`, so the directory is left empty and git
can clone straight into it:

```bash
sudo adduser --system --group --home /opt/pingen --shell /usr/sbin/nologin pingen
```

## 3. Install the app as `pingen`

`sudo -u pingen` runs each step as the service user, so everything under
`/opt/pingen` ends up owned by it and no `chown` is needed afterwards. These
paths are what the systemd units already expect.

```bash
sudo -u pingen git clone https://github.com/YOUR_USER/autobook.git /opt/pingen
```

```bash
sudo -u pingen python3 -m venv /opt/pingen/.venv
```

```bash
sudo -u pingen /opt/pingen/.venv/bin/pip install -r /opt/pingen/requirements.txt
```

## 4. Secrets and config

`secrets/` and `data/` are the only directories the service may write to.

```bash
sudo -u pingen mkdir -p /opt/pingen/secrets /opt/pingen/data && sudo chmod 700 /opt/pingen/secrets
```

Copy the secrets from your Mac. They are gitignored, so they are not in the
clone. Send them to `/tmp` first, because `sorin` cannot write into
`/opt/pingen`:

```bash
scp -i ~/.ssh/vpshosting secrets/gmail_credentials.json secrets/igloohome_credentials.json members.csv sorin@23.94.148.18:/tmp/
```

Then install them with the right owner and mode, and remove the copies:

```bash
sudo install -o pingen -g pingen -m 600 /tmp/gmail_credentials.json /tmp/igloohome_credentials.json /opt/pingen/secrets/ && rm /tmp/gmail_credentials.json /tmp/igloohome_credentials.json
```

Create the environment file:

```bash
sudo -u pingen cp /opt/pingen/.env.example /opt/pingen/.env && sudo -u pingen nano /opt/pingen/.env
```

Set `LOCK_ID` and `ADMIN_EMAIL`, check `PIN_VALID_HOURS` is between 1 and 24
(the app refuses to start otherwise), and set `HEALTHCHECK_URL`. For go-live,
make sure `EMAIL_REDIRECT_TO` is removed and `DRY_RUN` is unset or false.

## 5. Import members

```bash
sudo -u pingen /opt/pingen/.venv/bin/python /opt/pingen/scripts/import_members.py --db /opt/pingen/data/pingen.db --csv /tmp/members.csv
```

Delete the CSV afterwards — it contains member personal data:

```bash
rm /tmp/members.csv
```

## 6. Authorise Gmail (one-time, interactive)

The consent flow needs a browser and listens on port 8765, so tunnel that port
from your Mac. The OAuth client must be a **Desktop app** client from the
Google Cloud console, saved as `secrets/gmail_credentials.json`.

```bash
ssh -i ~/.ssh/vpshosting -L 8765:localhost:8765 sorin@YOUR_SERVER_IP
```

In that session:

```bash
cd /opt/pingen && sudo -u pingen .venv/bin/python scripts/generate_gmail_token.py
```

Open the printed URL in your local browser and approve. The token is written to
`secrets/gmail_token.json`, owned by `pingen`, and refreshed automatically after
that.

## 7. Install the timer

```bash
sudo cp /opt/pingen/systemd/pingen.service /opt/pingen/systemd/pingen.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now pingen.timer
```

The timer runs every 10 minutes. To change it, edit `OnUnitActiveSec` with
`sudo systemctl edit pingen.timer` and reload.

## 8. Verify

```bash
systemctl list-timers pingen.timer
```

```bash
sudo systemctl start pingen.service && sudo journalctl -u pingen.service -n 50 --no-pager
```

Service logs are system logs, so `sorin` needs `sudo` to read them. To drop the
`sudo`, add yourself to the `adm` group (journald grants it read access through
ACLs) and log out and back in:

```bash
sudo usermod -aG adm sorin
```

Journald only keeps logs across reboots if `/var/log/journal` exists. Check with
`sudo journalctl --disk-usage`; if it reports a runtime (volatile) journal,
create the directory and restart journald to make it persistent.

For a dry run that touches no locks, set `DRY_RUN=true` in `.env` first, run the
service once, then unset it.

## Pre-go-live checklist

- [ ] Google OAuth consent screen is **Published / in Production**, otherwise the
      refresh token expires after 7 days and the timer dies silently.
- [ ] `.env` has **no `EMAIL_REDIRECT_TO`** and **`DRY_RUN` unset or false**.
- [ ] `PIN_VALID_HOURS` is 1–24.
- [ ] `HEALTHCHECK_URL` set, so a crashed run alerts you.
- [ ] `members.csv` deleted from the server after import.
- [ ] A final `DRY_RUN=true` run reviewed before the first real run.

## Updating later

```bash
cd /opt/pingen && sudo -u pingen git pull && sudo -u pingen .venv/bin/pip install -r requirements.txt
```

Schema migrations apply automatically on the next run. Restart is not needed —
the timer starts a fresh process each time.

## Re-importing members

Export **all active members** each time: anyone missing from the CSV is deleted
from the database by design.

```bash
scp -i ~/.ssh/vpshosting members.csv sorin@YOUR_SERVER_IP:/tmp/ && ssh -i ~/.ssh/vpshosting sorin@YOUR_SERVER_IP 'sudo -u pingen /opt/pingen/.venv/bin/python /opt/pingen/scripts/import_members.py --db /opt/pingen/data/pingen.db --csv /tmp/members.csv; rm /tmp/members.csv'
```
