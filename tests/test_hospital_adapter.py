from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from app import hospital, server


class TestAnnouncementNormalization(unittest.TestCase):
    def test_synthetic_announcement_has_the_documented_schema(self):
        adapter = hospital.SyntheticAdapter()
        announcement = adapter.parse({
            "text": "  Token 47 may proceed to Counter 3.  ",
            "language": "en-IN",
            "status": "now-serving",
            "metadata": {"counter": "3"},
        })
        payload = announcement.to_dict()
        self.assertEqual(payload["source_id"], "synthetic-demo")
        self.assertEqual(payload["source_type"], "synthetic")
        self.assertEqual(payload["text"], "Token 47 may proceed to Counter 3.")
        self.assertEqual(payload["language"], "en-IN")
        self.assertEqual(payload["status"], "now_serving")
        self.assertEqual(payload["kind"], "hospital")
        self.assertFalse(payload["real_data"])
        self.assertEqual(payload["metadata"], {"counter": "3"})
        self.assertIsInstance(payload["received_at"], str)
        datetime.fromisoformat(payload["received_at"])

    def test_synthetic_default_text_is_used_when_text_is_absent(self):
        announcement = hospital.SyntheticAdapter().parse({})
        self.assertIn("Synthetic demo", announcement.text)
        self.assertFalse(announcement.real_data)

    def test_invalid_fields_are_rejected(self):
        adapter = hospital.SyntheticAdapter()
        cases = [
            {"text": ""},
            {"text": "x" * (hospital.MAX_TEXT_LENGTH + 1)},
            {"text": "ok", "status": "invented"},
            {"text": "ok", "language": "english!"},
            {"text": "ok", "kind": "Hospital Room"},
            {"text": "ok", "metadata": ["not", "an", "object"]},
            {"text": "ok", "metadata": {"blob": "x" * (hospital.MAX_METADATA_BYTES + 1)}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(hospital.HospitalError) as caught:
                    adapter.parse(payload)
                self.assertEqual(caught.exception.status_code, 400)


class TestAuthorizedAdapterGating(unittest.TestCase):
    def test_not_approved_is_refused_even_with_a_source(self):
        adapter = hospital.AuthorizedTextAdapter(
            approved=False,
            source_id="ward-announcements",
            source_name="Ward announcements",
        )
        self.assertFalse(adapter.available())
        with self.assertRaises(hospital.HospitalError) as caught:
            adapter.parse({"text": "Token 47"})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("not approved", str(caught.exception))

    def test_approved_without_a_configured_source_is_unavailable(self):
        adapter = hospital.AuthorizedTextAdapter(
            approved=True, source_id="", source_name=""
        )
        self.assertFalse(adapter.available())
        with self.assertRaises(hospital.HospitalError) as caught:
            adapter.parse({"text": "Token 47"})
        self.assertEqual(caught.exception.status_code, 503)

    def test_approved_source_returns_real_data_without_network_calls(self):
        adapter = hospital.AuthorizedTextAdapter(
            approved=True,
            source_id="ward-announcements",
            source_name="Ward announcements",
        )
        self.assertTrue(adapter.available())
        announcement = adapter.parse({
            "text": "Token 47, please come to room 12.",
            "source_id": "ward-announcements",
            "real_data": False,
        })
        self.assertTrue(announcement.real_data)
        self.assertEqual(announcement.source_id, "ward-announcements")
        self.assertEqual(announcement.source_name, "Ward announcements")


class TestHospitalRoutes(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.base_config = {
            "HOSPITAL_DATA_APPROVED": False,
            "HOSPITAL_SOURCE_ID": "",
            "HOSPITAL_SOURCE_NAME": "",
        }

    def config(self, **overrides):
        values = dict(self.base_config)
        values.update(overrides)
        return patch.dict(server.app.config, values)

    def test_sources_lists_synthetic_and_gated_authorized_source(self):
        with self.config(
            HOSPITAL_SOURCE_ID="ward-announcements",
            HOSPITAL_SOURCE_NAME="Ward announcements",
        ):
            response = self.client.get("/api/hospital/sources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = response.get_json()
        self.assertFalse(body["real_data_approved"])
        self.assertEqual(body["persistence"], "none")
        self.assertEqual(body["network_connections"], "none")
        sources = {source["source_id"]: source for source in body["sources"]}
        self.assertTrue(sources["synthetic-demo"]["available"])
        self.assertFalse(sources["ward-announcements"]["available"])
        self.assertTrue(sources["ward-announcements"]["requires_approval"])
        text = response.get_data(as_text=True).lower()
        self.assertNotIn("api_key", text)
        self.assertNotIn("approval_token", text)

    def test_synthetic_announcement_route_is_safe_and_stateless(self):
        with self.config():
            response = self.client.post(
                "/api/hospital/announcement",
                json={"source_id": "synthetic-demo", "text": "Token 47 at Counter 3."},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["stored"])
        self.assertEqual(body["provider_calls"], 0)
        self.assertFalse(body["announcement"]["real_data"])
        self.assertEqual(body["announcement"]["source_id"], "synthetic-demo")

    def test_unapproved_real_source_is_refused(self):
        with self.config(
            HOSPITAL_SOURCE_ID="ward-announcements",
            HOSPITAL_SOURCE_NAME="Ward announcements",
        ):
            response = self.client.post(
                "/api/hospital/announcement",
                json={"source_id": "ward-announcements", "text": "Token 47"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not approved", response.get_json()["error"])

    def test_unknown_source_is_404(self):
        with self.config():
            response = self.client.post(
                "/api/hospital/announcement",
                json={"source_id": "not-a-real-source", "text": "Token 47"},
            )
        self.assertEqual(response.status_code, 404)

    def test_missing_json_and_source_id_are_400(self):
        with self.config():
            self.assertEqual(
                self.client.post("/api/hospital/announcement").status_code, 400
            )
            self.assertEqual(
                self.client.post(
                    "/api/hospital/announcement", json={"text": "Token 47"}
                ).status_code,
                400,
            )

    def test_approved_source_works_and_is_labelled_real_data(self):
        with self.config(
            HOSPITAL_DATA_APPROVED=True,
            HOSPITAL_SOURCE_ID="ward-announcements",
            HOSPITAL_SOURCE_NAME="Ward announcements",
        ):
            response = self.client.post(
                "/api/hospital/announcement",
                json={"source_id": "ward-announcements", "text": "Token 47"},
            )
        self.assertEqual(response.status_code, 200)
        announcement = response.get_json()["announcement"]
        self.assertTrue(announcement["real_data"])
        self.assertEqual(announcement["source_name"], "Ward announcements")


if __name__ == "__main__":
    unittest.main()
