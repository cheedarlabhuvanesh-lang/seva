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

# --------------------------------------------------------------------------
# Word to Number Dictionaries
# --------------------------------------------------------------------------

TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # Telugu script native
    "ఇరవై": 20, "ముప్పై": 30, "నలభై": 40, "యాభై": 50,
    "అరవై": 60, "డెబ్బై": 70, "ఎనభై": 80, "తొంభై": 90,
    # Telugu transliteration of English
    "ట్వంటీ": 20, "థర్టీ": 30, "ఫార్టీ": 40, "ఫోర్టీ": 40,
    "ఫిఫ్టీ": 50, "సిక్స్టీ": 60, "సెవెంటీ": 70, "ఎయిటీ": 80, "నైంటీ": 90,
    # Hindi script native
    "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50,
    "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90,
    # Hindi transliteration of English
    "ट्वेंटी": 20, "थर्टी": 30, "फोर्टी": 40, "फार्टी": 40,
    "फिफ्टी": 50, "सिक्सटी": 60, "सेवंटी": 70, "एटी": 80, "नाइंटी": 90,
}

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    # Telugu native
    "సున్నా": 0, "ఒకటి": 1, "ఒక": 1, "రెండు": 2, "మూడు": 3,
    "నాలుగు": 4, "ఐదు": 5, "అయిదు": 5, "ఆరు": 6, "ఏడు": 7,
    "ఎనిమిది": 8, "తొమ్మిది": 9,
    # Telugu transliteration of English
    "జీరో": 0, "వన్": 1, "ఒన్": 1, "టూ": 2, "త్రీ": 3,
    "ఫోర్": 4, "ఫైవ్": 5, "సిక్స్": 6, "సెవెన్": 7, "ఎయిట్": 8, "నైన్": 9,
    # Hindi native
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4,
    "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9,
    # Hindi transliteration of English
    "ज़ीरो": 0, "जीरो": 0, "वन": 1, "टू": 2, "थ्री": 3,
    "फोर": 4, "फाइव": 5, "सिक्स": 6, "सेवन": 7, "एट": 8, "ऐट": 8, "नाइन": 9,
}

TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
    # Telugu native
    "పది": 10, "పదకొండు": 11, "పన్నెండు": 12, "పదమూడు": 13,
    "పద్నాలుగు": 14, "పదిహేను": 15, "పదహారు": 16, "పదిహేడు": 17,
    "పద్దెనిమిది": 18, "పంతొమ్మిది": 19,
    # Telugu transliteration of English
    "టెన్": 10, "ఎలెవెన్": 11, "ట్వెల్వ్": 12, "థర్టీన్": 13,
    "ఫోర్టీన్": 14, "ఫిఫ్టీన్": 15, "సిక్స్టీన్": 16, "సిక్స్‌టీన్": 16,
    "సెవెంటీన్": 17, "ఎయిటీన్": 18, "నైంటీన్": 19,
    # Hindi native
    "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13,
    "चौदह": 14, "पंद्रह": 15, "सोलह": 16, "सत्रह": 17,
    "अठारह": 18, "उन्नीस": 19,
    # Hindi transliteration of English
    "टेन": 10,
}

HUNDREDS = {"hundred", "వందల", "వందలు", "వంద", "నూరు", "सौ", "हंड्रेड", "హండ్రెడ్"}

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


