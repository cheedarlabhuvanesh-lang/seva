from __future__ import annotations

import unittest

from app.matcher import match_hospital


class TestUnmarkedLetterTokenMatching(unittest.TestCase):
    def test_a_47_matches_bare_user_token_without_inventing_room(self):
        result = match_hospital("A 47", "47")

        self.assertEqual(result["verdict"], "missing")
        self.assertEqual(result["token"], "A-47")
        self.assertIsNone(result["room"])

    def test_a_47_with_explicit_room_matches(self):
        result = match_hospital("A 47 room 204", "47")

        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "A-47")
        self.assertEqual(result["room"], "204")

    def test_explicit_token_without_room_is_recognized_without_guessing(self):
        result = match_hospital("Token number 47", "47")

        self.assertEqual(result["verdict"], "missing")
        self.assertEqual(result["token"], "47")
        self.assertIsNone(result["room"])

    def test_standalone_147_is_not_token_47(self):
        result = match_hospital("147", "47")

        self.assertNotEqual(result["verdict"], "mine")

    def test_token_847_is_not_token_47(self):
        result = match_hospital("Token 847", "47")

        self.assertNotEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "847")

    def test_different_letter_number_token_does_not_match(self):
        result = match_hospital("A 48", "47")

        self.assertEqual(result["verdict"], "other")
        self.assertEqual(result["token"], "A-48")

    def test_ambiguous_or_unbounded_letter_number_text_is_not_matched(self):
        ambiguous = match_hospital("A 47 room 204 room 205", "47")
        unrelated_suffix = match_hospital("A 47 and A 48", "47")

        self.assertEqual(ambiguous["verdict"], "none")
        self.assertIsNone(ambiguous["room"])
        self.assertEqual(unrelated_suffix["verdict"], "none")

    def test_existing_explicit_token_marker_still_uses_exact_matching(self):
        match = match_hospital("Token number 47 room 204", "47")
        mismatch = match_hospital("Token 847 room 204", "47")

        self.assertEqual(match["verdict"], "mine")
        self.assertEqual(match["room"], "204")
        self.assertEqual(mismatch["verdict"], "other")


if __name__ == "__main__":
    unittest.main()
