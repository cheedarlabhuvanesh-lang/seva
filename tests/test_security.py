from __future__ import annotations

import base64
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from app import server


class TestSecurityHeaders(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_html_and_api_responses_share_the_security_baseline(self):
        for path in ("/app/", "/healthz", "/api/providers"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertEqual(
                    response.headers["Referrer-Policy"],
                    "strict-origin-when-cross-origin",
                )
                policy = response.headers["Content-Security-Policy"]
                self.assertIn("default-src 'self'", policy)
                self.assertIn("connect-src 'self'", policy)
                self.assertIn("frame-ancestors 'none'", policy)
                self.assertEqual(
                    response.headers["Permissions-Policy"],
                    "microphone=(self), camera=(), geolocation=()",
                )

    def test_hsts_is_only_added_for_production_https(self):
        with patch.dict(server.app.config, {"SEVA_ENV": "production"}):
            http_response = self.client.get("/healthz")
            https_response = self.client.get(
                "/healthz", base_url="https://localhost"
            )
        self.assertNotIn("Strict-Transport-Security", http_response.headers)
        self.assertIn("Strict-Transport-Security", https_response.headers)
        self.assertIn(
            "max-age=31536000", https_response.headers["Strict-Transport-Security"]
        )

        with patch.dict(server.app.config, {"SEVA_ENV": "development"}):
            development = self.client.get(
                "/healthz", base_url="https://localhost"
            )
        self.assertNotIn("Strict-Transport-Security", development.headers)

    def test_api_payload_routes_are_not_cached(self):
        for path in (
            "/api/device/capabilities",
            "/api/hospital/sources",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_hospital_and_device_routes_are_method_restricted(self):
        self.assertEqual(
            self.client.get("/api/hospital/announcement").status_code, 405
        )
        self.assertEqual(
            self.client.post("/api/hospital/sources", json={}).status_code, 405
        )
        self.assertEqual(
            self.client.get("/api/device/session").status_code, 405
        )

    def test_upload_limit_is_bounded(self):
        self.assertEqual(server.app.config["MAX_CONTENT_LENGTH"], 2 * 1024 * 1024)
        self.assertLessEqual(server.app.config["MAX_CONTENT_LENGTH"], 2 * 1024 * 1024)


class TestSecretNonDisclosure(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_provider_status_never_echoes_configured_secrets(self):
        secrets = {
            "GNANI_API_KEY": "TEST_ONLY_GNANI_SECRET_VALUE",
            "GEMINI_API_KEY": "TEST_ONLY_GEMINI_SECRET_VALUE",
        }
        with patch.dict(os.environ, secrets, clear=False):
            response = self.client.get("/api/providers")
        text = response.get_data(as_text=True)
        for value in secrets.values():
            self.assertNotIn(value, text)
        self.assertNotIn("api_key", text.lower())
        body = response.get_json()
        self.assertTrue(body["gnani"]["configured"])
        self.assertTrue(body["gemini"]["configured"])

    def test_hospital_routes_do_not_echo_source_values_as_secrets(self):
        with patch.dict(server.app.config, {
            "HOSPITAL_DATA_APPROVED": False,
            "HOSPITAL_SOURCE_ID": "ward-announcements",
            "HOSPITAL_SOURCE_NAME": "Ward announcements",
        }):
            sources = self.client.get("/api/hospital/sources")
            refused = self.client.post(
                "/api/hospital/announcement",
                json={"source_id": "ward-announcements", "text": "Token 47"},
            )
        for response in (sources, refused):
            self.assertNotIn("TEST_ONLY_", response.get_data(as_text=True))
        self.assertEqual(refused.status_code, 403)


class TestPreviewAuthBoundary(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.auth_header = "Basic " + base64.b64encode(
            b"demo:long-test-password"
        ).decode("ascii")

    def test_new_api_routes_are_behind_preview_auth(self):
        with patch.dict(server.app.config, {
            "SEVA_ENV": "production",
            "PREVIEW_AUTH_REQUIRED": True,
        }), patch.dict(os.environ, {
            "SEVA_PREVIEW_USERNAME": "demo",
            "SEVA_PREVIEW_PASSWORD": "long-test-password",
        }):
            health = self.client.get("/healthz", base_url="https://localhost")
            denied_providers = self.client.get(
                "/api/providers", base_url="https://localhost"
            )
            denied_hospital = self.client.get(
                "/api/hospital/sources", base_url="https://localhost"
            )
            allowed_hospital = self.client.get(
                "/api/hospital/sources", base_url="https://localhost",
                headers={"Authorization": self.auth_header},
            )
        self.assertEqual(health.status_code, 200)
        self.assertEqual(denied_providers.status_code, 401)
        self.assertEqual(denied_hospital.status_code, 401)
        self.assertEqual(allowed_hospital.status_code, 200)

    def test_allowed_hosts_wildcard_supports_render_domains(self):
        with patch.dict(server.app.config, {
            "TRUSTED_HOSTS": server._normalize_allowed_hosts("*.onrender.com, localhost")
        }):
            # Subdomain of onrender.com is allowed
            allowed = self.client.get("/healthz", headers={"Host": "my-seva-app.onrender.com"})
            self.assertEqual(allowed.status_code, 200)

            # Localhost is allowed
            local = self.client.get("/healthz", headers={"Host": "localhost:8000"})
            self.assertEqual(local.status_code, 200)

            # Untrusted host is rejected
            rejected = self.client.get("/healthz", headers={"Host": "attacker.com"})
            self.assertEqual(rejected.status_code, 400)

    def test_preview_auth_disabled_allows_access_without_credentials(self):
        with patch.dict(server.app.config, {
            "SEVA_ENV": "production",
            "PREVIEW_AUTH_REQUIRED": False,
        }), patch.dict(os.environ, {}, clear=False):
            # Remove credentials if present in env during test
            os.environ.pop("SEVA_PREVIEW_USERNAME", None)
            os.environ.pop("SEVA_PREVIEW_PASSWORD", None)
            providers = self.client.get("/api/providers", base_url="https://localhost")
            hospital = self.client.get("/api/hospital/sources", base_url="https://localhost")
        self.assertEqual(providers.status_code, 200)
        self.assertEqual(hospital.status_code, 200)

    def test_gunicorn_conf_conditional_validation(self):
        gunicorn_path = Path(__file__).resolve().parent.parent / "gunicorn.conf.py"
        gunicorn_code = gunicorn_path.read_text(encoding="utf-8")

        temp_dir = str(Path(os.environ.get("TEMP", "/tmp")).resolve())
        # Case 1: PREVIEW_AUTH_REQUIRED=0 without credentials should validate successfully
        env_disabled = {
            "SEVA_ENV": "production",
            "SEVA_PREVIEW_AUTH_REQUIRED": "0",
            "SEVA_DATA_DIR": temp_dir,
            "SEVA_ALLOWED_HOSTS": "example.com",
        }
        with patch.dict(os.environ, env_disabled, clear=True):
            namespace = {}
            exec(gunicorn_code, namespace)
            self.assertEqual(namespace["bind"], "0.0.0.0:8000")

        # Case 2: PREVIEW_AUTH_REQUIRED=1 without credentials should raise RuntimeError
        env_enabled_missing = {
            "SEVA_ENV": "production",
            "SEVA_PREVIEW_AUTH_REQUIRED": "1",
            "SEVA_DATA_DIR": temp_dir,
            "SEVA_ALLOWED_HOSTS": "example.com",
        }
        with patch.dict(os.environ, env_enabled_missing, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                exec(gunicorn_code, {})
            self.assertIn("SEVA_PREVIEW_USERNAME", str(caught.exception))

        # Case 3: PREVIEW_AUTH_REQUIRED=1 with credentials should validate successfully
        env_enabled_valid = {
            "SEVA_ENV": "production",
            "SEVA_PREVIEW_AUTH_REQUIRED": "1",
            "SEVA_PREVIEW_USERNAME": "admin",
            "SEVA_PREVIEW_PASSWORD": "secretpassword",
            "SEVA_DATA_DIR": temp_dir,
            "SEVA_ALLOWED_HOSTS": "example.com",
        }
        with patch.dict(os.environ, env_enabled_valid, clear=True):
            namespace = {}
            exec(gunicorn_code, namespace)
            self.assertEqual(namespace["bind"], "0.0.0.0:8000")


if __name__ == "__main__":
    unittest.main()
