# Seva deployment guide

## Status and scope

This document covers a protected Render staging preview and a later controlled
production deployment. The Render blueprint intentionally omits provider
credentials and requires preview authentication. Do not remove those protections
or expose hospital facts until the owner has approved the data and access model.

The application has no build step. The browser app is plain HTML/CSS/JS in `web/`;
the server is Flask. Production is served by Gunicorn behind an HTTPS reverse
proxy.

## 1. Runtime and application requirements

- **Python:** 3.12 recommended. `.python-version` pins the major/minor version.
- **Python packages:** `requirements.txt` installs Flask, requests, Gunicorn (on
  Linux), NumPy, and `sounddevice`.
- **Web server:** Gunicorn, configured by `gunicorn.conf.py`.
- **Database:** the current Flask app uses **no production database**. Hospital
  facts are bundled read-only JSON in `data/hospital.json`. Browser history and
  preferences live in each visitor's `localStorage`. The SQLite file in
  `backend/seva.db` belongs to the legacy FastAPI app and must not be
  provisioned as part of this deployment.
- **Static files:** Flask serves `web/` directly. There is no Node build,
  separate static bundle, or service worker.
- **Persistent storage:** `SEVA_DATA_DIR` must be an absolute writable directory
  on a persistent volume. Seva uses it for:
  - `samples/web/` — answer audio;
  - `samples/alerts/` — alert, TTS, and replay audio;
  - `cache/` — Gnani TTS disk cache;
  - `usage.jsonl` — provider usage log;
  - `samples/clips/` — temporary microphone clips, deleted after processing.
- The protected free Render preview uses ephemeral `/tmp` storage and has no
  provider credentials. Do not enable provider-backed speech there: cached audio
  and usage state would disappear when the free instance restarts.
- **External APIs:** outbound HTTPS to Gnani (STT/TTS) and Google Gemini.
- **Background work:** announcement watch runs in a Python background thread
  inside the single Gunicorn worker. It does not use a separate job queue.
- **In-memory state:** watch state, alert state, and rate-limit counters are
  process-local. They reset when the process restarts and are not shared
  between workers.

### Microphone limitation

The microphone architecture uses `sounddevice` on the machine running Python.
A normal cloud server cannot access a visitor's phone or laptop microphone. In
a cloud deployment:

- `/api/listen` and `/api/watch/*` would listen to the cloud server's audio
  device, not the visitor's device.
- Production defaults disable these routes.
- The browser does not currently stream microphone audio to the server.
- Enabling watch requires a server host with a deliberately attached
  microphone, or a separately designed and approved browser-audio architecture.
- The text question, answer, TTS, and replay features work without a microphone.

Do not silently switch the microphone architecture during deployment.

## 2. Hosting compatibility

| Hosting type | Suitability | Notes |
|---|---|---|
| Small Linux VPS + Caddy/systemd | **Recommended** | Full Flask support, persistent disk, HTTPS, background thread, optional local microphone. |
| Managed container with persistent volume | Good for text/audio features | Use one instance; background watch is not reliable across replicas or auto-sleep. Persistent volume required. |
| PaaS with persistent disk and always-on process | Acceptable with checks | Confirm Gunicorn/port support, single-instance behavior, volume mounts, and no request-based sleeping. |
| Serverless functions (request-scoped) | **Not suitable for the full app** | No persistent process, local disk, watch thread, or reliable in-memory state. A static-only PWA would need a separate API. |
| Shared/cPanel hosting | **Not recommended** | Usually lacks a long-running Python process, native audio support, and persistent writable storage. |
| Docker on a VPS | Optional | Portable, but pass a persistent volume and do not expect container microphone access without explicit device mapping. |

### Estimated hosting needs and costs

These are planning estimates, not quotes:

- 1–2 vCPU, 1–2 GB RAM, and at least 10 GB persistent disk.
- A small VPS in this range commonly costs roughly **US$5–24/month** depending
  on provider, region, RAM, backups, and bandwidth.
- A domain typically costs roughly **US$10–20/year**; TLS via Let's Encrypt or
  Caddy is normally free.
- Gnani and Gemini usage is billed or quota-limited separately. Provider prices
  and quotas change; verify them before launch.
- Server-side microphone watch may require a physical/USB microphone and a host
  where the device can be attached. That is not a normal cloud feature.

No provider pricing was verified or purchased as part of this task.

## 3. Production configuration files

