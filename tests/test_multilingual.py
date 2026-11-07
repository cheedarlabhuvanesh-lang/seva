#!/usr/bin/env python3
"""Multilingual listening, hospital, train-name and specialist tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import server
from app.extract import (
    extract,
    find_platform,
    find_room,
    find_specialist,
    find_token,
    find_train_name,
    find_train_no,
    token_key,
    token_matches,
)
from app.matcher import match_announcement, match_hospital, normalize_text
from app.watch import Watch


def clean(value: str | None) -> str:
    return (value or "").replace("\u200c", "").replace("\u200d", "").casefold()


LANGUAGE_CASES = {
    "en-IN": {
        "text": (
            "Train number 12739 Godavari Express will arrive on platform 4. "
            "Token number 47, room 203, cardiology department."
        ),
        "train_name": "Godavari Express",
        "specialist": "cardiology",
    },
    "hi-IN": {
        "text": (
            "ट्रेन नंबर 12739 गोदावरी एक्सप्रेस प्लेटफॉर्म चार पर आएगी। "
            "टोकन नंबर सैंतालीस, कमरा दो सौ तीन, कार्डियोलॉजी विभाग।"
        ),
        "train_name": "गोदावरी एक्सप्रेस",
        "specialist": "कार्डियोलॉजी",
    },
    "te-IN": {
        "text": (
            "రైలు నంబర్ 12739 గోదావరి ఎక్స్‌ప్రెస్ ప్లాట్‌ఫారం నాలుగు. "
            "టోకెన్ నంబర్ నలభై ఏడు, గది రెండు వందల మూడు, కార్డియాలజీ విభాగం."
        ),
        "train_name": "గోదావరి ఎక్స్‌ప్రెస్",
        "specialist": "కార్డియాలజీ",
    },
    "ta-IN": {
        "text": (
            "ரயில் எண் 12739 கோதாவரி எக்ஸ்பிரஸ் நடைமேடை நான்கு. "
            "டோக்கன் எண் நாற்பத்து ஏழு, அறை 203, கார்டியாலஜி துறை."
        ),
        "train_name": "கோதாவரி எக்ஸ்பிரஸ்",
        "specialist": "கார்டியாலஜி",
    },
    "kn-IN": {
        "text": (
            "ರೈಲು ಸಂಖ್ಯೆ 12739 ಗೋದಾವರಿ ಎಕ್ಸ್‌ಪ್ರೆಸ್ ಪ್ಲಾಟ್‌ಫಾರ್ಮ್ ನಾಲ್ಕು. "
            "ಟೋಕನ್ ಸಂಖ್ಯೆ ನಲವತ್ತು ಏಳು, ಕೊಠಡಿ 203, ಕಾರ್ಡಿಯಾಲಜಿ ವಿಭಾಗ."
        ),
        "train_name": "ಗೋದಾವರಿ ಎಕ್ಸ್‌ಪ್ರೆಸ್",
        "specialist": "ಕಾರ್ಡಿಯಾಲಜಿ",
    },
    "ml-IN": {
        "text": (
            "ട്രെയിൻ നമ്പർ 12739 ഗോദാവരി എക്സ്പ്രസ് പ്ലാറ്റ്ഫോം നാല്. "
            "ടോക്കൺ നമ്പർ നാല്പത്തി ഏഴ്, മുറി 203, കാർഡിയോളജി വിഭാഗം."
        ),
        "train_name": "ഗോദാവരി എക്സ്പ്രസ്",
        "specialist": "കാർഡിയോളജി",
    },
    "mr-IN": {
        "text": (
            "ट्रेन क्रमांक १२७३९ गोदावरी एक्सप्रेस प्लॅटफॉर्म चार. "
            "टोकन क्रमांक ४७, खोली २०३, कार्डिओलॉजी विभाग."
        ),
        "train_name": "गोदावरी एक्सप्रेस",
        "specialist": "कार्डिओलॉजी",
    },
    "bn-IN": {
        "text": (
            "ট্রেন নম্বর ১২৭৩৯ গোদাবরী এক্সপ্রেস প্ল্যাটফর্ম চার. "
            "টোকেন নম্বর ৪৭, রুম ২০৩, কার্ডিওলজি বিভাগ."
        ),
        "train_name": "গোদাবরী এক্সপ্রেস",
        "specialist": "কার্ডিওলজি",
    },
    "gu-IN": {
        "text": (
            "ટ્રેન નંબર ૧૨૭૩૯ ગોદાવરી એક્સપ્રેસ પ્લેટફોર્મ ચાર. "
            "ટોકન નંબર ૪૭, રૂમ ૨૦૩, કાર્ડિયોલોજી વિભાગ."
        ),
        "train_name": "ગોદાવરી એક્સપ્રેસ",
        "specialist": "કાર્ડિયોલોજી",
    },
    "pa-IN": {
        "text": (
            "ਟ੍ਰੇਨ ਨੰਬਰ ੧੨੭੩੯ ਗੋਦਾਵਰੀ ਐਕਸਪ੍ਰੈਸ ਪਲੇਟਫਾਰਮ ਚਾਰ. "
            "ਟੋਕਨ ਨੰਬਰ ੪੭, ਕਮਰਾ ੨੦੩, ਕਾਰਡੀਓਲੋਜੀ ਵਿਭਾਗ."
        ),
        "train_name": "ਗੋਦਾਵਰੀ ਐਕਸਪ੍ਰੈਸ",
        "specialist": "ਕਾਰਡੀਓਲੋਜੀ",
    },
}


class TestEveryLanguage(unittest.TestCase):
    def test_each_language_extracts_the_same_announcement(self):
        for language, case in LANGUAGE_CASES.items():
            with self.subTest(language=language):
                slots = extract(case["text"])
                self.assertEqual(slots["train_no"], "12739")
                self.assertEqual(clean(slots["train_name"]), clean(case["train_name"]))
                self.assertEqual(slots["platform"], "4")
                self.assertEqual(slots["token"], "47")
                self.assertEqual(slots["room"], "203")
                self.assertEqual(slots["specialist"], case["specialist"])

    def test_each_language_matches_hospital_and_train(self):
        for language, case in LANGUAGE_CASES.items():
            with self.subTest(language=language):
                hospital = match_announcement("hospital", case["text"], "47")
                self.assertEqual(hospital["verdict"], "mine")
                self.assertEqual(hospital["room"], "203")
                self.assertEqual(hospital["specialist"], case["specialist"])

                train = match_announcement("train", case["text"], "12739")
                self.assertEqual(train["verdict"], "mine")
                self.assertEqual(train["platform"], "4")
                self.assertEqual(clean(train["train_name"]), clean(case["train_name"]))


class TestEnglishHospitalRepair(unittest.TestCase):
    def test_token_number_is_phrase(self):
        result = match_hospital(
            "Token number is forty seven. Please proceed to cardiology room two zero three.",
            "47",
        )
        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["room"], "203")
        self.assertEqual(result["specialist"], "cardiology")

    def test_room_before_token(self):
        result = match_hospital("Please go to room 203, token number 47.", "47")
        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["room"], "203")

    def test_alphanumeric_token(self):
        result = match_hospital("Now serving token A-47 at room 203.", "A-47")
        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "A-47")

    def test_numeric_watch_matches_block_token(self):
        result = match_hospital("Token A-47 at room 203.", "47")
        self.assertEqual(result["verdict"], "mine")

    def test_letter_watch_does_not_match_bare_numeric_token(self):
        result = match_hospital("Token 47 at room 203.", "A-47")
        self.assertEqual(result["verdict"], "other")

    def test_two_blocks_with_same_number_are_ambiguous(self):
        result = match_hospital(
            "Token A-47 at room 203. Token B-47 at room 204.", "47"
        )
        self.assertEqual(result["verdict"], "none")


class TestTokenPolicy(unittest.TestCase):
    def test_token_key(self):
        self.assertEqual(token_key("47"), (None, "47"))
        self.assertEqual(token_key("A-47"), ("A", "47"))
        self.assertEqual(token_key("a 47"), ("A", "47"))
        self.assertEqual(token_key("AB47"), ("AB", "47"))
        self.assertIsNone(token_key("47A"))

    def test_token_matches_is_strict(self):
        self.assertTrue(token_matches("47", "47"))
        self.assertTrue(token_matches("47", "A-47"))
        self.assertTrue(token_matches("A-47", "A-47"))
        self.assertFalse(token_matches("47", "847"))
        self.assertFalse(token_matches("A-47", "B-47"))
        self.assertFalse(token_matches("A-47", "47"))
        self.assertFalse(token_matches("047", "47"))

    def test_watch_accepts_block_letter_only_for_tokens(self):
        self.assertTrue(Watch("token", "A-47").matches({"token": "A-47"}))
        self.assertFalse(Watch("token", "A-47").matches({"token": "B-47"}))
        with self.assertRaises(ValueError):
            Watch("train", "A-47")
        with self.assertRaises(ValueError):
            Watch("train", "1273")
        with self.assertRaises(ValueError):
            Watch("train", "127390")


class TestNoInventedValues(unittest.TestCase):
    def test_destination_is_not_a_train_name(self):
        self.assertIsNone(find_train_name("12739 to Hyderabad platform 4"))
        self.assertIsNone(find_train_name("12739 Hyderabad platform 4"))

    def test_train_name_is_extracted_without_platform(self):
        self.assertEqual(
            clean(find_train_name("12739 Godavari Express is delayed")),
            "godavari express",
        )

    def test_train_name_before_the_number(self):
        self.assertEqual(
            clean(find_train_name("Godavari Express train number 12739")),
            "godavari express",
        )
        self.assertEqual(
            clean(find_train_name("गोदावरी एक्सप्रेस ट्रेन नंबर 12739")),
            "गोदावरी एक्सप्रेस",
        )
        self.assertEqual(
            clean(find_train_name("గోదావరి ఎక్స్‌ప్రెస్ రైలు నంబర్ 12739")),
            clean("గోదావరి ఎక్స్‌ప్రెస్"),
        )

    def test_specialist_needs_hospital_context_for_suffix_words(self):
        self.assertIsNone(find_specialist("Train 12739 technology express platform 4"))
        self.assertEqual(find_specialist("gastroenterology department"), "gastroenterology")
        self.assertEqual(find_specialist("oncology unit"), "oncology")

    def test_specialist_never_includes_a_number(self):
        self.assertEqual(find_specialist("तीन कार्डियोलॉजी विभाग"), "कार्डियोलॉजी")
        self.assertEqual(find_specialist("మూడు కార్డియాలజీ విభాగం"), "కార్డియాలజీ")
        self.assertIsNone(find_specialist("the department is closed"))

    def test_single_unknown_word_is_not_a_train_name(self):
        self.assertIsNone(find_train_name("12739 Hyderabad"))
        self.assertIsNone(find_train_name("12739 technology"))

    def test_unrelated_speech_has_no_hospital_slots(self):
        self.assertIsNone(find_token("The canteen is open on platform 2"))
        self.assertIsNone(find_room("The canteen is open on platform 2"))

    def test_ambiguous_rooms_are_refused(self):
        self.assertIsNone(find_room("room 203 and room 204"))

    def test_train_number_boundaries_are_strict(self):
        self.assertIsNone(find_train_no("127390"))
        self.assertIsNone(find_train_no("trains 12739 and 17201"))
        self.assertEqual(find_train_no("1 2 7 3 9"), "12739")


class TestScriptNormalisation(unittest.TestCase):
    def test_zero_width_platform_words(self):
        self.assertEqual(find_platform("ప్లాట్\u200cఫారం నాలుగు"), "4")
        self.assertEqual(find_platform("ಪ್ಲಾಟ್\u200cಫಾರ್ಮ್ ನಾಲ್ಕು"), "4")

    def test_native_digits_become_ascii(self):
        self.assertEqual(find_train_no("ట్రెయిన్ ౧౨౭౩౯"), "12739")
        slots = extract("टोकन क्रमांक १२७३९, खोली २०३")
        self.assertEqual(slots["room"], "203")
        self.assertEqual(normalize_text("૪૭"), "47")


class TestWatchSimulationEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        with server.WATCH_LOCK:
            server.WATCH["alerts"] = []
            server.WATCH["heard"] = 0
            server._RECENT_ALERTS.clear()

    def test_hospital_simulation_preserves_specialist_and_language(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "hospital",
            "value": "A-47",
            "text": "Token A-47, room 203, cardiology department",
            "language": "ta-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["verdict"], "mine")
        self.assertEqual(payload["token"], "A-47")
        self.assertEqual(payload["room"], "203")
        self.assertEqual(payload["specialist"], "cardiology")
        self.assertEqual(payload["requested_language"], "ta-IN")
        self.assertEqual(payload["lang"], "ta-IN")
        self.assertIn("அறை எண்", payload["spoken"])

    def test_train_without_platform_is_uncertain_not_confirmed(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "train",
            "value": "12739",
            "text": "Train 12739 Godavari Express is arriving",
            "language": "te-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["verdict"], "mine")
        self.assertFalse(payload["certain"])
        self.assertIn("వినపడలేదు", payload["spoken"])

    def test_train_simulation_speaks_platform_in_selected_language(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "train",
            "value": "12739",
            "text": "Train 12739 Godavari Express on platform 4",
            "language": "ta-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["lang"], "ta-IN")
        self.assertIn("நடைமேடை எண்", payload["spoken"])
        self.assertIn("4", payload["spoken"])

    def test_bus_simulation_speaks_bay_in_selected_language(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "bus",
            "value": "Hyderabad",
            "text": "Bus to Hyderabad is arriving at platform 7",
            "language": "kn-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["lang"], "kn-IN")
        self.assertIn("ವೇದಿಕೆ ಸಂಖ್ಯೆ", payload["spoken"])

    def test_unsupported_language_is_refused_not_silently_english(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "hospital",
            "value": "47",
            "text": "Token 47, room 203",
            "language": "xx-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("No voice is configured", response.get_json()["error"])

    def test_train_simulation_returns_train_name(self):
        response = self.client.post("/api/watch/simulate", json={
            "kind": "train",
            "value": "12739",
            "text": "Train 12739 Godavari Express on platform 4",
            "language": "en-IN",
            "speak": False,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["verdict"], "mine")
        self.assertEqual(payload["train_name"], "godavari express")
        self.assertEqual(payload["platform"], "4")

    def test_invalid_watch_values_are_rejected(self):
        bad_train = self.client.post("/api/watch/simulate", json={
            "kind": "train", "value": "12A39", "text": "anything",
        })
        short_train = self.client.post("/api/watch/simulate", json={
            "kind": "train", "value": "1273", "text": "anything",
        })
        long_train = self.client.post("/api/watch/simulate", json={
            "kind": "train", "value": "127390", "text": "anything",
        })
        bad_token = self.client.post("/api/watch/simulate", json={
            "kind": "hospital", "value": "47A", "text": "anything",
        })
        self.assertEqual(bad_train.status_code, 400)
        self.assertEqual(short_train.status_code, 400)
        self.assertEqual(long_train.status_code, 400)
        self.assertEqual(bad_token.status_code, 400)


class TestTranscriptLanguagePropagation(unittest.TestCase):
    def test_process_transcript_keeps_request_and_uses_supported_voice(self):
        captured = {}

        def fake_synthesize(text, language):
            captured["language"] = language
            return None

        pending = {"active": False, "started_at": 0, "token": None, "transcript": None}
        with patch.object(server, "_synthesize_alert", side_effect=fake_synthesize):
            record = server._process_transcript(
                "Token number 47, room 203, cardiology department",
                "hospital", "47", "ta-IN", pending,
            )

        self.assertIsNotNone(record)
        self.assertEqual(record["verdict"], "mine")
        self.assertEqual(record["requested_language"], "ta-IN")
        self.assertEqual(record["specialist"], "cardiology")
        self.assertEqual(record["lang"], "ta-IN")
        self.assertEqual(captured["language"], "ta-IN")
        self.assertIn("அறை எண்", record["spoken"])

    def test_missing_train_and_bus_alerts_keep_their_identifier(self):
        train, _, _ = server._compose_alert(
            "missing", "train", None, None, "en-IN",
            slots={"train_no": "12739"},
        )
        bus, _, _ = server._compose_alert(
            "missing", "bus", None, None, "en-IN",
            slots={"destination": "Guntur"},
        )
        self.assertIn("12739", train)
        self.assertIn("Guntur", bus)
        self.assertIn("not heard", train)
        self.assertIn("not heard", bus)


if __name__ == "__main__":
    unittest.main(verbosity=2)
