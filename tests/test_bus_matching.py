#!/usr/bin/env python3
"""Bus destination matching tests.

The matching algorithm must work for ANY Indian village/town/city name, so the
cases below are generated from base names instead of a fixed list of accepted
spellings. Train and hospital behaviour is exercised at the end as a regression
guard.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.alert import build_alert
from app.extract import (
    extract,
    find_platform,
    find_token,
    find_train_no,
)
from app.listen import load_stations
from app.matcher import match_announcement
from app.watch import Watch, best_place_match, place_match

# Base names only. Every variant below is generated from these, never typed in
# as an accepted spelling.
BASE_NAMES = [
    "Duggirala",
    "Vijayawada",
    "Visakhapatnam",
    "Narasaraopet",
    "Chilakaluripet",
    "Mangalagiri",
    "Sattenapalli",
    "Piduguralla",
    "Avanigadda",
    "Bhattiprolu",
    "Machilipatnam",
    "Rajahmundry",
    "Anantapur",
    "Karimnagar",
    "Nalgonda",
    "Suryapet",
    "Kandukur",
    "Markapur",
    "Giddalur",
    "Vemulapalli",
    "Chinnaganjam",
    "Gandikota",
    "Suryaraopeta",
]

# Distinct places that must never be treated as the same destination.
UNRELATED_PAIRS = [
    ("Suryapet", "Surat"),
    ("Karimnagar", "Srinagar"),
    ("Tenali", "Chennai"),
    ("Eluru", "Erode"),
    ("Ongole", "Anantapur"),
    ("Guntur", "Nellore"),
    ("Bapatla", "Bhopal"),
    ("Kadapa", "Kolkata"),
    ("Nellore", "Nalgonda"),
    ("Chirala", "Chittoor"),
]

VOWELS = set("aeiouAEIOU")


def drop_start(name: str) -> str:
    return name[1:]


def drop_end(name: str) -> str:
    return name[:-1]


def drop_middle(name: str) -> str:
    middle = len(name) // 2
    return name[:middle] + name[middle + 1:]


def substitute(name: str) -> str:
    midpoint = len(name) // 2
    for index in range(midpoint, len(name)):
        if name[index] not in VOWELS:
            replacement = "z" if name[index].lower() != "z" else "y"
            return name[:index] + replacement + name[index + 1:]
    return name[:midpoint] + "z" + name[midpoint + 1:]


def add_letter(name: str) -> str:
    middle = len(name) // 2
    return name[:middle] + "a" + name[middle:]


def sound_alike(name: str) -> str:
    for old, new in (("v", "w"), ("t", "th"), ("d", "dh"), ("s", "sh"), ("a", "aa")):
        if old in name:
            return name.replace(old, new, 1)
    return name


def announce(destination: str, platform: str | None = "5") -> str:
    text = f"Bus to {destination} is now arriving"
    if platform:
        text += f" at platform {platform}"
    return text


class TestUniversalPlaceMatching(unittest.TestCase):
    def test_exact_match_for_every_name(self):
        for name in BASE_NAMES:
            with self.subTest(name=name):
                result = place_match(name, name)
                self.assertEqual(result["verdict"], "exact")
                self.assertEqual(result["confidence"], 1.0)

    def test_normalisation_case_punctuation_and_spacing(self):
        self.assertEqual(place_match("Duggirala", "  dUgGiRaL a ").get("verdict"), "exact")
        self.assertEqual(place_match("Duggirala", "Duggi-rala.").get("verdict"), "exact")
        self.assertEqual(place_match("Hanuman Junction", "hanuman junction").get("verdict"), "exact")

    def test_missing_beginning_characters(self):
        # The reported examples.
        self.assertIn(place_match("Duggirala", "Girala")["verdict"], ("fuzzy",))
        self.assertIn(place_match("Duggirala", "Irala")["verdict"], ("fuzzy",))
        for name in BASE_NAMES:
            if len(name) < 8:
                continue
            variant = drop_start(name)
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_missing_middle_characters(self):
        for name in BASE_NAMES:
            if len(name) < 8:
                continue
            variant = drop_middle(name)
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_missing_ending_characters(self):
        for name in BASE_NAMES:
            if len(name) < 8:
                continue
            variant = drop_end(name)
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_substituted_characters(self):
        for name in BASE_NAMES:
            variant = substitute(name)
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_added_characters(self):
        for name in BASE_NAMES:
            variant = add_letter(name)
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_similar_pronunciation(self):
        for name in BASE_NAMES:
            variant = sound_alike(name)
            if variant == name:
                continue
            with self.subTest(name=name, variant=variant):
                self.assertIn(place_match(name, variant)["verdict"], ("exact", "fuzzy"))

    def test_multi_word_destinations(self):
        cases = [
            ("Hanuman Junction", "Hanuman Jn"),
            ("Kurnool Town", "Kurnool"),
            ("Bhattiprolu Town", "Bhattiprolu"),
            ("Naya Raipur", "Naya Raypur"),
        ]
        for target, variant in cases:
            with self.subTest(target=target, variant=variant):
                self.assertIn(place_match(target, variant)["verdict"], ("fuzzy",))

    def test_multi_word_partial_does_not_match_one_unrelated_word(self):
        # "Raipur" alone is not enough evidence for "Naya Raipur".
        self.assertEqual(place_match("Naya Raipur", "Raipur")["verdict"], "none")
        # "Junction" alone is a generic word, not the distinctive name.
        self.assertEqual(place_match("Hanuman Junction", "Junction")["verdict"], "none")

    def test_short_destination_names(self):
        self.assertEqual(place_match("Kodad", "Kodad")["verdict"], "exact")
        self.assertIn(place_match("Kodad", "Koda")["verdict"], ("fuzzy",))
        self.assertIn(place_match("Kodad", "Odad")["verdict"], ("fuzzy",))
        self.assertIn(place_match("Kodad", "Kodar")["verdict"], ("fuzzy",))
        # Too little evidence for a short name.
        self.assertEqual(place_match("Kodad", "Kod")["verdict"], "none")
        self.assertEqual(place_match("Kodad", "Oda")["verdict"], "none")
        self.assertEqual(place_match("Pon", "Poo")["verdict"], "none")

    def test_unrelated_destinations_are_rejected(self):
        for first, second in UNRELATED_PAIRS:
            with self.subTest(first=first, second=second):
                self.assertEqual(place_match(first, second)["verdict"], "none")
                self.assertEqual(place_match(second, first)["verdict"], "none")

    def test_empty_input_never_matches(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertEqual(place_match("Duggirala", value)["verdict"], "none")
                self.assertEqual(place_match(value, "Duggirala")["verdict"], "none")

    def test_multiple_plausible_candidates_are_ambiguous(self):
        result = best_place_match(
            "Duggirala",
            [{"text": "Girala", "source": "transcript"},
             {"text": "Irala", "source": "transcript"}],
        )
        self.assertEqual(result["verdict"], "ambiguous")

    def test_exact_match_has_priority_over_fuzzy_candidates(self):
        result = best_place_match(
            "Duggirala",
            [{"text": "Girala", "source": "transcript"},
             {"text": "Duggirala", "source": "transcript"}],
        )
        self.assertEqual(result["verdict"], "exact")
        self.assertEqual(result["candidate"], "Duggirala")

    def test_unknown_destination_matches_through_pipeline(self):
        stations = load_stations(ROOT)
        unknown = [
            name for name in BASE_NAMES
            if not any(name.casefold() == station.casefold() for station in stations)
        ]
        self.assertTrue(unknown, "fixtures must include names absent from data/")
        for name in unknown:
            heard = drop_start(name)
            with self.subTest(name=name, heard=heard):
                result = match_announcement("bus", announce(heard), name)
                self.assertEqual(result["verdict"], "mine")
                self.assertEqual(result["destination"], name)
                self.assertEqual(result["platform"], "5")

    def test_pipeline_exact_and_fuzzy(self):
        exact = match_announcement("bus", announce("Duggirala"), "Duggirala")
        self.assertEqual(exact["verdict"], "mine")
        self.assertEqual(exact["match_kind"], "exact")

        fuzzy = match_announcement("bus", announce("Girala"), "Duggirala")
        self.assertEqual(fuzzy["verdict"], "mine")
        self.assertEqual(fuzzy["match_kind"], "fuzzy")
        self.assertEqual(fuzzy["destination"], "Duggirala")
        self.assertEqual(fuzzy["heard_destination"].casefold(), "girala")

    def test_pipeline_unrelated_and_empty(self):
        other = match_announcement("bus", announce("Visakhapatnam"), "Duggirala")
        self.assertNotEqual(other["verdict"], "mine")

        empty = match_announcement("bus", "", "Duggirala")
        self.assertEqual(empty["verdict"], "none")

        blank = match_announcement("bus", "   ", "Duggirala")
        self.assertEqual(blank["verdict"], "none")

    def test_bus_alert_requires_a_destination_in_transport_context(self):
        for text in (
            "There is a Guntur hospital nearby",
            "The Guntur shop is closed",
            "Bus stop is closed in Guntur",
        ):
            with self.subTest(text=text):
                result = match_announcement("bus", text, "Guntur")
                self.assertNotEqual(result["verdict"], "mine")

        unrelated_stop = match_announcement(
            "bus", "Bus to Nellore and Guntur is delayed", "Guntur"
        )
        self.assertNotEqual(unrelated_stop["verdict"], "mine")

    def test_bus_context_still_matches_known_and_unknown_places(self):
        known = match_announcement("bus", "Bus to Guntur is arriving", "Guntur")
        self.assertEqual(known["verdict"], "mine")
        unknown = match_announcement(
            "bus", "Bus to Chinnaganjam is arriving", "Chinnaganjam"
        )
        self.assertEqual(unknown["verdict"], "mine")

    def test_pipeline_ambiguous_candidates_do_not_alert(self):
        result = match_announcement(
            "bus", "Bus to Girala via Irala has arrived", "Duggirala"
        )
        self.assertNotEqual(result["verdict"], "mine")
        self.assertEqual(result["match_kind"], "ambiguous")

    def test_watch_bus_uses_candidates(self):
        watch = Watch("bus", "Duggirala")
        self.assertTrue(watch.matches({"destination_candidates": [{"text": "Girala"}]}))
        self.assertTrue(watch.matches({"destination_candidates": [{"text": "Irala"}]}))
        self.assertFalse(watch.matches({"destination_candidates": [{"text": "Visakhapatnam"}]}))
        self.assertFalse(watch.matches({"destination_candidates": []}))


class TestNoRegressionTrainAndHospital(unittest.TestCase):
    def test_five_digit_train_extraction(self):
        self.assertEqual(find_train_no("Train number 12739 to Hyderabad"), "12739")
        self.assertEqual(find_train_no("Attention train one two seven three nine"), "12739")
        self.assertEqual(find_train_no("nine one two seven three"), "91273")
        self.assertIsNone(find_train_no("train 127390"))
        self.assertIsNone(find_train_no("trains 12739 and 17201 are delayed"))
        self.assertIsNone(find_train_no("no number here"))

    def test_exact_train_number_matching(self):
        watch = Watch("train", "12739")
        self.assertTrue(watch.matches({"train_no": "12739", "platform": "4"}))
        for wrong in ("12738", "1273", "127390", "12749", None):
            with self.subTest(wrong=wrong):
                self.assertFalse(watch.matches({"train_no": wrong}))

    def test_platform_extraction(self):
        self.assertEqual(find_platform("Train 12739 on platform 4"), "4")
        self.assertEqual(find_platform("platform number twelve"), "12")
        self.assertIsNone(find_platform("platform 99 does not exist"))
        self.assertIsNone(find_platform("platform ticket costs 10 rupees"))

    def test_hospital_token_extraction_and_matching(self):
        self.assertEqual(find_token("Token number 47 please come to room 204"), "47")
        self.assertIsNone(find_token("token number 47 and token number 52"))

        result = match_announcement("hospital", "Token number 47, room 204", "47")
        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["token"], "47")
        self.assertEqual(result["room"], "204")

        wrong = match_announcement("hospital", "Token number 47, room 204", "48")
        self.assertEqual(wrong["verdict"], "other")

    def test_hospital_exact_token_and_room_ambiguity(self):
        wrong_token = match_announcement("hospital", "Token 847, room 204", "47")
        self.assertEqual(wrong_token["verdict"], "other")
        self.assertNotEqual(wrong_token["verdict"], "mine")

        repeated = match_announcement(
            "hospital", "Token 47, room 204. Token 47, room 204.", "47"
        )
        self.assertEqual(repeated["verdict"], "mine")
        self.assertEqual(repeated["room"], "204")

        unclear_room = match_announcement(
            "hospital", "Token 47, room 204. Token 47, room 205.", "47"
        )
        self.assertEqual(unclear_room["verdict"], "none")
        self.assertIsNone(unclear_room["room"])

    def test_existing_train_announcement_matching(self):
        result = match_announcement("train", "Train 12739 arriving on platform 4", "12739")
        self.assertEqual(result["verdict"], "mine")
        self.assertEqual(result["platform"], "4")

    def test_train_number_in_non_train_speech_does_not_match(self):
        self.assertEqual(
            match_announcement("train", "Your ticket number is 12739", "12739")["verdict"],
            "none",
        )
        self.assertEqual(
            match_announcement(
                "train", "Your train ticket number is 12739", "12739"
            )["verdict"],
            "none",
        )
        self.assertEqual(
            match_announcement("train", "Train number 12739 is delayed", "12739")["verdict"],
            "mine",
        )

        named = match_announcement(
            "train", "Your train 12739 to Hyderabad is arriving on platform 4", "12739"
        )
        self.assertEqual(named["verdict"], "mine")
        self.assertEqual(named["destination"], "Hyderabad")

        other = match_announcement("train", "Train 17201 arriving on platform 4", "12739")
        self.assertEqual(other["verdict"], "other")

    def test_train_and_hospital_alerts_unchanged(self):
        train_alert = build_alert(
            {"train_no": "12739", "platform": "4", "destination": "Hyderabad"}
        )
        self.assertIn("12739", train_alert)
        self.assertIn("4", train_alert)
        self.assertNotIn("</", train_alert)

        hospital_alert = build_alert({"token": "47", "room": "204"}, kind="token")
        self.assertIn("47", hospital_alert)
        self.assertIn("204", hospital_alert)

    def test_extract_still_returns_original_fields(self):
        slots = extract("Train 12739 to Hyderabad on platform 4, token number 47 room 204")
        self.assertEqual(slots["train_no"], "12739")
        self.assertEqual(slots["platform"], "4")
        self.assertEqual(slots["token"], "47")
        self.assertEqual(slots["room"], "204")
        self.assertIn("destination_candidates", slots)


if __name__ == "__main__":
    unittest.main(verbosity=2)
