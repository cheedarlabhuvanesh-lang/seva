"""Production Gunicorn configuration. Run behind a TLS-terminating proxy."""

import os
from pathlib import Path

if os.environ.get("SEVA_ENV", "").strip().lower() != "production":
    raise RuntimeError("Set SEVA_ENV=production before starting Gunicorn.")

required = (
    "SEVA_DATA_DIR",
    "SEVA_ALLOWED_HOSTS",
    "SEVA_PREVIEW_USERNAME",
    "SEVA_PREVIEW_PASSWORD",
)
missing = [name for name in required if not os.environ.get(name, "").strip()]
if missing:
    raise RuntimeError(
        "Missing required production environment variables: " + ", ".join(missing)
    )

if os.environ.get("SEVA_PREVIEW_AUTH_REQUIRED", "1").strip().lower() not in {
    "1", "true", "yes", "on",
}:
    raise RuntimeError("Production deployments must require preview authentication.")

data_dir = Path(os.environ["SEVA_DATA_DIR"]).expanduser()
if not data_dir.is_absolute():
    raise RuntimeError("SEVA_DATA_DIR must be an absolute writable directory.")
data_dir.mkdir(parents=True, exist_ok=True)

port = int(os.environ.get("PORT", "8000"))
if not 1 <= port <= 65535:
    raise RuntimeError("PORT must be between 1 and 65535.")

bind_host = os.environ.get("SEVA_BIND_HOST", "0.0.0.0").strip()
if bind_host not in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
    raise RuntimeError(
        "SEVA_BIND_HOST must be 127.0.0.1, 0.0.0.0, ::1, or ::."
    )

bind = f"[{bind_host}]:{port}" if ":" in bind_host else f"{bind_host}:{port}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
# Keep logs useful without recording replay/announcement text. %(U)s is the
# path only; %(q)s (the query string) is deliberately omitted.
accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s'
errorlog = "-"
capture_output = True
loglevel = os.environ.get("LOG_LEVEL", "info").strip().lower()
