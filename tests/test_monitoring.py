#!/usr/bin/env python3
"""Regression tests for operational counters and abuse logging.

No network, microphone, or real provider key is used.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import server


class FakeClient:
    api_key = "test-key"

    def __init__(self):
        self.speak_calls = []

    def speak(self, text, *, language, voice, sample_rate=None, out_path=None):
        self.speak_calls.append(text)
        Path(out_path).write_bytes(b"RIFF" + b"\0" * 64)


class TestOperationalMetrics(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        with server._METRICS_LOCK:
            for name in server._METRICS:
                server._METRICS[name] = 0
        with server.SPEECH_RATE_LOCK:
            server.SPEECH_RATE.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(server, "ALERT_AUDIO_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_endpoint_reports_counters_without_secrets(self):
        with patch.object(server.GnaniClient, "is_configured", return_value=True), \
                patch.object(server.gemini, "is_configured", return_value=True):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["providers"]["gnani_configured"])
        self.assertTrue(body["providers"]["gemini_configured"])
        self.assertIn("tts_calls", body["metrics"])
        text = response.get_data(as_text=True).lower()
        self.assertNotIn("test-key", text)
        self.assertNotIn("api_key", text)

    def test_rate_limit_increments_counter_and_hashes_client(self):
        fake = FakeClient()
        with patch.object(server, "SPEECH_RATE_LIMIT", 1), \
                patch.object(server, "get_client", return_value=fake):
            first = self.client.post(
                "/tts", json={"text": "Token 47 called", "language": "en-IN"}
            )
            with self.assertLogs(server.app.logger, level="WARNING") as logs:
                second = self.client.post(
                    "/tts", json={"text": "Token 47 called", "language": "en-IN"}
                )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(server._metrics_snapshot()["speech_rate_limited"], 1)
        output = "\n".join(logs.output)
        self.assertIn("event=rate_limited", output)
        self.assertNotIn("127.0.0.1", output)
        self.assertNotIn("Token 47", output)

    def test_provider_failure_increments_counter(self):
        with patch.object(
            server, "get_client", side_effect=server.GnaniError("provider down")
        ):
            response = self.client.post(
                "/tts", json={"text": "Token 47 called", "language": "en-IN"}
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(server._metrics_snapshot()["tts_failures"], 1)

    def test_cross_site_replay_increments_counter(self):
        response = self.client.get(
            "/api/replay",
            query_string={"text": "Token 47 called", "lang": "en-IN"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(server._metrics_snapshot()["cross_site_rejected"], 1)
        self.assertEqual(server._metrics_snapshot()["tts_calls"], 0)


if __name__ == "__main__":
    unittest.main()
