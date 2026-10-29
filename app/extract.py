"""
extract.py — pull identifiers out of a public-address transcript.

The one rule this file exists to enforce:

    A MISS IS RECOVERABLE. A WRONG ANSWER IS NOT.

If the app fails to hear an announcement, the user waits and hears the next one.
If the app reports the wrong platform, the user walks away from their train.
So every function here refuses rather than guesses.

Nothing in this file calls a model, a network, or an API. Pure text in, dict out.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# number words
# --------------------------------------------------------------------------
# Announcements at Indian stations rotate through the local language, Hindi and
# English, so a digit can arrive in any of three scripts or as a spoken word.

ENGLISH_DIGITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}

# Ten to thirty in English. Kept OUT of ENGLISH_DIGITS on purpose: that table
# feeds _spoken_digit_runs, which must only ever see single digits when it is
# assembling a 5-digit train number.
ENGLISH_TENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30,
}

# Romanised Telugu as it actually appears in STT output, longest first so that
# "padakondu" is tried before "padi".
TELUGU_ROMAN = {
    "pannendu": 12, "padakondu": 11, "padi": 10, "tommidi": 9, "enimidi": 8,
    "edu": 7, "aaru": 6, "aru": 6, "aidu": 5, "ayidu": 5, "nalugu": 4,
    "moodu": 3, "mudu": 3, "rendu": 2, "okati": 1, "oka": 1,
}

TELUGU_SCRIPT = {
    "పన్నెండు": 12, "పదకొండు": 11, "పది": 10, "తొమ్మిది": 9, "ఎనిమిది": 8,
    "ఏడు": 7, "ఆరు": 6, "ఐదు": 5, "నాలుగు": 4, "మూడు": 3, "రెండు": 2,
    "ఒకటి": 1,
}

HINDI_ROMAN = {
    "baarah": 12, "gyarah": 11, "das": 10, "nau": 9, "aath": 8, "saat": 7,
    "chhah": 6, "chhe": 6, "paanch": 5, "panch": 5, "chaar": 4, "char": 4,
    "teen": 3, "do": 2, "ek": 1,
}

# Words that mean "platform" in the three languages an Indian station uses.
PLATFORM_WORDS = [
    "platform", "plaatform", "plat form", "pf",
    "ప్లాట్‌ఫారం", "ప్లాట్‌ఫాం", "ప్లాట్ ఫారం",
    "प्लेटफॉर्म", "प्लेटफार्म",
]

# Words that mean "train", used only to raise confidence — never to invent.
TRAIN_WORDS = ["train", "railu", "రైలు", "ట్రైన్", "ट्रेन", "gaadi", "గాడి"]

# Words allowed to sit between "platform" and its number. Anything outside this
# set ends the search. Without it, "platform ticket costs 10 rupees" reports
# platform 10 and sends her to the wrong side of the station.
PLATFORM_CONNECTORS = {
    "number", "num", "no", "nambar", "nambaru", "nambar", "sankhya",
    "నంబర్", "నంబరు", "సంఖ్య", "नंबर", "क्रमांक", "संख्या",
    "is", "on", "at", "ke", "par", "pe",
}

TRAIN_NO_DIGITS = 5          # Indian Railways train numbers are 5 digits
PLATFORM_MIN, PLATFORM_MAX = 1, 30


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation that splits words."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("-", " ").replace(".", " ").replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _word_value(word: str) -> int | None:
    """Return the number a single word represents, or None."""
    if word in ENGLISH_DIGITS:
        return ENGLISH_DIGITS[word]
    if word in ENGLISH_TENS:
        return ENGLISH_TENS[word]
    if word in TELUGU_SCRIPT:
        return TELUGU_SCRIPT[word]
    if word in TELUGU_ROMAN:
        return TELUGU_ROMAN[word]
    if word in HINDI_ROMAN:
        return HINDI_ROMAN[word]
    return None


# Real Telugu/Hindi case endings that attach to a number word.
# This is a WHITELIST on purpose. An earlier version allowed "any ending up to
# 3 characters", and the English word "does" matched the Hindi stem "do" (2)
# with leftover "es" — reporting platform 2 for the sentence
# "platform 99 does not exist". Prefix matching without a whitelist invents
# numbers, which is the exact failure this module exists to prevent.
NUMBER_SUFFIXES = {
    "", "ki", "ku", "va", "vа", "vaki", "ti", "ta", "to", "o", "ni", "na", "lo",
}

# Stems shorter than this are matched EXACTLY and never by prefix — "do", "ek"
# and "oka" are too short to prefix-match English text safely.
MIN_PREFIX_STEM = 4


def _word_value_with_suffix(word: str) -> int | None:
    """
    Telugu glues case endings onto numbers: 'nalugu' + 'ki' = 'naluguki'
    ("to four"). Accept that, but only when the stem is long enough to be
    distinctive AND the leftover is a real grammatical ending.
    """
    exact = _word_value(word)
    if exact is not None:
        return exact

    for table in (TELUGU_ROMAN, HINDI_ROMAN, TELUGU_SCRIPT):
        for stem in sorted(table, key=len, reverse=True):
            if len(stem) < MIN_PREFIX_STEM:
                continue
            if word.startswith(stem) and word[len(stem):] in NUMBER_SUFFIXES:
                return table[stem]

    return None


def _spoken_digit_runs(words: list[str]) -> list[list[int]]:
    """
    Find runs of CONSECUTIVE single-digit words.

    Consecutive is the whole point. "nine one two seven" scattered through a
    sentence about money is four digits, not five, and must not become a train
    number. Only an unbroken run is considered.
    """
    runs: list[list[int]] = []
    current: list[int] = []

    for word in words:
        value = _word_value(word)
        if value is not None and 0 <= value <= 9:
            current.append(value)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    return runs


# --------------------------------------------------------------------------
# field extractors
# --------------------------------------------------------------------------

def find_train_no(text: str) -> str | None:
    """Exactly five digits, as numerals or as five spoken digits in a row."""
    clean = _normalise(text)

    # Numerals. \b on both sides so a 6-digit code never yields a 5-digit slice.
    numeric = re.findall(r"\b(\d{5})\b", clean)
    if len(numeric) == 1:
        return numeric[0]
    if len(numeric) > 1:
        return None          # ambiguous: two candidates, refuse rather than pick

    # Spoken digits.
    runs = _spoken_digit_runs(clean.split())
    exact = [r for r in runs if len(r) == TRAIN_NO_DIGITS]
    if len(exact) == 1:
        return "".join(str(d) for d in exact[0])

    return None


def find_platform(text: str) -> str | None:
    """
    A number that appears NEAR a platform word. Proximity is required — a bare
    number anywhere in the sentence is not a platform.
    """
    clean = _normalise(text)

    for marker in PLATFORM_WORDS:
        idx = clean.find(marker)
        if idx == -1:
            continue

        after = clean[idx + len(marker):].split()
        for pos, token in enumerate(after[:3]):      # look just ahead
            digits = re.fullmatch(r"(\d{1,2})\D{0,3}", token)
            if digits:
                value = int(digits.group(1))
                if PLATFORM_MIN <= value <= PLATFORM_MAX:
                    return str(value)
                break        # a number was spoken and it is not a platform

            value = _word_value_with_suffix(token)
            if value is not None:
                # "twenty one" is 21, not 20. Reading only the first half of a
                # compound number invents a platform that exists.
                if value in (20, 30) and pos + 1 < len(after):
                    unit = _word_value_with_suffix(after[pos + 1])
                    if unit is not None and 1 <= unit <= 9:
                        value += unit
                if PLATFORM_MIN <= value <= PLATFORM_MAX:
                    return str(value)
                break

            # An ordinary word sits between "platform" and the number.
            # Only a handful of connectors are legitimate there. Anything else
            # means this sentence is about something other than a platform:
            # "platform ticket costs 10 rupees" must NOT report platform 10.
            if token not in PLATFORM_CONNECTORS and token not in PLATFORM_WORDS:
                break        # a repeated marker is fine, a new topic is not

        # Telugu puts the number before the noun: "naluguva platform".
        before = clean[:idx].split()
        for token in reversed(before[-2:]):
            value = _word_value_with_suffix(token)
            if value is not None and PLATFORM_MIN <= value <= PLATFORM_MAX:
                return str(value)

    return None


TOKEN_WORDS = ("token", "tokan", "తోకెన్", "టోకెన్", "టోకన్", "ట్రోకెన్",
               "टोकन")
TOKEN_NUMBER_CONNECTORS = {"number", "no", "నంబర్", "నంబరు", "नंबर", "नम्बर"}
ROOM_WORDS = ["room", "గది", "రూమ్", "कमरा", "रूम", "gadi"]


def _number_at_start(text: str) -> str | None:
    """Normalize a numeral or recognized number words at the phrase start."""
    from app.matcher import normalize_text

    words = normalize_text(text).split()
    if not words:
        return None
    if words[0].isascii() and words[0].isdigit():
        return words[0]

    units = {**ENGLISH_DIGITS, **TELUGU_ROMAN, **TELUGU_SCRIPT, **HINDI_ROMAN}
    teens = {
        "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19,
        "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16,
        "satrah": 17, "atharah": 18, "unnis": 19,
    }
    tens = {
        **ENGLISH_TENS,
        "iravai": 20, "muppai": 30, "nalabhai": 40,
        "nalabai": 40, "yabai": 50, "aravai": 60, "debbai": 70,
        "enabhai": 80, "tombhai": 90,
        "bees": 20, "tees": 30, "chalis": 40, "chaalis": 40,
        "pachaas": 50, "saath": 60, "sattar": 70, "assi": 80,
        "nabbe": 90,
    }
    if words[0] in teens:
        return str(teens[words[0]])
    if words[0] in tens:
        value = tens[words[0]]
        if len(words) > 1 and words[1] in units and 1 <= units[words[1]] <= 9:
            value += units[words[1]]
        return str(value)
    if words[0] in units:
        digits = []
        for word in words:
            if word not in units or units[word] > 9:
                break
            digits.append(str(units[word]))
        return "".join(digits) if digits else None
    return None


def find_tokens(text: str) -> list[dict[str, str | None]]:
    """Return numeric token announcements whose number follows a token cue."""
    clean = _normalise(text)
    markers = []
    for word in TOKEN_WORDS:
        markers.extend(re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", clean))
    markers.sort(key=lambda match: match.start())

    announcements = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(clean)
        tail = clean[marker.end():end]
        room_positions = [tail.find(word) for word in ROOM_WORDS if tail.find(word) >= 0]
        if room_positions:
            tail = tail[:min(room_positions)]

        number_words = tail.split()
        while number_words and number_words[0] in TOKEN_NUMBER_CONNECTORS:
            number_words.pop(0)
        token = _number_at_start(" ".join(number_words[:6])) if number_words else None
        if token is None:
            continue

        room = find_room(clean[marker.end():end])
        announcements.append({"token": token, "room": room})
    return announcements


def find_token(text: str) -> str | None:
    """Return a numeric hospital token only when one candidate is unambiguous."""
    announcements = find_tokens(text)
    if len(announcements) != 1:
        return None
    return announcements[0]["token"]


def find_room(text: str) -> str | None:
    """Return a room number only when a room marker is followed by its number."""
    clean = _normalise(text)
    for word in ROOM_WORDS:
        idx = clean.find(word)
        if idx == -1:
            continue
        tail = clean[idx + len(word):]
        words = tail.split()
        while words and words[0] in TOKEN_NUMBER_CONNECTORS:
            words.pop(0)
        if not words:
            continue
        number = _number_at_start(" ".join(words[:6]))
        if number:
            return number
    return None


def find_destination(text: str, stations: list[str] | None = None) -> str | None:
    """
    Match against a KNOWN list of station names. Never returns a guess — if the
    caller supplies no list, this returns None, because an unknown place name
    cannot be verified.
    """
    if not stations:
        return None

    clean = _normalise(text)
    for station in sorted(stations, key=len, reverse=True):
        needle = _normalise(station)
        if not needle:
            continue
        # Whole-word only. A plain substring test finds "Patna" inside
        # "Visakhapatnam" and announces the wrong city.
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", clean):
            return station
    return None


def extract(text: str, stations: list[str] | None = None) -> dict:
    """
    Pull every identifier we can justify from one transcript.

    Returns {"train_no": str|None, "platform": str|None, "destination": str|None}
    Any field the text does not clearly support is None.
    """
    if not text or not text.strip():
        return {"train_no": None, "platform": None, "destination": None,
                "token": None, "room": None}

    return {
        "train_no": find_train_no(text),
        "platform": find_platform(text),
        "destination": find_destination(text, stations),
        "token": find_token(text),
        "room": find_room(text),
    }


# --------------------------------------------------------------------------
# self test — runs with: python -m app.extract
# --------------------------------------------------------------------------

if __name__ == "__main__":
    STATIONS = ["Hyderabad", "Vijayawada", "Secunderabad", "Tenali", "Chennai"]

    # (text, expected train_no, expected platform)
    CASES = [
        # --- must find ---
        ("Train number 12739 Garib Rath to Hyderabad will arrive on platform 4",
         "12739", "4"),
        ("12739 nambaru railu platform naluguki vastundi",
         "12739", "4"),
        ("Attention train one two seven three nine is delayed",
         "12739", None),
        ("nine one two seven three",
         "91273", None),
        ("రైలు నంబర్ 12739 ప్లాట్‌ఫారం రెండు కి వస్తుంది",
         "12739", "2"),
        ("train 17201 arriving platform 12",
         "17201", "12"),

        # --- must REFUSE ---
        ("The canteen on platform 2 is now open",
         None, "2"),
        ("money is gone nine one two seven",
         None, None),
        ("there is no one here",
         None, None),
        ("stone alone phone money",
         None, None),
        ("trains 12739 and 17201 are both delayed",
         None, None),          # two candidates -> refuse
        ("platform 99 does not exist",
         None, None),          # out of range
        ("",
         None, None),
        ("ladies and gentlemen your attention please",
         None, None),
    ]

    TOKEN_CASES = [
        ("Token number 47 please come to room 204", "47", "204"),
        ("token 112 counter three", "112", None),
        ("టోకెన్ నంబర్ 47 గది 204 కి రండి", "47", "204"),
        ("the canteen is open", None, None),
        ("please come at 4 o clock", None, None),
        ("room 204 is closed today", None, "204"),
        ("token number 47 and token number 52", None, None),
    ]

    print("extract.py self test")
    print("=" * 68)
    failed = 0
    for text, want_train, want_plat in CASES:
        got = extract(text, STATIONS)
        ok_t = got["train_no"] == want_train
        ok_p = got["platform"] == want_plat
        if not (ok_t and ok_p):
            failed += 1
        mark = "PASS" if ok_t and ok_p else "FAIL"
        shown = text if len(text) <= 44 else text[:41] + "..."
        print(f"  {mark}  {shown:<46} train={str(got['train_no']):<6} "
              f"plat={str(got['platform']):<5}")
        if not ok_t:
            print(f"        ^ train expected {want_train}")
        if not ok_p:
            print(f"        ^ platform expected {want_plat}")

    for text, want_token, want_room in TOKEN_CASES:
        got = extract(text)
        ok_t = got["token"] == want_token
        ok_r = got["room"] == want_room
        if not (ok_t and ok_r):
            failed += 1
        mark = "PASS" if ok_t and ok_r else "FAIL"
        shown = text if len(text) <= 44 else text[:41] + "..."
        print(f"  {mark}  {shown:<46} token={str(got['token']):<6} "
              f"room={str(got['room']):<5}")
        if not ok_t:
            print(f"        ^ token expected {want_token}")
        if not ok_r:
            print(f"        ^ room expected {want_room}")

    total = len(CASES) + len(TOKEN_CASES)
    print("=" * 68)
    print(f"{total - failed} passed, {failed} failed")
