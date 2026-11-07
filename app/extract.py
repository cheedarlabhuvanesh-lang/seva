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

from app.langdata import (
    COMPOUND_NUMBERS,
    DEPARTMENT_FILLERS,
    DEPARTMENT_MARKERS,
    HOSPITAL_CUES,
    NATIVE_TEENS,
    NATIVE_TENS,
    NATIVE_UNITS,
    NUMBER_CONNECTORS,
    NUMBER_TABLES,
    PLATFORM_CONNECTORS,
    PLATFORM_WORDS,
    ROMAN_TEENS,
    ROMAN_TENS,
    ROMAN_UNITS,
    ROOM_WORDS,
    SPECIALIST_BLOCKERS,
    SPECIALIST_GENERIC,
    TOKEN_WORDS,
    TRAIN_WORDS,
    TRAIN_NAME_LEAD,
    TRAIN_NAME_STOPWORDS,
    TRAIN_NAME_SUFFIXES,
    strip_zero_width,
    to_ascii_digits,
)

TRAIN_NO_DIGITS = 5
PLATFORM_MIN, PLATFORM_MAX = 1, 30
MAX_TRAIN_NAME_WORDS = 4
MAX_PLACE_WORDS = 4

BUS_WORDS = frozenset({
    "bus", "buses", "బస్", "బస్సు", "बस", "பஸ்", "பேருந்து",
    "ಬಸ್", "ಬಸ್ಸು", "ബസ്", "ബസ്സ്", "বাস", "બસ", "ਬਸ",
})
_TRANSIT_WORDS = frozenset(TRAIN_WORDS) | BUS_WORDS
_DESTINATION_CONNECTORS = frozenset({
    "to", "towards", "for", "via", "destination", "bound",
    "కు", "కి", "వైపు", "तक", "की", "को", "க்கு", "வரை",
    "ಗೆ", "ವರೆಗೆ", "ലേക്ക്", "വരെ", "ला", "कडे", "দিকে",
    "ને", "તરફ", "ਨੂੰ", "ਵੱਲ",
})


def _marker_re(words) -> re.Pattern:
    alternatives = "|".join(
        re.escape(word) for word in sorted(set(words), key=len, reverse=True) if word
    )
    return re.compile(rf"(?<!\w)(?:{alternatives})")


_PLATFORM_RE = _marker_re(PLATFORM_WORDS)
_TOKEN_RE = _marker_re(TOKEN_WORDS)
_ROOM_RE = _marker_re(ROOM_WORDS)


