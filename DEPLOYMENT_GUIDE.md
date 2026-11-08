# Seva Deployment Guide

For the full, detailed production deployment guide, architecture requirements, VPS
setup, systemd units, Caddy configuration, backup/restore procedures, and Render
preview instructions, see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## Quick Reference Summary

### Architecture Summary
- **App Stack:** Flask + Python 3.12, Gunicorn WSGI (`workers = 1` for in-memory session coherence).
- **Web Client:** Plain HTML/CSS/JavaScript in `web/`, served directly by Flask at `/app/`. No Node build step.
- **Storage:** `SEVA_DATA_DIR` on a persistent volume for TTS cache, alerts, and usage logs. Ephemeral `/tmp` storage is acceptable only for staging/preview demos.
- **Microphone:**
  - Device microphone (PWA client): `web/device-listener.js` encodes 16 kHz mono WAV chunks uploaded to `POST /api/device/chunk`. Requires HTTPS.
  - Server microphone: Disabled in production (`SEVA_ENABLE_SERVER_MIC=0`).
  - Synthetic demo: Enabled via `/api/watch/demo` (`SEVA_ENABLE_SYNTHETIC_DEMO=1`) for safe demonstrations without audio hardware or external provider costs.

### Essential Production Environment Variables
| Variable | Value | Purpose |
|---|---|---|
| `SEVA_ENV` | `production` | Enables production security policies (e.g. HSTS over HTTPS, no `.env` loading). |
| `SEVA_PREVIEW_AUTH_REQUIRED` | `1` | Enforces HTTP Basic Auth for staging/preview instances. |
| `SEVA_PREVIEW_USERNAME` | `<username>` | Basic auth user. |
| `SEVA_PREVIEW_PASSWORD` | `<password>` | Basic auth password. |
| `SEVA_DATA_DIR` | `/var/lib/seva` | Persistent writable storage path. |
| `SEVA_ALLOWED_HOSTS` | `example.com,www.example.com` | Allowed hostnames for Host header validation. |
| `SEVA_TRUST_PROXY` | `1` | Enables Werkzeug `ProxyFix` when behind reverse proxy. |
| `SEVA_ENABLE_DEVICE_MIC` | `1` | Allows client browser audio capture over HTTPS. |
| `SEVA_ENABLE_SERVER_MIC` | `0` | Disables host hardware microphone probe. |
| `SEVA_HOSPITAL_DATA_APPROVED` | `0` | Keeps unapproved hospital facts disabled until written sign-off. |

### Running the Application
- **Local Development:** `START-SEVA.bat` or `python -m app.server --port 8000`
- **Production Server:** `gunicorn -c gunicorn.conf.py wsgi:app`

For full configuration and operations documentation, refer to [DEPLOYMENT.md](DEPLOYMENT.md).
