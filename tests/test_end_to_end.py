#!/usr/bin/env python3
"""End-to-end Flask contract tests for the watch, ask, TTS and replay flows.

No microphone, network or real API key is used. The Gnani client and Nova are
replaced with fakes that behave like the real boundaries.
"""

from __future__ import annotations

import json
import base64
import os
import sys
import tempfile
import unittest
import uuid
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
        self.speak_calls.append({
            "text": text,
            "language": language,
            "voice": voice,
            "sample_rate": sample_rate,
        })
        Path(out_path).write_bytes(b"RIFF" + b"\0" * 64)


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


class TestEntryPoint(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_root_redirects_to_app(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/app/")

    def test_health_endpoint_is_minimal_and_has_security_headers(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn(
            "media-src 'self' blob:",
            response.headers["Content-Security-Policy"],
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "microphone=(), camera=(), geolocation=()",
        )
        self.assertNotIn("api_key", response.get_data(as_text=True).lower())

    def test_production_hides_development_only_endpoints_by_default(self):
        with patch.dict(server.app.config, {
            "SEVA_ENV": "production",
            "SERVER_MIC_ENABLED": False,
            "SIMULATE_ENABLED": False,
            "USAGE_API_ENABLED": False,
        }):
            mic = self.client.get("/api/mic/status", base_url="https://localhost")
            state = self.client.get("/api/watch/state", base_url="https://localhost")
            stop = self.client.post("/api/watch/stop", base_url="https://localhost")
            simulate = self.client.post("/api/watch/simulate", json={
                "kind": "hospital", "value": "47", "text": "Token 47, room 204",
            }, base_url="https://localhost")
            usage = self.client.get("/api/usage", base_url="https://localhost")
        self.assertEqual(mic.status_code, 404)
        self.assertEqual(state.status_code, 404)
        self.assertEqual(stop.status_code, 404)
        self.assertEqual(simulate.status_code, 404)
        self.assertEqual(usage.status_code, 404)

    def test_production_redirects_http_requests_to_https(self):
        with patch.dict(server.app.config, {"SEVA_ENV": "production"}):
            response = self.client.get("/app/")
            secure_response = self.client.get("/app/", base_url="https://localhost")
        self.assertEqual(response.status_code, 308)
        self.assertTrue(response.headers["Location"].startswith("https://"))
        self.assertEqual(secure_response.status_code, 200)

    def test_production_preview_requires_basic_auth_but_health_is_public(self):
        auth_header = "Basic " + base64.b64encode(
            b"demo:long-test-password"
        ).decode("ascii")
        with patch.dict(server.app.config, {
            "SEVA_ENV": "production",
            "PREVIEW_AUTH_REQUIRED": True,
        }), patch.dict("os.environ", {
            "SEVA_PREVIEW_USERNAME": "demo",
            "SEVA_PREVIEW_PASSWORD": "long-test-password",
        }):
            health = self.client.get("/healthz", base_url="https://localhost")
            denied = self.client.get("/app/", base_url="https://localhost")
            allowed = self.client.get(
                "/app/", base_url="https://localhost",
                headers={"Authorization": auth_header},
            )
        self.assertEqual(health.status_code, 200)
        self.assertEqual(denied.status_code, 401)
        self.assertIn("Basic", denied.headers["WWW-Authenticate"])
        self.assertEqual(allowed.status_code, 200)

    def test_production_preview_fails_closed_without_auth_configuration(self):
        with patch.dict(server.app.config, {
            "SEVA_ENV": "production",
            "PREVIEW_AUTH_REQUIRED": True,
        }), patch.dict("os.environ", {}, clear=False):
            os.environ.pop("SEVA_PREVIEW_USERNAME", None)
            os.environ.pop("SEVA_PREVIEW_PASSWORD", None)
            response = self.client.get("/app/", base_url="https://localhost")
        self.assertEqual(response.status_code, 503)

    def test_disabled_hospital_knowledge_has_no_facts(self):
        from app.schemes import SchemeBook

        book = SchemeBook(enabled=False)
        self.assertEqual(len(book), 0)
        self.assertEqual(book.search("hospital OPD timings"), [])

    def test_app_index_is_served(self):
        response = self.client.get("/app/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Seva", response.data)

    def test_api_mic_status_handles_sounddevice_list_result(self):
        class FakeSoundDevice:
            default = type("Default", (), {"device": [1, 3]})()

            @staticmethod
            def query_devices(kind=None):
                return [{"name": "USB Mic"}, {"name": "Built-in mic"}]

        with patch.dict(sys.modules, {"sounddevice": FakeSoundDevice}):
            response = self.client.get("/api/mic/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_manifest_starts_at_app(self):
        manifest = json.loads((ROOT / "web" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["start_url"], "/app/")
        self.assertEqual(manifest["scope"], "/app/")


class TestSpeechEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        with server.SPEECH_RATE_LOCK:
            server.SPEECH_RATE.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(server, "ALERT_AUDIO_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _text(self):
        return f"Token {uuid.uuid4().hex[:8]} called"

    def test_audio_route_does_not_serve_temporary_recordings(self):
        with patch.object(server, "send_from_directory", return_value="generated speech") as send:
            denied = self.client.get("/audio/q-12345678.wav")
            allowed = self.client.get("/audio/a-12345678.wav")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data, b"generated speech")
        send.assert_called_once_with(server.AUDIO_DIR, "a-12345678.wav")

    def test_alert_audio_route_only_serves_known_file_shapes(self):
        with patch.object(server, "send_from_directory", return_value="alert audio") as send:
            allowed = self.client.get("/alerts/alert-1700000000-abcdef.wav")
            bad_names = (
                "/alerts/whatever.wav",
                "/alerts/tts-not-a-hash.wav",
                "/alerts/..%2F..%2Fseva.db",
            )
            denied = [self.client.get(name) for name in bad_names]
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data, b"alert audio")
        self.assertEqual([response.status_code for response in denied], [404, 404, 404])
        send.assert_called_once_with(server.ALERT_AUDIO_DIR, "alert-1700000000-abcdef.wav")

    def test_speech_routes_are_rate_limited(self):
        fake = FakeClient()
        with patch.object(server, "SPEECH_RATE_LIMIT", 2), \
                patch.object(server, "get_client", return_value=fake):
            responses = [
                self.client.post("/tts", json={"text": self._text(), "language": "en-IN"})
                for _ in range(3)
            ]
        self.assertEqual([response.status_code for response in responses], [200, 200, 429])
        self.assertIn("too many speech requests", responses[2].get_json()["error"])

    def test_listen_deletes_recording_after_success_and_failure(self):
        class FakeSTTClient:
            def __init__(self, fail=False):
                self.fail = fail

            def transcribe(self, audio, language_code):
                if self.fail:
                    raise server.GnaniError("provider details must not leak")
                return "What are the hospital hours?"

        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp_dir:
                wav = Path(temp_dir) / "recording.wav"
                wav.write_bytes(b"temporary audio")
                with patch.object(server, "record", return_value=wav), \
                        patch.object(server, "get_client", return_value=FakeSTTClient(fail)), \
                        patch.object(server, "answer", return_value={"timings": {}}):
                    response = self.client.post("/api/listen", json={"seconds": 2})

                self.assertFalse(wav.exists())
                if fail:
                    self.assertEqual(response.status_code, 502)
                    self.assertNotIn("provider details", response.get_data(as_text=True))
                else:
                    self.assertEqual(response.status_code, 200)

    def test_listen_rejects_invalid_duration_before_recording(self):
        for seconds in ("NaN", 0, 21, "bad"):
            with self.subTest(seconds=seconds), \
                    patch.object(server, "record") as record:
                response = self.client.post("/api/listen", json={"seconds": seconds})
                self.assertEqual(response.status_code, 400)
                record.assert_not_called()

    def test_tts_synthesises_audio(self):
        fake = FakeClient()
        text = self._text()
        with patch.object(server, "get_client", return_value=fake):
            response = self.client.post("/tts", json={"text": text, "language": "en-IN"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertTrue(response.data.startswith(b"RIFF"))
        self.assertEqual(fake.speak_calls[0]["language"], "en-IN")

    def test_tts_is_cached_by_text_and_language(self):
        fake = FakeClient()
        text = self._text()
        with patch.object(server, "get_client", return_value=fake):
            first = self.client.post("/tts", json={"text": text, "language": "te-IN"})
            second = self.client.post("/tts", json={"text": text, "language": "te-IN"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(fake.speak_calls), 1)

    def test_replay_accepts_get_and_normalises_language(self):
        fake = FakeClient()
        text = self._text()
        with patch.object(server, "get_client", return_value=fake):
            response = self.client.get(
                "/api/replay", query_string={"text": text, "lang": "hi"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(fake.speak_calls[0]["language"], "hi-IN")

    def test_replay_accepts_post_json(self):
        fake = FakeClient()
        text = self._text()
        with patch.object(server, "get_client", return_value=fake):
            response = self.client.post(
                "/api/replay", json={"text": text, "language": "ta-IN"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.speak_calls[0]["language"], "ta-IN")

    def test_unsupported_language_is_rejected(self):
        response = self.client.post(
            "/tts", json={"text": "hello", "language": "xx-IN"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No voice is configured", response.get_json()["error"])

    def test_empty_and_too_long_text_are_rejected(self):
        empty = self.client.post("/tts", json={"text": "  ", "language": "en-IN"})
        long = self.client.post(
            "/tts", json={"text": "x" * (server.MAX_SPEECH_CHARS + 1), "language": "en-IN"}
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(long.status_code, 400)

    def test_voice_failure_is_a_clean_502(self):
        class BrokenClient:
            api_key = "test-key"

            def speak(self, *args, **kwargs):
                raise OSError("no voice")

        with patch.object(server, "get_client", return_value=BrokenClient()):
            response = self.client.post("/tts", json={"text": "hello", "language": "en-IN"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Voice synthesis failed", response.get_json()["error"])


class TestWatchFlow(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.addCleanup(self._reset_watch)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(server, "ALERT_AUDIO_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        with server.WATCH_LOCK:
            server.WATCH.update({
                "active": False,
                "alerts": [],
                "heard": 0,
                "error": None,
                "thread": None,
            })
            server._RECENT_ALERTS.clear()

    @staticmethod
    def _reset_watch():
        with server.WATCH_LOCK:
            server.WATCH["active"] = False
            server.WATCH["alerts"] = []
            server.WATCH["heard"] = 0
            server.WATCH["error"] = None
            server.WATCH["thread"] = None
            server._RECENT_ALERTS.clear()

    def test_start_state_stop_contract(self):
        fake = FakeClient()
        with patch.object(server, "get_client", return_value=fake), \
                patch.object(server.threading, "Thread", FakeThread):
            started = self.client.post("/api/watch/start", json={
                "kind": "hospital",
                "value": "A-47",
                "language": "te-IN",
            })
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.get_json(), {
            "ok": True, "kind": "hospital", "value": "A-47",
        })

        state = self.client.get("/api/watch/state").get_json()
        self.assertTrue(state["active"])
        self.assertEqual(state["kind"], "hospital")
        self.assertEqual(state["value"], "A-47")
        self.assertEqual(state["language"], "te-IN")
        self.assertFalse(state["debug"])
        for internal in ("thread", "key_found", "mic_device", "last_heard_text"):
            self.assertNotIn(internal, state)

        stopped = self.client.post("/api/watch/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(self.client.get("/api/watch/state").get_json()["active"])

    def test_start_rejects_bad_token_and_unknown_language(self):
        bad_token = self.client.post("/api/watch/start", json={
            "kind": "hospital", "value": "47A", "language": "en-IN",
        })
        bad_language = self.client.post("/api/watch/start", json={
            "kind": "hospital", "value": "47", "language": "xx-IN",
        })
        self.assertEqual(bad_token.status_code, 400)
        self.assertEqual(bad_language.status_code, 400)

    def test_start_reports_missing_speech_key(self):
        class NoKeyClient:
            api_key = None

        with patch.object(server, "get_client", return_value=NoKeyClient()):
            response = self.client.post("/api/watch/start", json={
                "kind": "hospital", "value": "47", "language": "en-IN",
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn("GNANI_API_KEY", response.get_json()["error"])

    def test_simulate_writes_alert_and_audio_url_to_state(self):
        fake = FakeClient()
        with patch.object(server, "get_client", return_value=fake):
            response = self.client.post("/api/watch/simulate", json={
                "kind": "hospital",
                "value": "47",
                "text": "Token number 47, room 203, cardiology department",
                "language": "en-IN",
                "speak": True,
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["verdict"], "mine")
        self.assertEqual(payload["token"], "47")
        self.assertEqual(payload["room"], "203")
        self.assertEqual(payload["specialist"], "cardiology")
        self.assertTrue(payload["certain"])
        self.assertTrue(payload["audio_url"].startswith("/alerts/"))
        self.assertNotIn("tts_error", payload)

        audio = self.client.get(payload["audio_url"])
        self.assertEqual(audio.status_code, 200)

        state = self.client.get("/api/watch/state").get_json()
        self.assertEqual(len(state["alerts"]), 1)
        self.assertEqual(state["alerts"][0]["audio_url"], payload["audio_url"])

    def test_simulate_rejects_oversized_transcript_and_bad_threshold(self):
        too_long = self.client.post("/api/watch/simulate", json={
            "kind": "hospital",
            "value": "47",
            "text": "x" * (server.MAX_TRANSCRIPT_CHARS + 1),
            "language": "en-IN",
        })
        bad_threshold = self.client.post("/api/watch/start", json={
            "kind": "hospital",
            "value": "47",
            "language": "en-IN",
            "trigger_db": "NaN",
        })
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(bad_threshold.status_code, 400)

    def test_simulate_unknown_transcript_does_not_alert(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "hospital",
            "value": "47",
            "text": "The canteen closes at four",
            "language": "en-IN",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["verdict"], "none")
        self.assertEqual(self.client.get("/api/watch/state").get_json()["alerts"], [])

    def test_repeated_matching_announcement_is_not_queued_twice(self):
        body = {
            "kind": "train",
            "value": "12739",
            "text": "Train 12739 arriving on platform 4",
            "language": "en-IN",
        }
        first = self.client.post("/api/watch/simulate", json=body)
        second = self.client.post("/api/watch/simulate", json=body)

        self.assertEqual(first.get_json()["verdict"], "mine")
        self.assertFalse(first.get_json()["duplicate"])
        self.assertEqual(second.get_json()["verdict"], "mine")
        self.assertTrue(second.get_json()["duplicate"])

        updated = self.client.post(
            "/api/watch/simulate",
            json={**body, "text": "Train 12739 arriving on platform 5"},
        )
        self.assertFalse(updated.get_json()["duplicate"])
        self.assertEqual(len(self.client.get("/api/watch/state").get_json()["alerts"]), 2)


class TestAskFlow(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        with server.ASK_RATE_LOCK:
            server.ASK_RATE.clear()

    def test_ask_returns_nova_answer_and_audio(self):
        with patch.object(server, "gemini_ask", return_value="Drink water."), \
                patch.object(server, "speak_answer", return_value="/audio/answer.wav"):
            response = self.client.post("/api/ask", json={
                "question": "What is diabetes?",
                "language": "en-IN",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer"], "Drink water.")
        self.assertEqual(payload["source"], "gemini")
        self.assertFalse(payload["fallback"])
        self.assertEqual(payload["audio"], "/audio/answer.wav")

    def test_gemini_failure_uses_local_fallback_with_http_200(self):
        with patch.object(server, "gemini_ask", side_effect=RuntimeError("down")), \
                patch.object(server, "speak_answer", return_value=None):
            response = self.client.post("/api/ask", json={
                "question": "When is OPD open?",
                "language": "en-IN",
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["fallback"])
        self.assertIn(payload["source"], ("hospital_faq", "fallback"))
        self.assertTrue(payload["answer"].strip())

    def test_empty_question_is_rejected(self):
        response = self.client.post("/api/ask", json={"question": "   "})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
