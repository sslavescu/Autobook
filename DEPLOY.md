# Deploying to Oracle Cloud (Always Free tier)

This runs the app on an Always Free Arm VM with a systemd timer firing every
10 minutes. Oracle's Always Free `VM.Standard.A1.Flex` shapes do not expire.

## 1. Create the Oracle Cloud account (you, in a browser)

- Sign up at <https://www.oracle.com/cloud/free/>. Card verification is required
  even for the free tier (a small temporary hold, not a charge).
- Pick a home region close to the club (e.g. UK South (London) or Germany
  Central (Frankfurt) for Ireland). The region cannot be changed later.

## 2. Provision the VM (you, in the OCI Console)

1. **Compute → Instances → Create instance.**
2. Image & shape: **Canonical Ubuntu 24.04**, shape **VM.Standard.A1.Flex**
   (Ampere/Arm). 1 OCPU + 6 GB RAM is well within Always Free and plenty here.
3. Add your **SSH public key** (upload `~/.ssh/id_ed25519.pub`, or generate one
   with `ssh-keygen -t ed25519`).
4. Networking: let it create a VCN. **Do not add any ingress rules** — the app
   needs outbound HTTPS only, and SSH is enough for management. (Leave the
   default SSH ingress on port 22 so you can connect.)
5. Create, then note the instance's **public IP**.

## 3. First connection and OS prep

```bash
ssh ubuntu@<PUBLIC_IP>
sudo apt update && sudo apt install -y python3.12-venv git curl
```

## 4. Install the app

```bash
sudo useradd --system --home /opt/pingen --shell /usr/sbin/nologin pingen
sudo mkdir -p /opt/pingen
sudo chown "$USER" /opt/pingen
git clone https://github.com/<you>/autobook.git /opt/pingen
cd /opt/pingen
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 5. Secrets, config, members

Copy the secrets from your Mac (they are gitignored, so they are not in the repo):

```bash
# run these on your Mac
scp -r secrets ubuntu@<PUBLIC_IP>:/tmp/pingen-secrets
scp members.csv ubuntu@<PUBLIC_IP>:/tmp/members.csv
```

Then on the VM:

```bash
sudo mkdir -p /opt/pingen/secrets
sudo cp /tmp/pingen-secrets/* /opt/pingen/secrets/
rm -rf /tmp/pingen-secrets

cp .env.example .env
# Edit .env: real LOCK_ID and ADMIN_EMAIL, and for go-live make sure
# EMAIL_REDIRECT_TO is REMOVED and DRY_RUN is not true. Set HEALTHCHECK_URL.
nano .env

.venv/bin/python scripts/import_members.py --csv /tmp/members.csv --reset
rm /tmp/members.csv   # do not leave member PII on the box

sudo chown -R pingen:pingen /opt/pingen
sudo chmod 700 /opt/pingen/secrets
```

## 6. Authorise Gmail (one-time, interactive)

The OAuth consent flow needs a browser. It listens on port 8765, so tunnel it
from your Mac:

```bash
ssh -L 8765:localhost:8765 ubuntu@<PUBLIC_IP>
# on the VM:
cd /opt/pingen
sudo -u pingen .venv/bin/python scripts/generate_gmail_token.py
# open the printed URL in your local browser, approve, done
```

## 7. Install the timer

```bash
sudo cp systemd/pingen.service systemd/pingen.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pingen.timer
```

## 8. Verify

```bash
systemctl list-timers pingen.timer      # next/last run
journalctl -u pingen.service -f         # live logs
sudo systemctl start pingen.service     # trigger a run now
```

## Pre-go-live checklist (independent of hosting)

- [ ] The igloohome padlock is **registered to the account** behind the API
      credentials (`list_devices` returns it) — nothing issues a real PIN otherwise.
- [ ] Google OAuth consent screen is **Published / in Production** (else the
      refresh token dies after 7 days).
- [ ] `.env` has **no `EMAIL_REDIRECT_TO`** and **`DRY_RUN` unset/false**.
- [ ] `HEALTHCHECK_URL` set to a healthchecks.io (or similar) check, so a crash
      alerts you within the interval.
- [ ] `members.csv` deleted from the VM after import.
- [ ] A final `DRY_RUN=true` run reviewed before the first real run.

## Updating later

```bash
cd /opt/pingen && sudo -u pingen git pull
sudo -u pingen .venv/bin/pip install -r requirements.txt
# schema migrations apply automatically on next run
```