| File | Purpose |
|---|---|
| `gunicorn.conf.py` | Production Gunicorn settings, environment validation, safe access-log format, persistent data directory. |
| `wsgi.py` | WSGI entry point; optional `ProxyFix` only when `SEVA_TRUST_PROXY=1`. |
| `.env.example` | Placeholder-only environment template. Never put real keys in it. |
| `.python-version` | Pins Python 3.12 for compatible platforms. |
| `.gitattributes` | Keeps Linux deployment files on LF line endings. |
| `Procfile` | Generic PaaS start command: `gunicorn -c gunicorn.conf.py wsgi:app`. |
| `render.yaml` | Protected Render staging blueprint with no provider credentials. |
| `deploy/seva.service` | systemd unit for a Linux VPS. |
| `deploy/Caddyfile` | Minimal Caddy HTTPS reverse-proxy example. |
| `deploy/backup.sh` | Archives `SEVA_DATA_DIR` with retention and checksum. |
| `deploy/restore.sh` | Stops the service, restores an archive, fixes ownership, and restarts. |

## 4. Environment variables

Set variables in the hosting platform's secret/configuration manager or a
root-protected environment file. Do not upload `.env` or paste secret values into
source control, deployment files, tickets, logs, or screenshots.

In `SEVA_ENV=production`, Seva deliberately skips `.env` loading. Secrets must
come from the process environment.

### Required for the protected public preview

- `SEVA_ENV=production`
- `SEVA_PREVIEW_AUTH_REQUIRED=1`
- `SEVA_PREVIEW_USERNAME` and `SEVA_PREVIEW_PASSWORD` — enter these directly in
  Render's environment settings; never commit or paste the password into chat.
- `SEVA_DATA_DIR` — absolute writable path. The Render preview uses `/tmp/seva`
  and is ephemeral; production must use a persistent volume.
- `SEVA_ALLOWED_HOSTS` — comma-separated public hostnames, without schemes,
  paths, or spaces.

Provider credentials are optional at process startup and intentionally omitted
from `render.yaml`. Without them, the app does not contact Gnani or Gemini;
provider-backed features will return safe errors or local fallbacks.
`SEVA_HOSPITAL_DATA_APPROVED=0` keeps bundled hospital facts out of production
responses until they are verified and approved.

### Recommended

- `SEVA_BIND_HOST=127.0.0.1` — bind Gunicorn to loopback when Caddy or another
  trusted reverse proxy runs on the same host. The default is `0.0.0.0` for
  PaaS compatibility.
- `SEVA_TRUST_PROXY=1` — enable only when exactly one trusted proxy overwrites
  forwarded client IP, protocol, and host headers.
- `SEVA_HOSPITAL_DATA_APPROVED=0` — keep unverified bundled hospital data out
  of responses. Only set to `1` after written approval.
- `PORT=8000` — port Gunicorn listens on.
- `LOG_LEVEL=info` — Gunicorn log level.

### Optional provider settings

- `GEMINI_MODEL` — defaults to the application's configured Gemini model.
- `GNANI_BASE_URL` — override only when the provider supplies a different base.
- `GNANI_AUTH_HEADER` — override only for a provider-compatible header change.

### Development-only routes

Leave these unset or `0` for public hosting:

- `SEVA_ENABLE_SERVER_MIC=0`
- `SEVA_ENABLE_SIMULATE=0`
- `SEVA_ENABLE_USAGE_API=0`

`ALLOWED_ORIGINS` belongs to the legacy FastAPI backend. It is not used by this
Flask app and should not be treated as a production CORS setting here.

## 5. Recommended VPS setup

The following assumes Ubuntu 24.04 LTS (or another Linux distribution with
Python 3.12 available), Caddy, a dedicated `seva` user, and the release
directory `/srv/seva/current`. Adjust names to match the host.

### 5.1 Create service user and directories

```sh
sudo adduser --system --group --home /srv/seva seva
sudo mkdir -p /srv/seva /var/lib/seva /etc/seva /var/backups/seva
sudo chown -R seva:seva /srv/seva /var/lib/seva /var/backups/seva
sudo chmod 0750 /var/lib/seva
```

### 5.2 Install the release

Put the tested release in `/srv/seva/current` using Git or `rsync`. Do not copy
a development `.env`.