# --------------------------------------------------------------------------
# Normalization Pipeline
# --------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Full normalizer:
    1. Lowercase and Unicode NFC.
    2. Convert Telugu (౦-౯) and Devanagari (०-९) digits to ASCII 0-9.
    3. Treat 'not/nought/o/oh' between two digits as 0 ('two not four' -> 204).
    4. Compound and word-to-number expansion.
    5. Merge adjacent spaced digits ('4 7' -> 47, '2 0 4' -> 204).
    """
    text = unicodedata.normalize("NFC", text or "").lower()

    # Convert Telugu and Devanagari script digits
    for i in range(10):
        text = text.replace(chr(0x0C66 + i), str(i))
        text = text.replace(chr(0x0966 + i), str(i))

    # Strip punctuation that separates words
    text = re.sub(r"[,.:;!?-]", " ", text)
    tokens = text.split()

    # Pass 1: zero between digits (e.g. "two not four" -> "two 0 four")
    for i in range(1, len(tokens) - 1):
        if tokens[i] in ("not", "nought", "o", "oh"):
            prev_is_digit = tokens[i - 1].isdigit() or tokens[i - 1] in UNITS
            next_is_digit = tokens[i + 1].isdigit() or tokens[i + 1] in UNITS
            if prev_is_digit and next_is_digit:
                tokens[i] = "0"

    # Pass 2: Word number to integer/digits conversion
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        w = tokens[i]

        # Multiplier + Hundred + Remainder: e.g. "two hundred and four"
        if (w.isdigit() or w in UNITS) and i + 1 < n and tokens[i + 1] in HUNDREDS:
            mult = int(w) if w.isdigit() else UNITS[w]
            i += 2
            rem = 0
            if i < n and tokens[i] in ("and", "మరియు", "औ", "और"):
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

        # Tens + Unit: e.g. "forty seven" -> 47
        if w in TENS:
            t_val = TENS[w]
            if i + 1 < n and tokens[i + 1] in UNITS:
                out.append(str(t_val + UNITS[tokens[i + 1]]))
                i += 2
            else:
                out.append(str(t_val))
                i += 1
            continue

        # Teens
        if w in TEENS:
            out.append(str(TEENS[w]))
            i += 1
            continue

        # Single units
        if w in UNITS:
            out.append(str(UNITS[w]))
            i += 1
            continue

        out.append(w)
        i += 1

    res = " ".join(out)
    # Merge spaced digits: '4 7' -> '47', '2 0 4' -> '204'
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
            "room": str | None
        }
    """
    target = str(my_value).strip()
    if not target.isascii() or not target.isdigit():
        return {"verdict": "none", "token": None, "room": None}

    # The extractor owns hospital token boundaries and multilingual numbers.
    from app.extract import find_tokens
    announcements = find_tokens(transcript)
    if not announcements:
        return {"verdict": "none", "token": None, "room": None}

    matching = [item for item in announcements if item["token"] == target]
    if matching:
        chosen = next((item for item in matching if item["room"]), matching[0])
        if chosen["room"]:
            return {"verdict": "mine", "token": target, "room": chosen["room"]}
        return {"verdict": "missing", "token": target, "room": None}

    other = announcements[0]
    return {"verdict": "other", "token": other["token"], "room": other["room"]}


def match_announcement(kind: str, transcript: str, value: str | int) -> dict:
    """General announcement matcher supporting hospital, train, and bus."""
    if kind == "hospital" or kind == "token":
        return match_hospital(transcript, value)

    # extract owns slot parsing; Watch owns the exact/fuzzy comparison policy.
    from pathlib import Path
    from app.extract import extract
    from app.listen import load_stations
    from app.watch import Watch
    slots = extract(transcript, load_stations(Path(__file__).resolve().parent.parent))
    target = Watch(kind, str(value))
    if kind == "train":
        train_no = slots.get("train_no")
        plat = slots.get("platform")
        if target.matches(slots):
            return {"verdict": "mine", "train_no": str(value).strip(), "platform": plat}
        if train_no:
            return {"verdict": "other", "train_no": train_no, "platform": plat}
        return {"verdict": "none", "train_no": None, "platform": None}

    if kind == "bus":
        dest = slots.get("destination")
        plat = slots.get("platform")
        if target.matches(slots):
            return {"verdict": "mine", "destination": dest, "platform": plat}
        if dest:
            return {"verdict": "other", "destination": dest, "platform": plat}
        return {"verdict": "none", "destination": None, "platform": None}

    return {"verdict": "none"}
