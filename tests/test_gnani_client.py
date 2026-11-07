from __future__ import annotations

import tempfile
import unittest
import wave
import os
from pathlib import Path
from unittest.mock import patch

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
                    "audio_bytes": stream.read(),
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


if __name__ == "__main__":
    unittest.main()
