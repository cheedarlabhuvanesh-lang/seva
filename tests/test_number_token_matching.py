from __future__ import annotations

import unittest

from app.matcher import match_hospital


class TestLeadingNumberTokenMatching(unittest.TestCase):
    def test_number_47_is_recognized_as_token_47_without_a_room(self):
        result = match_hospital("Number 47", "47")

        self.assertEqual(result["verdict"], "missing")
        self.assertEqual(result["token"], "47")
        self.assertIsNone(result["room"])

    def test_number_47_with_a_room_is_confirmed(self):
        result = match_hospital("Number 47 room 204", "47")

        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "47")
        self.assertEqual(result["room"], "204")

    def test_token_47_is_recognized_as_token_47_without_a_room(self):
        result = match_hospital("Token 47", "47")

        self.assertEqual(result["verdict"], "missing")
        self.assertEqual(result["token"], "47")
        self.assertIsNone(result["room"])

    def test_a_47_is_recognized_as_token_a_47_without_a_room(self):
        result = match_hospital("A 47", "47")

        self.assertEqual(result["verdict"], "missing")
        self.assertEqual(result["token"], "A-47")
        self.assertIsNone(result["room"])

    def test_standalone_847_does_not_match_47(self):
        result = match_hospital("847", "47")

        self.assertEqual(result["verdict"], "none")
        self.assertIsNone(result["token"])

    def test_number_847_does_not_match_47(self):
        result = match_hospital("Number 847", "47")

        self.assertNotEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "847")

    def test_number_48_does_not_match_47(self):
        result = match_hospital("Number 48", "47")

        self.assertNotEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "48")

    def test_unclear_speech_does_not_confirm(self):
        result = match_hospital("Unclear speech", "47")

        self.assertEqual(result["verdict"], "none")
        self.assertIsNone(result["token"])

    def test_number_after_room_word_is_not_treated_as_a_token(self):
        result = match_hospital("Room number 47", "47")

        self.assertEqual(result["verdict"], "none")
        self.assertIsNone(result["token"])


if __name__ == "__main__":
    unittest.main()
