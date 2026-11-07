"""WSGI entry point for production servers."""

import os

from app.server import app

if os.environ.get("SEVA_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
    )