```sh
sudo -u seva python3.12 -m venv /srv/seva/current/.venv
sudo -u seva /srv/seva/current/.venv/bin/python -m pip install --upgrade pip
sudo -u seva /srv/seva/current/.venv/bin/pip install -r /srv/seva/current/requirements.txt
```

If server-microphone capture will be enabled, install the host PortAudio runtime
(for example `sudo apt install libportaudio2`) and attach a supported input
device. A cloud host without a microphone will not receive visitor audio.

### 5.3 Create the secret environment file

```sh
sudo install -m 0640 -o root -g seva /dev/null /etc/seva/seva.env
sudoedit /etc/seva/seva.env
```

Start from `.env.example` and fill in real values. At minimum:

```dotenv
SEVA_ENV=production
GNANI_API_KEY=...
GEMINI_API_KEY=...
SEVA_DATA_DIR=/var/lib/seva
SEVA_ALLOWED_HOSTS=seva.example.org
SEVA_BIND_HOST=127.0.0.1
SEVA_TRUST_PROXY=1
PORT=8000
LOG_LEVEL=info
```

Do not commit or copy this file into the release directory.

### 5.4 Install systemd and Caddy configuration

Install Caddy from its official package repository for the host OS, then:

```sh
sudo cp deploy/seva.service /etc/systemd/system/seva.service
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now seva
sudo systemctl reload caddy
```

Edit the hostname in `/etc/caddy/Caddyfile` first. Point the domain's DNS
records at the server before starting Caddy so it can obtain a certificate.
Caddy redirects HTTP to HTTPS and forwards the original host and scheme.

Keep port `8000` closed to the internet when Caddy is on the same host. Allow
only SSH and HTTP/HTTPS through the firewall, for example:

```sh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
```

### 5.5 Start and verify

```sh
sudo systemctl restart seva
sudo systemctl status seva --no-pager
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS https://seva.example.org/healthz
```

`/healthz` returns a minimal process health response. It does not verify Gnani,
Gemini, DNS, TLS, microphone availability, or provider quotas.

## 6. Start command and process behavior

The production start command is:

```sh
gunicorn -c gunicorn.conf.py wsgi:app
```

Do not use `START-SEVA.bat` or `python -m app.server` for production. Those
commands start Flask's development server and load `.env` outside production
mode.

`gunicorn.conf.py` refuses to start unless `SEVA_ENV=production` and the required
production variables are present. It requires preview authentication and an
absolute, creatable `SEVA_DATA_DIR`; provider API keys are not required to boot.

Production uses **one Gunicorn worker with threads**. This is intentional:
announcement watch state and rate limits are in-memory. Do not add multiple
workers or replicas without first moving that state to a shared service and
rethinking the single-microphone architecture.

Access logs are written to stdout/journald with the request path only. Query
strings are deliberately omitted because legacy replay requests can contain
spoken text. Do not enable a proxy log format that records full query strings
unless replay is changed to POST or signed URLs.

## 7. Database and persistence

There is no database initialization or migration step for the Flask app.

- `data/*.json` is bundled application content. Keep it in the release.
- Browser storage is per device and is not copied to the server.
- `SEVA_DATA_DIR` is the only mutable server state that needs a backup.
- In-memory watch state is intentionally lost on restart.
- Do not mount or initialize the legacy `backend/seva.db` for this app.

If a database is added later, provision a persistent volume, keep migrations in
the release, run migrations as a separate controlled step, and back up before
applying them.

## 8. Static files and caching

Flask serves `/app/` and the files in `web/`; API routes are served from the same
origin. No CORS configuration is required.

- Do not put API responses, generated audio, or replay URLs in a public CDN or
  shared cache.
- Generated audio is served with `Cache-Control: no-store`; the server may still
  keep a private file cache in `SEVA_DATA_DIR`.
- HTML/JS/CSS can be cached by the browser. After a release, force-refresh or
  version asset URLs if a stale page is reported.
- There is no service worker or offline cache, so a network failure disables the
  app even if the PWA is installed.

## 9. Logging and error handling

- Gunicorn writes error and access logs to stdout/stderr; under systemd inspect
  them with `journalctl -u seva -f`.
- The access-log format records method, path, status, size, and duration but not
  the query string.
- The application does not log request bodies, audio, transcripts, or provider
  keys.
- Production debug output is disabled. Provider exception details are hidden
  from responses unless an explicit development debug flag is enabled.
- `/healthz` is intentionally minimal and does not reveal whether keys are
  configured.