def _normalise(text: str) -> str:
    """Lowercase, strip zero-width marks/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFC", text or "")
    text = strip_zero_width(text)
    text = to_ascii_digits(text)
    text = re.sub(r"[\-.,:;!?/\\#@()\[\]{}]+", " ", text)
    text = text.replace("।", " ").replace("॥", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _word_value(word: str) -> int | None:
    if word in NATIVE_UNITS:
        return NATIVE_UNITS[word]
    if word in NATIVE_TEENS:
        return NATIVE_TEENS[word]
    if word in NATIVE_TENS:
        return NATIVE_TENS[word]
    if word in COMPOUND_NUMBERS:
        return COMPOUND_NUMBERS[word]
    if word in ROMAN_UNITS:
        return ROMAN_UNITS[word]
    if word in ROMAN_TEENS:
        return ROMAN_TEENS[word]
    if word in ROMAN_TENS:
        return ROMAN_TENS[word]
    return None


NUMBER_SUFFIXES = {
    "", "ki", "ku", "va", "vaki", "ti", "ta", "to", "o", "ni", "na", "lo",
    "కి", "కు", "లో", "తో", "ని", "న", "క",
    "को", "में", "से", "का", "की", "के",
    "க்கு", "இல்", "ல்", "ஐ", "ஆல்",
    "ಗೆ", "ಗೂ", "ನಲ್ಲಿ", "ನ್ನು", "ಿಂದ",
    "ലേക്ക്", "ൽ", "ന്", "ഇൽ",
    "ला", "मध्ये", "ने", "चा", "ची",
    "কে", "তে", "এ",
    "ને", "માં", "થી", "નો", "ની",
    "ਨੂੰ", "ਵਿੱਚ", "ਤੋਂ", "ਦਾ", "ਦੀ",
}

MIN_PREFIX_STEM = 3


def _word_value_with_suffix(word: str) -> int | None:
    exact = _word_value(word)
    if exact is not None:
        return exact

    for table in NUMBER_TABLES:
        for stem in sorted(table, key=len, reverse=True):
            if len(stem) < MIN_PREFIX_STEM:
                continue
            if word.startswith(stem) and word[len(stem):] in NUMBER_SUFFIXES:
                return table[stem]

    return None


def _number_from_words(words: list[str]) -> str | None:
    """Read one number from the start of a word list, preserving leading zeros."""
    if not words:
        return None
    first = _word_value_with_suffix(words[0])
    if first is None:
        return None

    if first in (20, 30, 40, 50, 60, 70, 80, 90) and len(words) > 1:
        unit = _word_value_with_suffix(words[1])
        if unit is not None and 1 <= unit <= 9:
            return str(first + unit)

    if 0 <= first <= 9:
        digits = [str(first)]
        for word in words[1:]:
            value = _word_value_with_suffix(word)
            if value is None or not 0 <= value <= 9:
                break
            digits.append(str(value))
        return "".join(digits)

    return str(first)


def _spoken_digit_runs(words: list[str]) -> list[list[int]]:
    """
    Find runs of CONSECUTIVE single-digit words or ASCII digits.

    Consecutive is the whole point. "nine one two seven" scattered through a
    sentence about money is four digits, not five, and must not become a train
    number. Only an unbroken run is considered.
    """
    runs: list[list[int]] = []
    current: list[int] = []

    for word in words:
        value = _word_value(word)
        if value is None and word in ("oh", "o"):
            value = 0
        if value is None and len(word) == 1 and word.isascii() and word.isdigit():
            value = int(word)
        if value is not None and 0 <= value <= 9:
            current.append(value)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    return runs


def _merge_spaced_single_digits(text: str) -> str:
    """Join runs of single ASCII digits: '9 1 2 7 3' -> '91273'."""
    tokens = text.split()
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isascii() and token.isdigit() and len(token) == 1:
            run = []
            while (
                index < len(tokens)
                and tokens[index].isascii()
                and tokens[index].isdigit()
                and len(tokens[index]) == 1
            ):
                run.append(tokens[index])
                index += 1
            merged.append("".join(run) if len(run) > 1 else run[0])
        else:
            merged.append(token)
            index += 1
    return " ".join(merged)


def find_train_no(text: str) -> str | None:
    """Exactly five digits, as numerals or as five spoken digits in a row."""
    clean = _normalise(text)

    numeric = re.findall(r"\b(\d{5})\b", _merge_spaced_single_digits(clean))
    if len(numeric) == 1:
        return numeric[0]
    if len(numeric) > 1:
        return None

    runs = _spoken_digit_runs(clean.split())
    exact = [run for run in runs if len(run) == TRAIN_NO_DIGITS]
    if len(exact) == 1:
        return "".join(str(digit) for digit in exact[0])

    return None


def has_train_context(text: str, train_no: str) -> bool:
    """Require a nearby train cue before treating a five-digit number as a train."""
    from app.matcher import normalize_text

    words = normalize_text(text).split()
    cues = {
        word
        for cue in set(TRAIN_WORDS)
        for word in normalize_text(cue).split()
    }
    positions = [index for index, word in enumerate(words) if word == train_no]
    return any(
        any(word in cues for word in words[max(0, index - 3):index + 2])
        for index in positions
    )


def _name_tokens(tokens: list[str]) -> list[str]:
    collected = []
    for token in tokens:
        if len(collected) >= MAX_TRAIN_NAME_WORDS:
            break
        if token in TRAIN_NAME_STOPWORDS:
            break
        if token.isdigit() or not any(char.isalpha() for char in token):
            break
        collected.append(token)
    return collected


def _name_is_supported(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    if len(tokens) >= 2:
        return " ".join(tokens)
    single = tokens[0]
    if any(single.endswith(suffix) for suffix in TRAIN_NAME_SUFFIXES):
        return single
    return None


def find_train_name(text: str) -> str | None:
    """
    Return the name between the train number and a platform/direction cue.

    The name is only returned when it has at least two words, or one word that
    is an obvious train-name suffix. A single unknown word could be a city, and
    calling a destination a train name is a wrong answer.
    """
    from app.matcher import normalize_text

    normalised = normalize_text(text)
    match = re.search(r"\b\d{5}\b", normalised)
    if not match:
        return None

    after = normalised[match.end():].split()
    while after and after[0] in TRAIN_NAME_LEAD:
        after.pop(0)
    name = _name_is_supported(_name_tokens(after))
    if name:
        return name

    before = normalised[:match.start()].split()
    while before and before[-1] in TRAIN_NAME_LEAD:
        before.pop()
    name = _name_is_supported(_name_tokens(list(reversed(before)))[::-1])
    if name:
        return name

    return None


def _platform_after(words: list[str]) -> str | None:
    for position, token in enumerate(words[:4]):
        digits = re.fullmatch(r"(\d{1,2})", token)
        if digits:
            value = int(digits.group(1))
            return str(value) if PLATFORM_MIN <= value <= PLATFORM_MAX else None

        value = _word_value_with_suffix(token)
        if value is not None:
            if value in (20, 30) and position + 1 < len(words):
                unit = _word_value_with_suffix(words[position + 1])
                if unit is not None and 1 <= unit <= 9:
                    value += unit
            return str(value) if PLATFORM_MIN <= value <= PLATFORM_MAX else None

        if token not in PLATFORM_CONNECTORS and token not in PLATFORM_WORDS:
            return None
    return None


def _platform_before(words: list[str]) -> str | None:
    for token in reversed(words[-3:]):
        digits = re.fullmatch(r"(\d{1,2})", token)
        if digits:
            value = int(digits.group(1))
            if PLATFORM_MIN <= value <= PLATFORM_MAX:
                return str(value)
            continue
        value = _word_value_with_suffix(token)
        if value is not None and PLATFORM_MIN <= value <= PLATFORM_MAX:
            return str(value)
    return None


def find_platform(text: str) -> str | None:
    """
    A number that appears NEAR a platform word. Proximity is required — a bare
    number anywhere in the sentence is not a platform.
    """
    clean = _normalise(text)
    candidates: list[str] = []
    for match in _PLATFORM_RE.finditer(clean):
        after = clean[match.end():].split()
        value = _platform_after(after)
        if value is None:
            value = _platform_before(clean[:match.start()].split())
        if value is not None:
            candidates.append(value)

    distinct = set(candidates)
    if len(distinct) == 1:
        return distinct.pop()
    return None


def _token_at_start(text: str) -> str | None:
    """Read a token (digits, or one/two letters + digits) from a phrase start."""
    from app.matcher import normalize_text

    raw_words = _normalise(text).split()
    while raw_words and raw_words[0] in NUMBER_CONNECTORS:
        raw_words.pop(0)
    if not raw_words:
        return None

    normalized = normalize_text(" ".join(raw_words)).strip()
    match = re.match(r"^([A-Za-z]{1,2})\s*-?\s*(\d+)", normalized)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"

    match = re.match(r"^(\d+)", normalized)
    if match:
        return match.group(1)

    if re.fullmatch(r"[A-Za-z]{1,2}", raw_words[0]) and len(raw_words) > 1:
        number = _number_from_words(raw_words[1:])
        if number:
            return f"{raw_words[0].upper()}-{number}"

    return _number_from_words(raw_words)


_TOKEN_KEY_RE = re.compile(r"^([A-Za-z]{1,2})?-?(\d+)$")


def token_key(value: str) -> tuple[str | None, str] | None:
    raw = to_ascii_digits(str(value or "")).strip()
    raw = re.sub(r"[\s\-]+", "", raw)
    match = _TOKEN_KEY_RE.fullmatch(raw)
    if not match:
        return None
    letter, digits = match.groups()
    return (letter.upper() if letter else None, digits)


def token_matches(configured: str, heard: str) -> bool:
    expected = token_key(configured)
    actual = token_key(heard)
    if expected is None or actual is None:
        return False
    expected_letter, expected_number = expected
    actual_letter, actual_number = actual
    if expected_number != actual_number:
        return False
    if expected_letter is None:
        return True
    return expected_letter == actual_letter


def find_tokens(text: str) -> list[dict[str, str | None]]:
    """Return token announcements with their room, if one is clearly present."""
    clean = _normalise(text)
    markers = list(_TOKEN_RE.finditer(clean))

    announcements = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(clean)
        tail = clean[marker.end():end]
        token = _token_at_start(tail)
        if token is None:
            continue

        room = find_room(tail)
        if room is None:
            start = markers[index - 1].end() if index > 0 else 0
            room = find_room(clean[start:marker.start()])

        announcements.append({"token": token, "room": room})
    return announcements


def find_token(text: str) -> str | None:
    """Return a hospital token only when one candidate is unambiguous."""
    announcements = find_tokens(text)
    if len(announcements) != 1:
        return None
    return announcements[0]["token"]


def find_room(text: str) -> str | None:
    """Return a room number only when every room cue points to the same value."""
    clean = _normalise(text)
    found: list[str] = []
    for match in _ROOM_RE.finditer(clean):
        words = clean[match.end():].split()
        while words and words[0] in NUMBER_CONNECTORS:
            words.pop(0)
        if not words:
            continue
        number = _number_at_start(" ".join(words[:8]))
        if number:
            found.append(number)

    distinct = set(found)
    if len(distinct) == 1:
        return distinct.pop()
    return None


def _number_at_start(text: str) -> str | None:
    from app.matcher import normalize_text

    normalized = normalize_text(text).split()
    if normalized and normalized[0].isdigit():
        return normalized[0]

    raw_words = _normalise(text).split()
    while raw_words and raw_words[0] in NUMBER_CONNECTORS:
        raw_words.pop(0)
    return _number_from_words(raw_words)


_SPECIALIST_SUFFIX_RE = re.compile(
    r"(ology|ologist|iatrics|iatrician|pedics|pediatrician|iatry|iatrist|"
    r"otherapy|otherapist|surgery|surgeon|pedic)$"
)


def _specialist_allowed(token: str) -> bool:
    return (
        not token.isdigit()
        and _word_value(token) is None
        and any(char.isalpha() for char in token)
    )


def find_specialist(text: str) -> str | None:
    """
    Return a department/specialist name when the words around it support one.

    A bare English word ending in a medical suffix is accepted only when the
    transcript also contains a hospital cue, so "technology" in an unrelated
    announcement is not reported as a specialist.
    """
    clean = _normalise(text)
    tokens = clean.split()

    has_hospital_cue = (
        any(token in HOSPITAL_CUES for token in tokens)
        or _TOKEN_RE.search(clean) is not None
        or _ROOM_RE.search(clean) is not None
    )
    if has_hospital_cue:
        for token in tokens:
            if _SPECIALIST_SUFFIX_RE.search(token) and _specialist_allowed(token):
                return token

    for index, token in enumerate(tokens):
        if token not in DEPARTMENT_MARKERS:
            continue

        before: list[str] = []
        cursor = index - 1
        while cursor >= 0 and len(before) < 2:
            candidate = tokens[cursor]
            if (
                candidate in DEPARTMENT_FILLERS
                or candidate in SPECIALIST_BLOCKERS
                or not _specialist_allowed(candidate)
            ):
                break
            before.insert(0, candidate)
            cursor -= 1

        before_text = " ".join(before)
        if before_text and not (len(before) == 1 and before[0] in SPECIALIST_GENERIC):
            return before_text

        after: list[str] = []
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor] in DEPARTMENT_FILLERS:
            if tokens[cursor] in SPECIALIST_BLOCKERS:
                break
            cursor += 1
        while cursor < len(tokens) and len(after) < 2:
            candidate = tokens[cursor]
            if candidate in SPECIALIST_BLOCKERS or not _specialist_allowed(candidate):
                break
            after.append(candidate)
            cursor += 1

        after_text = " ".join(after)
        if after_text and not (len(after) == 1 and after[0] in SPECIALIST_GENERIC):
            return after_text

    return None


def find_destination(text: str, stations: list[str] | None = None) -> str | None:
    """
    Match against a KNOWN list of station names. Never returns a guess — if the
    caller supplies no list, this returns None, because an unknown place name
    cannot be verified.
    """
    if not stations:
        return None

    candidates = find_destination_candidates(text, stations)
    known = {
        _normalise(entry["text"]): entry["text"]
        for entry in candidates if entry["source"] == "known"
    }
    return next(iter(known.values())) if len(known) == 1 else None


BUS_PLACE_STOPWORDS = frozenset({
    "a", "all", "an", "and", "announcement", "arrival", "arrivals", "arrive",
    "arrives", "arriving", "at", "attention", "bay", "boarding", "bus", "buses",
    "by", "city", "daily", "dear", "depart", "departs", "departure",
    "destination", "express", "for", "from", "gate", "go", "goes", "going",
    "has", "have", "in", "is", "near", "no", "now", "number", "of", "on",
    "ordinary", "passenger", "passengers", "platform", "please", "route",
    "schedule", "service", "services", "stand", "station", "stop", "terminal",
    "that", "the", "this", "time", "to", "town", "towards", "train", "trains",
    "via", "village", "will", "your",
})


def _place_tokens(text: str) -> list[str]:
    clean = _normalise(text)
    tokens = []
    for token in clean.split():
        token = token.strip("!?;:\"'()[]{}")
        if token and any(ch.isalpha() for ch in token):
            tokens.append(token)
    return tokens


def find_destination_candidates(
    text: str,
    stations: list[str] | None = None,
    max_words: int = MAX_PLACE_WORDS,
) -> list[dict[str, str]]:
    """Return plausible place-name candidates without requiring a known list."""
    if not text or not text.strip():
        return []

    tokens = _place_tokens(text)
    if not tokens or not _TRANSIT_WORDS.intersection(tokens):
        return []

    # A place is a destination only when it follows an explicit direction cue.
    # Do not turn nearby speech or a place mentioned as context into a route.
    phrases: list[list[str]] = []
    for index, word in enumerate(tokens):
        if word not in _DESTINATION_CONNECTORS:
            continue
        phrase: list[str] = []
        for candidate in tokens[index + 1:index + 1 + max_words]:
            if candidate in BUS_PLACE_STOPWORDS:
                break
            phrase.append(candidate)
        if phrase:
            phrases.append(phrase)

    if not phrases:
        return []

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(value: str, source: str) -> None:
        value = " ".join(value.split())
        key = _normalise(value)
        if len(key.replace(" ", "")) < 3 or not any(ch.isalpha() for ch in key):
            return
        if key in seen:
            return
        seen.add(key)
        candidates.append({"text": value, "source": source})

    for phrase in phrases:
        phrase_text = " ".join(phrase)
        if stations:
            for station in sorted(stations, key=len, reverse=True):
                needle = _normalise(station)
                if needle and re.search(
                    rf"(?<!\w){re.escape(needle)}(?!\w)",
                    _normalise(phrase_text),
                ):
                    add(station, "known")
        add(phrase_text, "transcript")

    return candidates


def extract(text: str, stations: list[str] | None = None) -> dict:
    """Pull every identifier we can justify from one transcript."""
    if not text or not text.strip():
        return {
            "train_no": None, "train_name": None, "platform": None,
            "destination": None, "token": None, "room": None,
            "specialist": None, "destination_candidates": [],
        }

    return {
        "train_no": find_train_no(text),
        "train_name": find_train_name(text),
        "platform": find_platform(text),
        "destination": find_destination(text, stations),
        "token": find_token(text),
        "room": find_room(text),
        "specialist": find_specialist(text),
        "destination_candidates": find_destination_candidates(text, stations),
    }


if __name__ == "__main__":
    STATIONS = ["Hyderabad", "Vijayawada", "Secunderabad", "Tenali", "Chennai"]

    CASES = [
        ("Train number 12739 Garib Rath to Hyderabad will arrive on platform 4",
         "12739", "4"),
        ("12739 nambaru railu platform naluguki vastundi",
         "12739", "4"),
        ("Attention train one two seven three nine is delayed",
         "12739", None),
        ("nine one two seven three",
         "91273", None),
        ("రైలు నంబర్ 12739 ప్లాట్ఫారం రెండు కి వస్తుంది",
         "12739", "2"),
        ("train 17201 arriving platform 12",
         "17201", "12"),
        ("The canteen on platform 2 is now open",
         None, "2"),
        ("money is gone nine one two seven",
         None, None),
        ("there is no one here",
         None, None),
        ("stone alone phone money",
         None, None),
        ("trains 12739 and 17201 are both delayed",
         None, None),
        ("platform 99 does not exist",
         None, None),
        ("", None, None),
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
