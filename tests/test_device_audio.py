"""Offline tests for the phone-microphone listening path.

No network, no real Gnani key, no real microphone. A fake speech client stands
in for the provider so the chunk route, temporary-file cleanup, shared matcher,
and duplicate suppression can be checked deterministically.
"""

from __future__ import annotations

import io
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import device_audio, server
from gnani import GnaniError


def make_wav(seconds: float = 1.0, rate: int = 16000,
             channels: int = 1, width: int = 2) -> bytes:
    frames = int(rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * frames * channels * width)
    return buffer.getvalue()


class FakeClient:
    api_key = "test-key"

    def __init__(self, transcript: str = "Token number 47, room 204"):
        self.transcript = transcript
        self.transcribe_calls: list[Path] = []
        self.spoken: list[tuple[str, str]] = []
        self.fail_speak = False

    def transcribe_multilingual(self, path):
        path = Path(path)
        self.transcribe_calls.append(path)
        return {
            "transcript": self.transcript,
            "detected_language_code": "en-IN",
        }

    def speak(self, text, language=None, voice=None, out_path=None):
        if self.fail_speak:
            raise GnaniError("synthetic synthesis failure")
        self.spoken.append((text, language or ""))
        Path(out_path).write_bytes(b"RIFF-test")
        return out_path


class TestDeviceSessions(unittest.TestCase):
    def setUp(self):
        device_audio.clear_sessions()

    def tearDown(self):
        device_audio.clear_sessions()

    def test_watch_validation_is_shared_with_the_matcher(self):
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.validate_watch("train", "123")
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.validate_watch("hospital", "not-a-token")
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.validate_watch("bus", "!!!")
        device_audio.validate_watch("train", "12739")
        device_audio.validate_watch("hospital", "A-47")
        device_audio.validate_watch("bus", "Tirupati")

    def test_invalid_language_is_refused_not_silently_english(self):
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.create_session(
                "hospital", "47", "xx-IN", client_ip="127.0.0.1"
            )

    def test_session_is_bound_to_the_client_address(self):
        session = device_audio.create_session(
            "hospital", "47", "en-IN", client_ip="127.0.0.1"
        )
        self.assertIs(
            device_audio.get_session(session.id, client_ip="127.0.0.1"),
            session,
        )
        self.assertIsNone(
            device_audio.get_session(session.id, client_ip="10.0.0.9")
        )
        self.assertFalse(
            device_audio.stop_session(session.id, client_ip="10.0.0.9")
        )
        self.assertTrue(
            device_audio.stop_session(session.id, client_ip="127.0.0.1")
        )

    def test_expired_session_is_removed(self):
        session = device_audio.create_session(
            "hospital", "47", "en-IN", client_ip="127.0.0.1"
        )
        session.last_seen = time.time() - device_audio.SESSION_TTL_SECONDS - 1
        self.assertIsNone(
            device_audio.get_session(session.id, client_ip="127.0.0.1")
        )
        self.assertIsNone(
            device_audio.get_session(session.id, client_ip="127.0.0.1")
        )


class TestTranscriptProcessing(unittest.TestCase):
    def setUp(self):
        device_audio.clear_sessions()

    def tearDown(self):
        device_audio.clear_sessions()

    def make(self, kind="hospital", value="47"):
        return device_audio.create_session(
            kind, value, "en-IN", client_ip="127.0.0.1"
        )

    def test_exact_match_produces_a_certain_alert(self):
        session = self.make()
        record = device_audio.process_transcript(
            session, "Token number 47, room 204"
        )
        self.assertEqual(record["verdict"], "mine")
        self.assertTrue(record["certain"])
        self.assertEqual(record["token"], "47")
        self.assertEqual(record["room"], "204")
        self.assertTrue(record["spoken"])
        self.assertFalse(record["duplicate"])
        self.assertEqual(session.heard, 1)

    def test_47_never_matches_847(self):
        session = self.make()
        record = device_audio.process_transcript(
            session, "Token number 847, room 204"
        )
        self.assertEqual(record["verdict"], "other")

    def test_repeated_transcript_is_suppressed(self):
        session = self.make()
        first = device_audio.process_transcript(
            session, "Token number 47, room 204"
        )
        second = device_audio.process_transcript(
            session, "Token number 47, room 204"
        )
        self.assertEqual(first["verdict"], "mine")
        self.assertEqual(second["verdict"], "duplicate")
        self.assertTrue(second["duplicate"])
        self.assertEqual(session.duplicate_chunks, 1)

    def test_same_alert_from_different_words_is_suppressed(self):
        session = self.make()
        first = device_audio.process_transcript(
            session, "Token number 47, room 204"
        )
        second = device_audio.process_transcript(
            session, "Room 204 for token 47"
        )
        self.assertEqual(first["verdict"], "mine")
        self.assertEqual(second["verdict"], "mine")
        self.assertTrue(second["duplicate"])

    def test_token_without_room_waits_then_becomes_missing(self):
        session = self.make()
        pending = device_audio.process_transcript(session, "Token number 47")
        self.assertEqual(pending["verdict"], "missing_pending")
        self.assertTrue(pending["pending"])
        self.assertIsNone(device_audio.check_pending(session))

        session.pending_missing["started_at"] = (
            time.time() - device_audio.PENDING_MISSING_SECONDS - 1
        )
        missing = device_audio.check_pending(session)
        self.assertIsNotNone(missing)
        self.assertEqual(missing["verdict"], "missing")
        self.assertFalse(missing["certain"])
        self.assertFalse(missing["duplicate"])
        self.assertTrue(missing["spoken"])
        self.assertIsNone(device_audio.check_pending(session))

    def test_missing_uses_the_watched_token_when_extraction_is_empty(self):
        session = self.make(value="A-47")
        pending = device_audio.process_transcript(session, "Token number A-47")
        self.assertEqual(pending["verdict"], "missing_pending")
        session.pending_missing["started_at"] = (
            time.time() - device_audio.PENDING_MISSING_SECONDS - 1
        )
        missing = device_audio.check_pending(session)
        self.assertEqual(missing["token"], "A-47")

    def test_room_heard_after_pending_cancels_the_missing_alert(self):
        session = self.make()
        pending = device_audio.process_transcript(session, "Token number 47")
        self.assertEqual(pending["verdict"], "missing_pending")
        record = device_audio.process_transcript(
            session, "Token 47, room 204"
        )
        self.assertEqual(record["verdict"], "mine")
        self.assertIsNone(device_audio.check_pending(session))

    def test_unrelated_speech_is_none(self):
        session = self.make()
        record = device_audio.process_transcript(
            session, "The cafeteria closes at eight"
        )
        self.assertEqual(record["verdict"], "none")
        self.assertEqual(record["token"], None)


class TestChunkValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_wav_is_transcribed_and_deleted(self):
        client = FakeClient()
        result = device_audio.transcribe_chunk(
            client, make_wav(1.0), self.dir
        )
        self.assertEqual(result["transcript"], "Token number 47, room 204")
        self.assertEqual(len(client.transcribe_calls), 1)
        self.assertFalse(client.transcribe_calls[0].exists())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_provider_failure_still_deletes_the_temporary_file(self):
        class Broken(FakeClient):
            def transcribe_multilingual(self, path):
                self.transcribe_calls.append(Path(path))
                raise GnaniError("provider down")

        with self.assertRaises(GnaniError):
            device_audio.transcribe_chunk(Broken(), make_wav(1.0), self.dir)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_malformed_and_unsupported_wav_files_are_refused(self):
        client = FakeClient()
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.transcribe_chunk(client, b"not a wav", self.dir)
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.transcribe_chunk(
                client, make_wav(1.0, rate=44100), self.dir
            )
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.transcribe_chunk(
                client, make_wav(1.0, channels=2), self.dir
            )
        with self.assertRaises(device_audio.DeviceAudioError):
            device_audio.transcribe_chunk(
                client, make_wav(0.05), self.dir
            )
        self.assertEqual(list(self.dir.iterdir()), [])


