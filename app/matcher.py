"""
matcher.py — Hospital token and transport announcement matching.

Hospital token matching here uses phrase-aware number and room parsing;
app.extract provides transport slots, while app.watch owns transport
identifier/destination comparison policy.

Rules:
1. A standalone number equal to the target value matches exactly (e.g. 47).
2. No leading-digit leniency: 847 does not match 47.
3. Room is the number following the token after a room word or "go to".
4. If room is preceded by "not" (e.g. "not four") without a preceding digit, it is
   garbled. Room is None, NEVER "4".
5. Unrelated speech (no token context, e.g. "ticket costs 10 rupees") yields "none".
"""

from __future__ import annotations

import re
import unicodedata

from app.langdata import (
    COMPOUND_NUMBERS,
    NATIVE_AND,
    NATIVE_HUNDREDS,
    NATIVE_TEENS,
    NATIVE_TENS,
    NATIVE_UNITS,
    ROOM_WORDS,
    strip_zero_width,
    to_ascii_digits,
)

TENS = NATIVE_TENS
UNITS = NATIVE_UNITS
TEENS = NATIVE_TEENS
HUNDREDS = NATIVE_HUNDREDS

TOKEN_INDICATORS = {
    "token", "rookie", "tokan", "తోకెన్", "టోకెన్", "టోకన్", "ట్రోకెన్",
    "टोकन", "टोकन", "number", "నంబర్", "నంబరు", "नंबर", "no"
}

ROOM_INDICATORS = {
    "room", "గది", "రూమ్", "कमरा", "कक्ष", "रूम", "gadi", "roomno", "roomnumber"
}

GO_INDICATORS = {
    "go", "వెళ్ళండి", "వెళ్లండి", "జాచ్", "जाएं", "जाएँ"
}

_ROOM_WORD_PATTERN = "|".join(
    re.escape(word) for word in sorted(set(ROOM_WORDS), key=len, reverse=True)
)
_UNMARKED_LETTER_TOKEN_RE = re.compile(
    rf"^([a-z])\s+(\d+)(?:\s+(?:{_ROOM_WORD_PATTERN})\s+(\d+))?$"
)


