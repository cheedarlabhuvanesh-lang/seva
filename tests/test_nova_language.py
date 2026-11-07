#!/usr/bin/env python3
"""Network-free tests for Nova's selected-language answer + TTS flow."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import gemini, server
from gnani import GnaniError


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError("simulated provider error")

    def json(self):
        if self.status_code >= 400:
            return {"error": {"message": "simulated provider error"}}
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


class ScriptedSession:
    """Return one scripted answer per POST and record the request bodies."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        text = self.texts.pop(0) if self.texts else ""
        return FakeResponse(text)

    def get(self, url, **kwargs):  # pragma: no cover - not used by these tests
        return FakeResponse("")


TELUGU_ANSWER = "\u0c28\u0c2e\u0c38\u0c4d\u0c24\u0c47! \u0c07\u0c26\u0c3f \u0c24\u0c46\u0c32\u0c41\u0c17\u0c41 \u0c1c\u0c35\u0c3e\u0c2c\u0c41."
HINDI_ANSWER = "\u0928\u092e\u0938\u094d\u0924\u0947! \u092f\u0939 \u0939\u093f\u0902\u0926\u0940 \u091c\u0935\u093e\u092c \u0939\u0948\u0964"
TAMIL_ANSWER = "\u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd! \u0b87\u0ba4\u0bc1 \u0ba4\u0bae\u0bbf\u0bb4\u0bcd \u0baa\u0ba4\u0bbf\u0bb2\u0bcd."
KANNADA_ANSWER = "\u0ca8\u0cae\u0cb8\u0ccd\u0c95\u0cbe\u0cb0! \u0c87\u0ca6\u0cc1 \u0c95\u0ca8\u0ccd\u0ca8\u0ca1 \u0c89\u0ca4\u0ccd\u0ca4\u0cb0."
PUNJABI_ANSWER = "\u0a38\u0a24\u0a4d\u0a38\u0a30\u0a3f\u0a06\u0a15\u0a3e\u0a32! \u0a07\u0a39 \u0a2a\u0a70\u0a1c\u0a3e\u0a2c\u0a40 \u0a1c\u0a35\u0a3e\u0a2c \u0a39\u0a48\u0a06."