class TestDeviceRoutes(unittest.TestCase):
    def setUp(self):
        device_audio.clear_sessions()
        with server.DEVICE_RATE_LOCK:
            server.DEVICE_RATE.clear()
        self.client = server.app.test_client()
        self.fake = FakeClient()
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        (self.temp_path / "alerts").mkdir()
        self.patches = [
            patch.dict(server.app.config, {"DEVICE_MIC_ENABLED": True}),
            patch.object(server, "get_client", return_value=self.fake),
            patch.object(server, "DEVICE_TEMP_DIR", self.temp_path / "chunks"),
            patch.object(server, "ALERT_AUDIO_DIR", self.temp_path / "alerts"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        device_audio.clear_sessions()
        with server.DEVICE_RATE_LOCK:
            server.DEVICE_RATE.clear()
        self.temp.cleanup()

    def create(self, kind="hospital", value="47"):
        response = self.client.post("/api/device/session", json={
            "kind": kind,
            "value": value,
            "language": "en-IN",
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def send(self, session_id, data=None):
        return self.client.post(
            "/api/device/chunk",
            data={
                "session_id": session_id,
                "audio": (io.BytesIO(data if data is not None else make_wav(1.0)),
                          "chunk.wav"),
            },
            content_type="multipart/form-data",
        )

    def test_capabilities_are_explicit_about_audio_transmission(self):
        response = self.client.get("/api/device/capabilities")
        body = response.get_json()
        self.assertTrue(body["device_microphone"])
        self.assertTrue(body["transmits_audio"])
        self.assertEqual(body["audio_provider"], "gnani")
        self.assertFalse(body["background_listening"])
        self.assertEqual(body["chunk_seconds"], 6)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_provider_status_exposes_booleans_not_secrets(self):
        with patch.object(server.GnaniClient, "is_configured", return_value=False), \
             patch.object(server.gemini, "is_configured", return_value=False):
            response = self.client.get("/api/providers")
        body = response.get_json()
        self.assertFalse(body["gnani"]["configured"])
        self.assertFalse(body["gemini"]["configured"])
        self.assertTrue(body["device_microphone"]["enabled"])
        self.assertFalse(body["hospital_data"]["approved"])
        text = response.get_data(as_text=True).lower()
        self.assertNotIn("test-key", text)
        self.assertNotIn("api_key", text)

    def test_device_script_is_served_for_the_browser(self):
        response = self.client.get("/app/device-listener.js")
        self.assertEqual(response.status_code, 200)
        source = response.get_data(as_text=True)
        self.assertIn("getUserMedia", source)
        self.assertIn("encodeWav", source)
        self.assertIn("sendBeacon", source)
        page = self.client.get("/app/listening.html").get_data(as_text=True)
        self.assertIn("device-listener.js", page)
        self.assertIn("i18n-device.js", page)

    def test_oversized_audio_chunk_is_refused_before_storage(self):
        session = self.create()
        response = self.send(session["session_id"], make_wav(35.0))
        self.assertEqual(response.status_code, 413)
        self.assertFalse((self.temp_path / "chunks").exists())

    def test_session_chunk_state_and_stop(self):
        session = self.create()
        self.assertTrue(session["session_id"])
        self.assertEqual(session["audio"]["sample_rate"], 16000)
        self.assertTrue(session["audio"]["transmits_audio"])

        response = self.send(session["session_id"])
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["heard"])
        self.assertEqual(body["verdict"], "mine")
        self.assertEqual(body["alert"]["token"], "47")
        self.assertEqual(body["alert"]["room"], "204")
        self.assertTrue(body["alert"]["audio_url"].startswith("/alerts/"))
        self.assertEqual(body["alert"]["source"], "device_microphone")
        self.assertEqual(list((self.temp_path / "chunks").iterdir()), [])

        state = self.client.get(
            "/api/device/state", query_string={"session_id": session["session_id"]}
        )
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.get_json()["alert"]["id"], body["alert"]["id"])

        stop = self.client.post(
            "/api/device/stop", json={"session_id": session["session_id"]}
        )
        self.assertEqual(stop.status_code, 200)
        expired = self.client.get(
            "/api/device/state", query_string={"session_id": session["session_id"]}
        )
        self.assertEqual(expired.status_code, 410)

    def test_unknown_session_is_gone_not_a_server_error(self):
        response = self.send("unknown-session")
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()["code"], "session_expired")

    def test_malformed_wav_is_a_clean_400(self):
        session = self.create()
        response = self.send(session["session_id"], b"not a wav file")
        self.assertEqual(response.status_code, 400)
        self.assertIn("audio chunk", response.get_json()["error"])

    def test_missing_audio_and_missing_session_id_are_400(self):
        session = self.create()
        response = self.client.post(
            "/api/device/chunk", data={"session_id": session["session_id"]},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/device/chunk",
            data={"audio": (io.BytesIO(make_wav(1.0)), "chunk.wav")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_provider_key_refuses_to_create_a_session(self):
        self.fake.api_key = ""
        response = self.client.post("/api/device/session", json={
            "kind": "hospital", "value": "47", "language": "en-IN",
        })
        self.assertEqual(response.status_code, 503)
        with device_audio._lock():
            self.assertEqual(len(device_audio._SESSIONS), 0)

    def test_tts_failure_keeps_the_result_and_reports_it(self):
        self.fake.fail_speak = True
        session = self.create()
        body = self.send(session["session_id"]).get_json()
        self.assertEqual(body["alert"]["verdict"], "mine")
        self.assertIsNone(body["alert"]["audio_url"])
        self.assertIn("Voice audio could not be generated", body["alert"]["tts_error"])

    def test_rate_limit_returns_429(self):
        with patch.object(server, "DEVICE_RATE_LIMIT", 2):
            self.create()
            self.create()
            response = self.client.post("/api/device/session", json={
                "kind": "hospital", "value": "47", "language": "en-IN",
            })
        self.assertEqual(response.status_code, 429)

    def test_state_emits_pending_missing_after_the_window(self):
        session = self.create()
        pending = self.send(session["session_id"], make_wav(1.0)).get_json()
        # The fake transcript still has a room, so make the next one roomless.
        self.fake.transcript = "Token number 47"
        second = self.send(session["session_id"]).get_json()
        self.assertEqual(second["verdict"], "missing_pending")
        with device_audio._lock():
            stored = device_audio._SESSIONS[session["session_id"]]
            stored.pending_missing["started_at"] = (
                time.time() - device_audio.PENDING_MISSING_SECONDS - 1
            )
        state = self.client.get(
            "/api/device/state", query_string={"session_id": session["session_id"]}
        ).get_json()
        self.assertEqual(state["alert"]["verdict"], "missing")
        self.assertEqual(state["alert"]["token"], "47")


if __name__ == "__main__":
    unittest.main()
