from __future__ import annotations

import logging
import time
import unittest
from unittest.mock import patch

from flask import Flask, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from app import device_audio, server


class TestSessionDiagnostics(unittest.TestCase):
    def setUp(self):
        device_audio.clear_sessions()

    def tearDown(self):
        device_audio.clear_sessions()

    def create(self, address: str = "198.51.100.10"):
        return device_audio.create_session(
            "hospital",
            "47",
            "en-IN",
            client_ip=address,
        )

    def test_lookup_logs_found_missing_expired_and_owner_mismatch_safely(self):
        session = self.create()
        with self.assertLogs("app.device_audio", level=logging.INFO) as captured:
            found = device_audio.lookup_session(
                session.id, client_ip="198.51.100.10", route="state"
            )
            mismatch = device_audio.lookup_session(
                session.id, client_ip="203.0.113.20", route="chunk"
            )
            missing = device_audio.lookup_session(
                "unknown-session-secret",
                client_ip="198.51.100.10",
                route="state",
            )
            session.last_seen = time.time() - device_audio.SESSION_TTL_SECONDS - 1
            expired = device_audio.lookup_session(
                session.id, client_ip="198.51.100.10", route="chunk"
            )

        self.assertEqual(found.outcome, "found")
        self.assertIs(found.session, session)
        self.assertEqual(mismatch.outcome, "owner_mismatch")
        self.assertIsNone(mismatch.session)
        self.assertEqual(missing.outcome, "missing")
        self.assertEqual(expired.outcome, "expired")

        logs = "\n".join(captured.output)
        for outcome in ("found", "owner_mismatch", "missing", "expired"):
            self.assertIn(f"outcome={outcome}", logs)
        self.assertIn("route=state", logs)
        self.assertIn("route=chunk", logs)
        for sensitive in (
            session.id,
            "unknown-session-secret",
            "198.51.100.10",
            "203.0.113.20",
        ):
            self.assertNotIn(sensitive, logs)
        self.assertNotIn("value=47", logs)

    def test_creation_expiry_capacity_and_stop_outcomes_are_logged(self):
        with self.assertLogs("app.device_audio", level=logging.INFO) as captured:
            session = self.create()
            self.assertFalse(
                device_audio.stop_session(
                    session.id, client_ip="203.0.113.20"
                )
            )
            self.assertTrue(
                device_audio.stop_session(
                    session.id, client_ip="198.51.100.10"
                )
            )
            with patch.object(device_audio, "MAX_ACTIVE_SESSIONS", 1):
                self.create("198.51.100.11")
                self.create("198.51.100.12")

        logs = "\n".join(captured.output)
        for event in (
            "event=session_created",
            "event=session_stop",
            "event=session_evicted",
        ):
            self.assertIn(event, logs)
        self.assertIn("outcome=owner_mismatch", logs)
        self.assertIn("outcome=stopped", logs)
        self.assertIn("outcome=capacity", logs)
        for sensitive in ("198.51.100.10", "198.51.100.11", "198.51.100.12"):
            self.assertNotIn(sensitive, logs)
        self.assertNotIn("value=47", logs)

    def test_proxyfix_effective_remote_addr_is_the_value_used_by_client_helper(self):
        test_app = Flask("controlled-proxy-test")

        @test_app.get("/effective-address")
        def effective_address():
            return jsonify(client_ip=server._client_ip())

        test_app.wsgi_app = ProxyFix(test_app.wsgi_app, x_for=1)
        response = test_app.test_client().get(
            "/effective-address",
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
            headers={"X-Forwarded-For": "198.51.100.42"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["client_ip"],
            "198.51.100.42",
        )


if __name__ == "__main__":
    unittest.main()