class TestLanguageNormalisation(unittest.TestCase):
    def test_base_and_region_codes_become_canonical(self):
        self.assertEqual(gemini.normalize_language("te"), "te-IN")
        self.assertEqual(gemini.normalize_language("te-IN"), "te-IN")
        self.assertEqual(gemini.normalize_language("TE_in"), "te-IN")
        self.assertEqual(gemini.normalize_language("hi"), "hi-IN")
        self.assertEqual(gemini.normalize_language(""), "en-IN")
        self.assertIsNone(gemini.normalize_language("xx-IN"))
        self.assertIsNone(gemini.normalize_language("klingon"))

    def test_language_names_are_human_readable(self):
        self.assertEqual(gemini.language_name("te"), "Telugu")
        self.assertEqual(gemini.language_name("ta-IN"), "Tamil")
        self.assertEqual(gemini.language_name(None), "English")

    def test_script_check_accepts_native_scripts_only(self):
        cases = {
            "te-IN": TELUGU_ANSWER,
            "hi-IN": HINDI_ANSWER,
            "ta-IN": TAMIL_ANSWER,
            "kn-IN": KANNADA_ANSWER,
            "pa-IN": PUNJABI_ANSWER,
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                self.assertTrue(gemini._looks_like_language(text, code))
                self.assertFalse(gemini._looks_like_language("This is English only.", code))
        self.assertTrue(gemini._looks_like_language("This is English only.", "en-IN"))


class TestGeminiLanguageGuarantee(unittest.TestCase):
    def test_english_reply_is_translated_before_tts(self):
        session = ScriptedSession("This is four.", TELUGU_ANSWER)
        result = gemini.ask(
            "What is 2 + 2?", "te-IN", api_key="test-key",
            model="gemini-test", session=session,
        )
        self.assertEqual(result, TELUGU_ANSWER)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("Telugu", session.calls[0]["json"]["systemInstruction"]["parts"][0]["text"])
        self.assertIn("Translate", session.calls[1]["json"]["systemInstruction"]["parts"][0]["text"])
        self.assertNotIn("test-key", str(session.calls[0]["json"]))

    def test_native_reply_is_not_translated_again(self):
        session = ScriptedSession(TELUGU_ANSWER)
        result = gemini.ask(
            "What is Seva?", "te-IN", api_key="test-key",
            model="gemini-test", session=session,
        )
        self.assertEqual(result, TELUGU_ANSWER)
        self.assertEqual(len(session.calls), 1)

    def test_translation_failure_never_returns_english(self):
        class FailingSecondPost(ScriptedSession):
            def post(self, url, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return FakeResponse("This is an English answer.")
                return FakeResponse(status=401)

        session = FailingSecondPost()
        with self.assertRaises(gemini.GeminiError) as caught:
            gemini.ask(
                "What is Seva?", "ta-IN", api_key="test-key",
                model="gemini-test", session=session,
            )
        self.assertIn("selected language", str(caught.exception))


class TestLocalizedFallbacks(unittest.TestCase):
    def test_every_language_has_its_own_fallback(self):
        english = server.FALLBACK_MESSAGES["en"]
        for code in server.FALLBACK_MESSAGES:
            with self.subTest(code=code):
                message = server._localized_fallback(f"{code}-IN")
                self.assertTrue(message.strip())
                if code != "en":
                    self.assertNotEqual(message, english)

    def test_non_english_fallback_never_contains_english_hospital_facts(self):
        match = {
            "name_en": "OPD Timings",
            "name_te": "\u0c13\u0c2a\u0c40\u0c21\u0c40",
            "benefit_te": "OPD runs Monday to Saturday.",
            "how_te": "Come before 11.",
            "url": "https://example.test",
        }
        for code in ("te-IN", "hi-IN", "ta-IN", "kn-IN", "ml-IN",
                     "mr-IN", "bn-IN", "gu-IN", "pa-IN"):
            with self.subTest(code=code):
                message = server._local_fallback([match], code)
                self.assertNotIn("OPD runs", message)
                self.assertEqual(message, server.FALLBACK_MESSAGES[code.split("-")[0]])

    def test_english_still_receives_hospital_facts(self):
        match = {
            "name_en": "OPD Timings",
            "name_te": "\u0c13\u0c2a\u0c40\u0c21\u0c40",
            "benefit_te": "OPD runs Monday to Saturday.",
            "how_te": "Come before 11.",
            "url": "https://example.test",
        }
        message = server._local_fallback([match], "en-IN")
        self.assertIn("local hospital information", message.lower())
        self.assertIn("OPD runs Monday", message)


class TestAskEndpointLanguage(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        with server.ASK_RATE_LOCK:
            server.ASK_RATE.clear()

    def tearDown(self):
        with server.ASK_RATE_LOCK:
            server.ASK_RATE.clear()

    def test_base_code_is_normalised_and_used_for_text_and_audio(self):
        captured = {}

        def fake_gemini(question, language, context, **kwargs):
            captured["gemini"] = language
            return TELUGU_ANSWER

        def fake_speak(text, language):
            captured["tts"] = language
            return "/audio/test.wav"

        with patch.object(server, "gemini_ask", side_effect=fake_gemini), \
                patch.object(server, "speak_answer", side_effect=fake_speak):
            response = self.client.post("/api/ask", json={
                "question": "What is Seva?", "language": "te",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(captured["gemini"], "te-IN")
        self.assertEqual(captured["tts"], "te-IN")
        self.assertEqual(payload["language"], "te-IN")
        self.assertEqual(payload["answer_language"], "te-IN")
        self.assertEqual(payload["audio"], "/audio/test.wav")

    def test_unsupported_language_is_a_clear_400_not_english(self):
        response = self.client.post("/api/ask", json={
            "question": "What is Seva?", "language": "xx-IN",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("No voice is configured", response.get_json()["error"])

    def test_voice_failure_is_reported_not_hidden(self):
        with patch.object(server, "gemini_ask", return_value=TAMIL_ANSWER), \
                patch.object(server, "speak_answer",
                             side_effect=GnaniError("voice service down")):
            response = self.client.post("/api/ask", json={
                "question": "What is Seva?", "language": "ta-IN",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload["audio"])
        self.assertIn("voice service down", payload["audio_error"])
        self.assertEqual(payload["language"], "ta-IN")


class FakeSpeechClient:
    tts_model = "timbre-v3.0"

    def __init__(self):
        self.calls = []

    def speak(self, text, language, voice, sample_rate=16000, out_path=None):
        self.calls.append({
            "text": text, "language": language, "voice": voice, "out_path": Path(out_path),
        })
        Path(out_path).write_bytes(b"RIFF-test")
        return Path(out_path)


class TestVoiceSelection(unittest.TestCase):
    def test_v3_uses_configured_chetaina_voice_for_every_language(self):
        fake = FakeSpeechClient()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(server, "get_client", return_value=fake), \
                patch.object(server, "AUDIO_DIR", Path(tmp)), \
                patch.dict(server.app.config, {"VOICE": "Chetana"}):
            url = server.speak_answer(TELUGU_ANSWER, "te-IN")

        self.assertTrue(url.startswith("/audio/"))
        self.assertEqual(fake.calls[0]["language"], "te-IN")
        self.assertEqual(fake.calls[0]["voice"], "Chetana")

    def test_v25_model_uses_a_language_appropriate_voice(self):
        fake = FakeSpeechClient()
        fake.tts_model = "timbre-v2.5"
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(server, "get_client", return_value=fake), \
                patch.object(server, "AUDIO_DIR", Path(tmp)), \
                patch.dict(server.app.config, {"VOICE": "Suhana"}):
            server.speak_answer(HINDI_ANSWER, "hi-IN")

        # Suhana is a Telugu v2.5 voice and cannot speak Hindi; the Hindi
        # default (Vikrant) must be used instead of failing or speaking Telugu.
        self.assertEqual(fake.calls[0]["language"], "hi-IN")
        self.assertEqual(fake.calls[0]["voice"], "Vikrant")


class TestSpeechCleanup(unittest.TestCase):
    def test_markdown_is_stripped_but_urls_and_identifiers_are_kept(self):
        cleaned = server._for_speech(
            "**Hello** `my_var` https://example.test/a_b\n- first item"
        )
        self.assertEqual(cleaned, "Hello my_var https://example.test/a_b first item")


if __name__ == "__main__":
    unittest.main(verbosity=2)