# --------------------------------------------------------------------------
# Normalization Pipeline
# --------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Full normalizer:
    1. Lowercase, Unicode NFC, strip zero-width marks.
    2. Convert every supported script's digits to ASCII 0-9.
    3. Treat 'not/nought/o/oh' between two digits as 0 ('two not four' -> 204).
    4. Compound and word-to-number expansion in all ten languages.
    5. Merge adjacent spaced digits ('4 7' -> 47, '2 0 4' -> 204).
    """
    text = unicodedata.normalize("NFC", text or "")
    text = strip_zero_width(text)
    text = to_ascii_digits(text).lower()
    text = re.sub(r"[\-.,:;!?/\\#@()\[\]{}]+", " ", text)
    text = text.replace("।", " ").replace("॥", " ")
    tokens = text.split()

    for i in range(1, len(tokens) - 1):
        if tokens[i] in ("not", "nought", "o", "oh"):
            prev_is_digit = tokens[i - 1].isdigit() or tokens[i - 1] in UNITS
            next_is_digit = tokens[i + 1].isdigit() or tokens[i + 1] in UNITS
            if prev_is_digit and next_is_digit:
                tokens[i] = "0"

    out = []
    i = 0
    n = len(tokens)
    while i < n:
        w = tokens[i]

        if (w.isdigit() or w in UNITS) and i + 1 < n and tokens[i + 1] in HUNDREDS:
            mult = int(w) if w.isdigit() else UNITS[w]
            i += 2
            rem = 0
            if i < n and tokens[i] in NATIVE_AND:
                i += 1
            if i < n:
                nxt = tokens[i]
                if nxt in TENS:
                    t_val = TENS[nxt]
                    i += 1
                    if i < n and tokens[i] in UNITS:
                        rem = t_val + UNITS[tokens[i]]
                        i += 1
                    else:
                        rem = t_val
                elif nxt in TEENS:
                    rem = TEENS[nxt]
                    i += 1
                elif nxt in UNITS:
                    rem = UNITS[nxt]
                    i += 1
                elif nxt.isdigit():
                    rem = int(nxt)
                    i += 1
            out.append(str(mult * 100 + rem))
            continue

        if w in COMPOUND_NUMBERS:
            out.append(str(COMPOUND_NUMBERS[w]))
            i += 1
            continue

        if w in TENS:
            t_val = TENS[w]
            if i + 1 < n and tokens[i + 1] in UNITS:
                out.append(str(t_val + UNITS[tokens[i + 1]]))
                i += 2
            else:
                out.append(str(t_val))
                i += 1
            continue

        if w in TEENS:
            out.append(str(TEENS[w]))
            i += 1
            continue

        if w in UNITS:
            out.append(str(UNITS[w]))
            i += 1
            continue

        out.append(w)
        i += 1

    res = " ".join(out)
    res = re.sub(r"(?<=\d)\s+(?=\d)", "", res)
    return res


# --------------------------------------------------------------------------
# Hospital Announcement Matcher
# --------------------------------------------------------------------------

def match_hospital(transcript: str, my_value: str | int) -> dict:
    """
    Match transcript against expected hospital token.

    Returns dict:
        {
            "verdict": "mine" | "other" | "missing" | "none",
            "token": str | None,
            "room": str | None,
            "specialist": str | None
        }
    """
    target = str(my_value).strip()

    # The extractor owns hospital token boundaries and multilingual numbers.
    from app.extract import find_specialist, find_tokens, token_key, token_matches
    if token_key(target) is None:
        return {"verdict": "none", "token": None, "room": None, "specialist": None}

    specialist = find_specialist(transcript)
    announcements = find_tokens(transcript)
    if not announcements:
        normalized = normalize_text(transcript).strip()
        unmarked = _UNMARKED_LETTER_TOKEN_RE.fullmatch(normalized)
        if unmarked:
            letter, number, room = unmarked.groups()
            announcements = [{
                "token": f"{letter.upper()}-{number}",
                "room": room,
            }]

    if not announcements:
        return {
            "verdict": "none", "token": None, "room": None,
            "specialist": specialist,
        }

    matching = [item for item in announcements if token_matches(target, item["token"])]
    if matching:
        letters = {
            key[0] for item in matching
            if (key := token_key(str(item["token"]))) is not None
        }
        rooms = {item["room"] for item in matching if item["room"]}
        if len(matching) > 1 and (len(letters) > 1 or len(rooms) > 1):
            return {
                "verdict": "none", "token": None, "room": None,
                "specialist": specialist,
            }

        chosen = next((item for item in matching if item["room"]), matching[0])
        if chosen["room"]:
            return {
                "verdict": "mine", "token": chosen["token"],
                "room": chosen["room"], "specialist": specialist,
            }
        return {
            "verdict": "missing", "token": chosen["token"],
            "room": None, "specialist": specialist,
        }

    other = announcements[0]
    return {
        "verdict": "other", "token": other["token"],
        "room": other["room"], "specialist": specialist,
    }


def match_announcement(kind: str, transcript: str, value: str | int) -> dict:
    """General announcement matcher supporting hospital, train, and bus."""
    if kind == "hospital" or kind == "token":
        return match_hospital(transcript, value)

    # extract owns slot parsing; Watch owns the exact/fuzzy comparison policy.
    from pathlib import Path
    from app.extract import extract, has_train_context
    from app.listen import load_stations
    from app.watch import Watch, best_place_match
    slots = extract(transcript, load_stations(Path(__file__).resolve().parent.parent))
    target = Watch(kind, str(value))
    if kind == "train":
        train_no = slots.get("train_no")
        train_name = slots.get("train_name")
        plat = slots.get("platform")
        dest = slots.get("destination")
        if train_no and not has_train_context(transcript, train_no):
            return {
                "verdict": "none", "train_no": None,
                "train_name": None, "platform": None,
                "destination": None,
            }
        if not dest:
            candidates = {
                str(entry.get("text", "")).strip()
                for entry in (slots.get("destination_candidates") or [])
                if isinstance(entry, dict) and entry.get("text")
            }
            if len(candidates) == 1:
                dest = candidates.pop()
        if dest and dest.isascii():
            dest = dest.title()
        if target.matches(slots):
            return {
                "verdict": "mine", "train_no": str(value).strip(),
                "train_name": train_name, "platform": plat,
                "destination": dest,
            }
        if train_no:
            return {
                "verdict": "other", "train_no": train_no,
                "train_name": train_name, "platform": plat,
                "destination": dest,
            }
        return {
            "verdict": "none", "train_no": None,
            "train_name": None, "platform": None,
            "destination": None,
        }

    if kind == "bus":
        dest = slots.get("destination")
        plat = slots.get("platform")
        candidates = list(slots.get("destination_candidates") or [])
        if dest and not any(
            str(entry.get("text", "")).casefold() == str(dest).casefold()
            for entry in candidates if isinstance(entry, dict)
        ):
            candidates.insert(0, {"text": dest, "source": "known"})
        match = best_place_match(str(value), candidates)
        if match["verdict"] in ("exact", "fuzzy"):
            # Show/speak the destination the user configured, not the mangled
            # text that speech recognition produced.
            return {
                "verdict": "mine",
                "destination": str(value).strip(),
                "heard_destination": match["candidate"] or dest,
                "platform": plat,
                "match_kind": match["verdict"],
                "confidence": match["confidence"],
            }
        if match["verdict"] == "ambiguous":
            return {
                "verdict": "none",
                "destination": str(value).strip(),
                "heard_destination": match["candidate"] or dest,
                "platform": plat,
                "match_kind": "ambiguous",
                "confidence": match["confidence"],
            }
        known = [
            entry for entry in candidates
            if isinstance(entry, dict) and entry.get("source") == "known"
        ]
        if known:
            return {
                "verdict": "other",
                "destination": known[0]["text"],
                "platform": plat,
                "match_kind": "other",
                "confidence": 0.0,
            }
        return {
            "verdict": "none", "destination": None, "platform": None,
            "match_kind": "none", "confidence": 0.0,
        }

    return {"verdict": "none"}
