from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import server

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


class TestDeviceUIContract(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def page(self, name: str) -> str:
        response = self.client.get(f"/app/{name}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_device_scripts_are_loaded_in_dependency_order(self):
        page = self.page("listening.html")
        device = page.index('src="device-listener.js"')
        device_i18n = page.index('src="i18n-device.js"')
        page_i18n = page.index('src="i18n.js"')
        self.assertLess(device, device_i18n)
        self.assertLess(device_i18n, page_i18n)

    def test_device_path_has_a_legacy_fallback_only_when_the_script_is_missing(self):
        page = self.page("listening.html")
        self.assertIn("if (window.SevaDeviceAudio) return;", page)
        self.assertIn("window.SevaDeviceAudio.create", page)
        self.assertIn("Tap to start", page)

    def test_device_listener_captures_resamples_and_encodes_wav(self):
        source = self.page("device-listener.js")
        self.assertIn("TARGET_RATE = 16000", source)
        self.assertIn("getUserMedia", source)
        self.assertIn("encodeWav", source)
        self.assertIn("resampleLinear", source)
        self.assertIn("sendBeacon", source)
        self.assertNotIn("MediaRecorder", source)
        self.assertNotIn(".webm", source)

    def test_device_strings_exist_for_every_supported_language(self):
        source = (WEB / "i18n-device.js").read_text(encoding="utf-8")
        languages = re.findall(r"^    ([a-z]{2}): \{", source, flags=re.MULTILINE)
        self.assertEqual(
            sorted(languages),
            sorted(["te", "hi", "ta", "kn", "ml", "mr", "bn", "gu", "pa"]),
        )
        self.assertGreaterEqual(source.count('"Tap to start":'), 9)
        self.assertGreaterEqual(source.count('"Allow microphone":'), 9)

    def test_privacy_copy_discloses_external_recognition_and_retention(self):
        page = self.page("privacy.html")
        self.assertIn("Gnani", page)
        self.assertIn("deleted after processing", page)
        self.assertIn("microphone stops", page)
        self.assertIn("in-memory", page)

    def test_help_copy_does_not_claim_background_browser_listening(self):
        page = self.page("help.html")
        self.assertIn("Reliable background listening requires an Android app", page)
        self.assertIn("Closing the tab", page)
        self.assertNotIn("listens in the background", page.lower())

    def test_listen_page_discloses_short_clip_upload(self):
        page = self.page("listen.html")
        self.assertIn("short audio clips", page)
        self.assertIn("speech provider", page)
        self.assertIn('src="i18n-device.js"', page)

    def test_device_capture_only_test_mode_is_removed(self):
        source = self.page("device-listener.js")
        self.assertNotIn("createTest", source)
        self.assertNotIn("captureOnly", source)
        self.assertNotIn("test_listening", source)
        self.assertNotIn("sound_detected", source)
        page = self.page("listening.html")
        self.assertNotIn('id="mic-test"', page)
        self.assertNotIn("Test microphone only", page)


if __name__ == "__main__":
    unittest.main()
