from __future__ import annotations

import os
import time
import unittest

from app import device_audio


class TestSessionLookupDiagnosticFields(unittest.TestCase):
    def setUp(self):
        device_audio.clear_sessions()

    def tearDown(self):
        device_audio.clear_sessions()

    def test_creation_and_lookup_logs_are_private_and_not_repeated_for_polls(self):
        with self.assertLogs("app.device_audio", level="INFO") as captured:
            session = device_audio.create_session(
                "hospital",
                "47",
                "en-IN",
                client_ip="198.51.100.10",
            )
            device_audio.lookup_session(
                session.id, client_ip="198.51.100.10", route="state"
            )
            device_audio.lookup_session(
                session.id, client_ip="198.51.100.10", route="state"
            )
            device_audio.lookup_session(
                session.id, client_ip="203.0.113.20", route="state"
            )
            device_audio.lookup_session(
                "missing-session-secret",
                client_ip="198.51.100.10",
                route="state",
            )
            session.last_seen = time.time() - device_audio.SESSION_TTL_SECONDS - 1
            device_audio.lookup_session(
                session.id, client_ip="198.51.100.10", route="chunk"
            )

        logs = captured.output
        creation = next(line for line in logs if "event=session_created" in line)
        lookups = [line for line in logs if "event=session_lookup" in line]

        self.assertIn(f"pid={os.getpid()}", creation)
        self.assertIn("owner_ip_stored=true", creation)
        self.assertEqual(len(lookups), 4)
        self.assertIn("outcome=found", lookups[0])
        self.assertIn("session_found=true", lookups[0])
        self.assertIn("owner_check_matched=true", lookups[0])
        self.assertIn("reason=owner_mismatch", lookups[1])
        self.assertIn("owner_check_matched=false", lookups[1])
        self.assertIn("reason=missing", lookups[2])
        self.assertIn("reason=expired", lookups[3])
        self.assertIn("expired=true", lookups[3])
        self.assertRegex(lookups[1], r"session_age_seconds=\d+\.\d{3}")

        all_logs = "\n".join(logs)
        for sensitive in (
            session.id,
            "missing-session-secret",
            "198.51.100.10",
            "203.0.113.20",
            "value=47",
        ):
            self.assertNotIn(sensitive, all_logs)

    def test_creation_reports_when_no_owner_ip_was_stored(self):
        with self.assertLogs("app.device_audio", level="INFO") as captured:
            device_audio.create_session(
                "hospital",
                "47",
                "en-IN",
                client_ip="",
            )

        self.assertIn("owner_ip_stored=false", captured.output[0])


if __name__ == "__main__":
    unittest.main()