- Monitor disk space, provider usage/quota, error rate, and response health.
  Keep an external uptime check against `/healthz`.

Rate limiting is in-memory and per client IP. It is not authentication and is
not shared across workers or replicas. Put a maintained WAF/edge rate limit in
front of billable routes, and add authentication before unrestricted public use.

## 10. Backups and restore

### Back up

The provided script archives the persistent data directory. Make the scripts
executable on the host first (`chmod +x deploy/backup.sh deploy/restore.sh`):

```sh
sudo SEVA_DATA_DIR=/var/lib/seva ./deploy/backup.sh
```

It writes a timestamped `.tar.gz` plus SHA-256 checksum under
`/var/backups/seva` by default and removes archives older than 14 days. Override
with `SEVA_BACKUP_DIR` and `SEVA_BACKUP_RETENTION_DAYS`.

For a consistent copy, stop `seva.service` first or use a filesystem snapshot.
Treat audio and usage logs as potentially sensitive; encrypt off-host backups.

### Restore

```sh
sudo SEVA_DATA_DIR=/var/lib/seva SEVA_SERVICE_NAME=seva \
  ./deploy/restore.sh /var/backups/seva/seva-data-YYYYMMDDTHHMMSSZ.tar.gz
```

The script validates the archive, stops the service, moves the current data
directory aside, extracts the backup, fixes ownership, and starts the service.
Test restoration into a temporary directory before relying on it.

Browser `localStorage` is not included in server backups. Users lose saved
history if their browser storage is cleared.

## 11. Restart, release, and rollback

Restart after an environment or code change:

```sh
sudo systemctl restart seva
sudo systemctl status seva --no-pager
```

For repeatable releases, keep versioned release directories and a `current`
symlink:

```text
/srv/seva/releases/<release-id>/
/srv/seva/current -> /srv/seva/releases/<release-id>
```

Deploy procedure:

1. Back up `SEVA_DATA_DIR` and record the current release id.
2. Put the new release in a new directory; install its dependencies in its own
   virtual environment.
3. Run the offline test suite in that release.
4. Point `current` at the new release and restart the service.
5. Check `/healthz`, logs, and a text question/TTS path.

Rollback:

1. Point `current` back to the previous known-good release.
2. Restart the service.
3. Do **not** replace or delete `SEVA_DATA_DIR`; it is separate from releases.
4. If data was changed incompatibly, restore a tested backup instead.

This app currently has no Flask database schema migration, so the normal rollback
is code-only. Keep the previous release until the new one is accepted.

## 12. Manual setup checklist

- [ ] Choose a host with Python 3.12, persistent disk, HTTPS, and enough RAM.
- [ ] Point DNS at the host; confirm certificate issuance and renewal.
- [ ] Create unprivileged service user and `/var/lib/seva`.
- [ ] Install requirements into a virtual environment.
- [ ] Create `/etc/seva/seva.env` with production secrets and hostname; mode
      `0640`, owned by `root:seva`.
- [ ] Install `deploy/seva.service` and `deploy/Caddyfile`; adjust paths and
      hostname.
- [ ] Set `SEVA_BIND_HOST=127.0.0.1` and keep port 8000 firewalled.
- [ ] Confirm `SEVA_ENV=production` and `SEVA_ALLOWED_HOSTS` match the public
      hostname.
- [ ] Leave server microphone, simulation, and usage routes disabled.
- [ ] Add edge rate limits/WAF and decide whether authentication is required.
- [ ] Verify provider quotas, privacy/retention terms, and legal requirements.
- [ ] Test backup and restore.
- [ ] Run an external uptime check against `/healthz`.

## 13. Known limitations and readiness

The app is **ready for controlled staging** behind HTTPS with the microphone
routes disabled. It is **not ready for unrestricted public launch** until:

- authentication or an equivalent access control is added for billable routes;
- edge rate limiting/WAF and provider spending caps are configured;
- privacy/consent/retention terms for Gnani and Gemini are reviewed and accepted;
- hospital data is signed off against the real hospital;
- the single-worker, in-memory state model is accepted, or replaced with shared
  state for horizontal scaling;
- dependency vulnerability scanning is run in CI (`pip-audit` was unavailable
  during the audit).

The server-microphone watch feature is unavailable to ordinary cloud visitors
without a separately approved browser-audio architecture. Do not enable it on a
cloud host and present it as listening to the visitor's phone.
