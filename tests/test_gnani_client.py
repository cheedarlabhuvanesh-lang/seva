from __future__ import annotations

import tempfile
import unittest
import wave
import os
from pathlib import Path
from unittest.mock import patch

import requests

from gnani import GnaniError
from gnani.client import GnaniClient, STT_PATH, load_dotenv


class TestProductionEnvironment(unittest.TestCase):
    def test_dotenv_is_not_loaded_in_production(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("SEVA_TEST_ONLY_SECRET=placeholder\n", encoding="utf-8")
            with patch.dict(os.environ, {"SEVA_ENV": "production"}, clear=False):
                os.environ.pop("SEVA_TEST_ONLY_SECRET", None)
                load_dotenv(env_file)
                self.assertNotIn("SEVA_TEST_ONLY_SECRET", os.environ)

    def test_persistent_data_directory_controls_default_cache_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "SEVA_ENV": "production",
                "SEVA_DATA_DIR": temp_dir,
            }, clear=False):
                client = GnaniClient(api_key="test-key")
            self.assertEqual(client.cache_dir, Path(temp_dir) / "cache")
            self.assertEqual(client.usage_log, Path(temp_dir) / "usage.jsonl")


class TestGnaniSTTRequest(unittest.TestCase):
    def test_wav_upload_has_documented_mime_type_and_original_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "speech.wav"
            samples = b"\x01\x00" * 1600
            with wave.open(str(audio_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(samples)
            expected_audio = audio_path.read_bytes()

            client = GnaniClient(
                api_key="test-key",
                cache_dir=root / "cache",
                usage_log=root / "usage.jsonl",
            )
            observed = {}

            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return {"success": True, "transcript": "hello"}

            def fake_post(url, *, headers, data, files, timeout):
                filename, stream, content_type = files["audio_file"]
                observed.update({
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "filename": filename,
                    "audio_bytes": stream.read() if hasattr(stream, "read") else stream,
                    "content_type": content_type,
                    "timeout": timeout,
                })
                return Response()

            with patch("gnani.client.requests.post", side_effect=fake_post):
                transcript = client.transcribe(
                    audio_path, language_code="indic-auto",
                    model="prisma-v3.0",
                )

        self.assertEqual(transcript, "hello")
        self.assertEqual(observed["url"], f"{client.base_url}{STT_PATH}")
        self.assertEqual(observed["headers"]["X-API-Key-ID"], "test-key")
        self.assertEqual(observed["filename"], "speech.wav")
        self.assertEqual(observed["audio_bytes"], expected_audio)
        self.assertEqual(observed["content_type"], "audio/wav")
        self.assertEqual(observed["data"]["model"], "prisma-v3.0")
        self.assertEqual(observed["data"]["language_code"], "indic-auto")
        self.assertEqual(observed["data"]["format"], "transcribe")


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 1600)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}
        self.text = ""
        self.content = b""

    def json(self):
        return self._payload


class TestGnaniRetryPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "speech.wav"
        write_wav(self.audio)
        self.client = GnaniClient(
            api_key="test-key",
            cache_dir=self.root / "cache",
            usage_log=self.root / "usage.jsonl",
            max_retries=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_503_is_retried_once_then_succeeds(self):
        calls = []
        responses = [
            FakeResponse(503, {"success": False, "error": {"type": "BUSY"}}),
            FakeResponse(200, {"success": True, "transcript": "hello"}),
        ]

        def fake_post(*args, **kwargs):
            calls.append(args[0])
            return responses.pop(0)

        with patch("gnani.client.requests.post", side_effect=fake_post), \
             patch("gnani.client.time.sleep", return_value=None):
            transcript = self.client.transcribe(self.audio)
        self.assertEqual(transcript, "hello")
        self.assertEqual(len(calls), 2)

    def test_429_and_5xx_are_retryable_but_400_is_not(self):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(args[0])
            return FakeResponse(400, {"success": True})

        with patch("gnani.client.requests.post", side_effect=fake_post), \
             patch("gnani.client.time.sleep", return_value=None):
            with self.assertRaises(GnaniError):
                self.client.transcribe(self.audio)
        self.assertEqual(len(calls), 1)

    def test_timeout_is_never_retried(self):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(args[0])
            raise requests.Timeout("simulated timeout")

        with patch("gnani.client.requests.post", side_effect=fake_post), \
             patch("gnani.client.time.sleep", return_value=None):
            with self.assertRaises(GnaniError) as caught:
                self.client.transcribe(self.audio)
        self.assertEqual(len(calls), 1)
        self.assertIn("did not respond in time", str(caught.exception))

    def test_retry_cap_is_configurable_and_bounded(self):
        with patch.dict(os.environ, {"GNANI_MAX_RETRIES": "99"}, clear=False):
            client = GnaniClient(
                api_key="test-key",
                cache_dir=self.root / "cache",
                usage_log=self.root / "usage.jsonl",
            )
        self.assertEqual(client.max_retries, 2)
        with patch.dict(os.environ, {"GNANI_MAX_RETRIES": "0"}, clear=False):
            client = GnaniClient(
                api_key="test-key",
                cache_dir=self.root / "cache",
                usage_log=self.root / "usage.jsonl",
            )
        self.assertEqual(client.max_retries, 0)

    def test_is_configured_returns_only_a_boolean(self):
        with patch("gnani.client.load_dotenv", return_value=None):
            with patch.dict(os.environ, {"GNANI_API_KEY": "secret-value"}, clear=False):
                self.assertIs(GnaniClient.is_configured(), True)
            with patch.dict(os.environ, {"GNANI_API_KEY": ""}, clear=False):
                self.assertIs(GnaniClient.is_configured(), False)


if __name__ == "__main__":
    unittest.main()
