"""
watch.py — what the user is waiting for.

The user declares one thing at the start:

    "my token is 47"            -> Watch("token", "47")
    "I'm going to Hyderabad"    -> Watch("bus", "Hyderabad")
    "train 12739"               -> Watch("train", "12739")

Every announcement is then checked against that one thing. Everything else in
the hall is somebody else's business.

Matching rule, in priority order:

    1. Exact identifier match  — train number, token. Never fuzzy. A digit is
       either right or it is not.
    2. Fuzzy place-name match  — only for destinations, because STT mangles
       place names constantly ("hydrabad", "hyderbad", "haidarabad") while it
       rarely mangles a digit into another valid digit string.

That split is deliberate. Being loose about place names costs the user a
glance at the board. Being loose about numbers sends them to the wrong
platform.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.langdata import strip_zero_width, to_ascii_digits

# Standard library only - no pip install, so this runs anywhere Python runs.

#: Two place names this similar are treated as the same place.
FUZZY_THRESHOLD = 0.80

KINDS = ("train", "token", "bus")

_PLACE_SHORT = 4
_PLACE_MIN_PARTIAL = 4
_PLACE_PARTIAL_RATIO = 0.55
_PLACE_SIMILARITY = 0.82
_PLACE_EXTRA_LENGTH_RATIO = 0.70
_PLACE_AMBIGUITY_MARGIN = 0.15
_PLACE_MIN_PHONETIC = 3
_PLACE_PHONETIC_SIMILARITY = 0.65

#: Generic place-type words. These are NOT destination names and are not a list
#: of spelling variants: they are the ordinary words ("Town", "Junction") that
#: surround a real place name. Comparing the distinctive part of a name means
#: "Kurnool Town" still matches a heard "Kurnool", while two different
#: distinctive names are never matched on a shared generic word alone.
_PLACE_GENERIC = frozenset({
    "town", "city", "village", "nagar", "puram", "pura", "peta", "pet",
    "palli", "palle", "gudem", "guda", "patnam", "junction", "jn", "station",
    "stand", "stop", "terminal", "road", "colony", "market", "centre",
    "center", "temple", "bus",
    # Telugu
    "బస్", "బస్సు", "స్టాండ్", "స్టాప్", "స్టేషన్", "టర్మినల్",
    "జంక్షన్", "రోడ్", "టౌన్", "సిటీ", "గ్రామం", "నగర్", "నగరం",
    "పురం", "పేట", "పల్లి", "గూడెం", "పట్నం", "కాలనీ", "మార్కెట్", "సెంటర్",
    # Hindi
    "बस", "स्टैंड", "स्टॉप", "स्टेशन", "टर्मिनल", "जंक्शन", "रोड",
    "टाउन", "सिटी", "गांव", "गाँव", "नगर", "पुरम", "पेटा", "पल्ली",
    "गुडेम", "पटनम", "कॉलोनी", "मार्केट", "सेंटर",
    # Tamil
    "பஸ்", "பேருந்து", "நிலையம்", "நிறுத்தம்", "சந்திப்பு", "சாலை",
    "நகரம்", "ஊர்", "கிராமம்", "நகர்", "புரம்", "பேட்டை", "சந்தை", "மையம்",
    # Kannada
    "ಬಸ್", "ನಿಲ್ದಾಣ", "ನಿಲುಗಡೆ", "ಜಂಕ್ಷನ್", "ರಸ್ತೆ", "ಪಟ್ಟಣ",
    "ನಗರ", "ಹಳ್ಳಿ", "ಗ್ರಾಮ", "ಪುರ", "ಪೇಟೆ", "ಕಾಲೋನಿ", "ಮಾರುಕಟ್ಟೆ", "ಕೇಂದ್ರ",
    # Malayalam
    "ബസ്", "സ്റ്റാൻഡ്", "സ്റ്റോപ്പ്", "സ്റ്റേഷൻ", "ജംഗ്ഷൻ", "റോഡ്",
    "ടൗൺ", "സിറ്റി", "ഗ്രാമം", "നഗർ", "പുരം", "പേട്ട", "പള്ളി",
    "കോളനി", "മാർക്കറ്റ്", "സെന്റർ",
    # Marathi
    "बस", "स्टँड", "थांबा", "स्टेशन", "जंक्शन", "रस्ता", "शहर",
    "गाव", "नगर", "पुरम", "पेठ", "पट्टण", "वसाहत", "बाजार", "केंद्र", "नाका",
    # Bengali
    "বাস", "স্ট্যান্ড", "স্টপ", "স্টেশন", "জংশন", "রোড", "শহর",
    "গ্রাম", "নগর", "পুর", "পাড়া", "কলোনি", "বাজার", "কেন্দ্র",
    # Gujarati
    "બસ", "સ્ટેન્ડ", "સ્ટોપ", "સ્ટેશન", "જંકશન", "રોડ", "શહેર",
    "ગામ", "નગર", "પુર", "પેટા", "કોલોની", "બજાર", "કેન્દ્ર",
    # Punjabi
    "ਬਸ", "ਸਟੈਂਡ", "ਸਟਾਪ", "ਸਟੇਸ਼ਨ", "ਜੰਕਸ਼ਨ", "ਸੜਕ", "ਸ਼ਹਿਰ",
    "ਪਿੰਡ", "ਨਗਰ", "ਪੁਰ", "ਪੇਟ", "ਕਾਲੋਨੀ", "ਬਾਜ਼ਾਰ", "ਕੇਂਦਰ",
})

_INDIC_BLOCKS = (
    (0x0900, 0x097F),
    (0x0980, 0x09FF),
    (0x0A00, 0x0A7F),
    (0x0A80, 0x0AFF),
    (0x0B00, 0x0B7F),
    (0x0B80, 0x0BFF),
    (0x0C00, 0x0C7F),
    (0x0C80, 0x0CFF),
    (0x0D00, 0x0D7F),
    (0x0D80, 0x0DFF),
)

_ROMAN_VOWELS = frozenset({
    "A", "AA", "I", "II", "U", "UU", "R", "RR", "L", "LL",
    "E", "EE", "AI", "O", "OO", "AU",
})

_ROMAN_VOWEL_SIGNS = {
    "A": "a", "AA": "a", "I": "i", "II": "i", "U": "u", "UU": "u",
    "R": "r", "RR": "r", "L": "l", "LL": "l", "E": "e", "EE": "e",
    "AI": "ai", "O": "o", "OO": "o", "AU": "au",
}

_ROMAN_CONSONANTS = {
    "K": "k", "G": "g", "C": "c", "J": "j", "T": "t", "D": "d",
    "N": "n", "P": "p", "B": "b", "M": "m", "Y": "y", "R": "r",
    "L": "l", "V": "v", "S": "s", "H": "h", "F": "f", "Z": "s",
    "Q": "k", "X": "ks",
}


_PLACE_GENERIC_CANONICAL = {
    "jn": "junction",
    "jcn": "junction",
    "jct": "junction",
}


def _core_place(text: str) -> str:
    """Drop generic place-type words, keeping the distinctive name."""
    tokens = [token for token in text.split() if token not in _PLACE_GENERIC]
    return " ".join(tokens) if tokens else text


def _generic_tokens(text: str) -> set[str]:
    """Canonical generic words present in a place name."""
    return {
        _PLACE_GENERIC_CANONICAL.get(token, token)
        for token in text.split()
        if token in _PLACE_GENERIC
    }


def similarity(a: str, b: str) -> float:
    """0.0 to 1.0. Case and surrounding spaces are ignored."""
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def fuzzy_equal(a: str, b: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    """True when two place names are close enough to be the same place."""
    if not a or not b:
        return False
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l == b_l:
        return True
    # A short string inside a longer one, e.g. "hyderabad" in "hyderabad decan".
    if len(a_l) >= 5 and len(b_l) >= 5 and (a_l in b_l or b_l in a_l):
        return True
    return similarity(a_l, b_l) >= threshold


def _normalise_place(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = strip_zero_width(text)
    text = to_ascii_digits(text)
    text = text.casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[\u2010-\u2015\-_/\\]", " ", text)
    text = re.sub(r"[.,;:!?\"'()\[\]{}]", " ", text)
    text = text.replace("।", " ").replace("॥", " ")
    if all(ord(ch) < 0x0900 for ch in text if ch.isalpha()):
        text = "".join(
            ch for ch in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(ch)
        )
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left != right),
            ))
        previous = current
    return previous[-1]


def _common_prefix(a: str, b: str) -> int:
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def _common_suffix(a: str, b: str) -> int:
    count = 0
    for left, right in zip(reversed(a), reversed(b)):
        if left != right:
            break
        count += 1
    return count


def _phonetic_key(value: str) -> str:
    text = re.sub(r"[^a-z]", "", _normalise_place(value))
    if not text:
        return ""
    for old, new in (
        ("ph", "f"), ("gh", "g"), ("kh", "k"), ("th", "t"),
        ("dh", "d"), ("bh", "b"), ("ch", "c"), ("sh", "s"),
        ("zh", "l"), ("w", "v"), ("q", "k"), ("x", "ks"), ("z", "s"),
    ):
        text = text.replace(old, new)
    text = re.sub(r"(.)\1+", r"\1", text)
    return re.sub(r"[aeiouy]", "", text)


def _script_block(text: str) -> int:
    """Return the Unicode block start for the first alphabetic character."""
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        for start, end in _INDIC_BLOCKS:
            if start <= code <= end:
                return start
        return 0
    return 0


def _has_indic(text: str) -> bool:
    return _script_block(text) != 0


def _indic_romanise(value: str) -> str:
    """Approximate Latin spelling for any supported Indic script.

    Unicode names are systematic ("TELUGU LETTER DDA", "DEVANAGARI VOWEL SIGN
    AA"), so this stays generic instead of keeping ten transliteration tables.
    It is intentionally approximate: it is only one signal in a conservative
    cross-script check, never an exact-match substitute.
    """
    text = _normalise_place(value)
    chars = list(text)
    output: list[str] = []
    for index, char in enumerate(chars):
        if char.isascii() and char.isalpha():
            output.append(char)
            continue

        name = unicodedata.name(char, "")
        if "VIRAMA" in name or "ADDAK" in name or "NUKTA" in name:
            continue
        if "VOWEL SIGN" in name:
            suffix = name.split("VOWEL SIGN", 1)[1].strip()
            output.append(_ROMAN_VOWEL_SIGNS.get(suffix, suffix.lower()))
            continue
        if "LETTER" in name:
            suffix = name.rsplit("LETTER", 1)[1].strip()
            if suffix in _ROMAN_VOWELS:
                output.append(_ROMAN_VOWEL_SIGNS.get(suffix, suffix.lower()))
                continue
            if suffix.startswith("CHILLU"):
                suffix = suffix.split()[-1]
            consonant = _ROMAN_CONSONANTS.get(suffix[0], "")
            following = chars[index + 1] if index + 1 < len(chars) else ""
            following_name = unicodedata.name(following, "") if following else ""
            if "VIRAMA" in following_name or "VOWEL SIGN" in following_name:
                output.append(consonant)
            else:
                output.append(consonant + "a")
            continue
        if "ANUSVARA" in name or "CANDRABINDU" in name:
            output.append("n")
            continue
        if "VISARGA" in name:
            output.append("h")
    return "".join(output)


def _romanised_key(value: str) -> str:
    """Vowel-preserving, voicing-tolerant key used for cross-script names."""
    text = _indic_romanise(value)
    for old, new in (
        ("ph", "f"), ("gh", "g"), ("kh", "k"), ("th", "t"),
        ("dh", "d"), ("bh", "b"), ("jh", "j"), ("ch", "c"),
        ("sh", "s"), ("zh", "l"), ("w", "v"), ("q", "k"),
        ("x", "ks"), ("z", "s"),
    ):
        text = text.replace(old, new)
    text = re.sub(r"(.)\1+", r"\1", text)
    text = re.sub(r"aa+", "a", text)
    text = re.sub(r"ii+", "i", text)
    text = re.sub(r"uu+", "u", text)
    for old, new in (("b", "p"), ("d", "t"), ("g", "k"), ("j", "c")):
        text = text.replace(old, new)
    return text


def _cross_script_confidence(target: str, candidate: str) -> float | None:
    """High-confidence bridge between Latin and Indic spellings."""
    target_key = _romanised_key(target)
    candidate_key = _romanised_key(candidate)
    if len(target_key) < 4 or len(candidate_key) < 4:
        return None

    ratio = SequenceMatcher(None, target_key, candidate_key).ratio()
    shorter, longer = sorted((target_key, candidate_key), key=len)
    contained = len(shorter) >= 4 and shorter in longer
    containment = len(shorter) / len(longer) if contained else 0.0

    if ratio >= 0.78 or (contained and containment >= _PLACE_PARTIAL_RATIO):
        best = max(ratio, containment)
        return round(min(0.95, 0.60 + 0.40 * best), 3)
    return None


def _token_score(target: str, candidate: str) -> float:
    """Score one word against another, tolerating a truncated word."""
    if target == candidate:
        return 1.0
    score = SequenceMatcher(None, target, candidate).ratio()
    shorter, longer = sorted((target, candidate), key=len)
    if len(shorter) >= _PLACE_MIN_PARTIAL and shorter in longer:
        score = max(score, len(shorter) / len(longer))
    return score


def _token_details(target_tokens: list[str], candidate_tokens: list[str]) -> tuple[float, float, float]:
    if not target_tokens or not candidate_tokens:
        return 0.0, 0.0, 0.0
    used: set[int] = set()
    scores: list[float] = []
    for target in target_tokens:
        best_score = 0.0
        best_index = None
        for index, candidate in enumerate(candidate_tokens):
            if index in used:
                continue
            score = _token_score(target, candidate)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is not None:
            used.add(best_index)
        scores.append(best_score)
    average = sum(scores) / len(scores)
    strong = sum(1 for score in scores if score >= 0.78)
    return average, strong / len(target_tokens), strong / len(candidate_tokens)


def place_match(target: str, candidate: str) -> dict:
    target_text = _normalise_place(target)
    candidate_text = _normalise_place(candidate)
    empty = {
        "verdict": "none", "candidate": str(candidate or ""),
        "target": target_text, "confidence": 0.0,
    }
    if not target_text or not candidate_text:
        return empty

    # 1. Exact match always wins, before any fuzzy reasoning.
    if target_text == candidate_text or _compact(target_text) == _compact(candidate_text):
        return {
            "verdict": "exact", "candidate": str(candidate),
            "target": target_text, "confidence": 1.0,
        }

    # 2. Compare the distinctive part of the name. "Kurnool Town" and a heard
    #    "Kurnool" are the same place; "Karimnagar" and "Srinagar" are not.
    target_core = _core_place(target_text)
    candidate_core = _core_place(candidate_text)
    if target_core == candidate_core:
        target_generic = _generic_tokens(target_text)
        candidate_generic = _generic_tokens(candidate_text)
        # "Kurnool Town" and "Kurnool" are the same place, and "Junction" /
        # "Jn" are the same label. "Gandhi Nagar" and "Gandhi Puram" are not:
        # the differing suffix is the only thing that tells them apart.
        if target_generic <= candidate_generic or candidate_generic <= target_generic:
            return {
                "verdict": "fuzzy", "candidate": str(candidate),
                "target": target_text, "confidence": 0.98,
            }

    # 3. Latin <-> Indic (and different Indic scripts) need a romanised bridge.
    #    Direct character comparison cannot see that "Duggirala" and
    #    "డుగ్గిరాల" are the same word.
    target_block = _script_block(target_text)
    candidate_block = _script_block(candidate_text)
    if target_block != candidate_block and (target_block or candidate_block):
        confidence = _cross_script_confidence(target_text, candidate_text)
        if confidence is not None:
            return {
                "verdict": "fuzzy", "candidate": str(candidate),
                "target": target_text, "confidence": confidence,
            }

    target_compact = _compact(target_core)
    candidate_compact = _compact(candidate_core)
    if not target_compact or not candidate_compact:
        return empty

    target_length = len(target_compact)
    candidate_length = len(candidate_compact)
    if min(target_length, candidate_length) < 3:
        return empty

    char_ratio = SequenceMatcher(None, target_compact, candidate_compact).ratio()
    distance = _levenshtein(target_compact, candidate_compact)
    edit_ratio = 1.0 - distance / max(target_length, candidate_length)
    similarity_score = max(char_ratio, edit_ratio)
    length_ratio = min(target_length, candidate_length) / max(target_length, candidate_length)
    partial_ratio = max(
        _common_prefix(target_compact, candidate_compact),
        _common_suffix(target_compact, candidate_compact),
    ) / max(target_length, candidate_length)

    # A contiguous run of characters kept from the longer name. This is what
    # speech recognition leaves behind when it drops the start, middle or end
    # of a word ("Duggirala" -> "Girala" / "Irala"). A divergent spelling with
    # scattered shared letters ("Suryapet" -> "Surat") is NOT a substring and
    # must not be accepted on length alone.
    longer, shorter = sorted((target_compact, candidate_compact), key=len, reverse=True)
    contained = len(shorter) >= _PLACE_MIN_PARTIAL and shorter in longer
    containment_ratio = len(shorter) / len(longer)

    target_tokens = target_core.split()
    candidate_tokens = candidate_core.split()
    token_average, target_coverage, candidate_coverage = _token_details(
        target_tokens, candidate_tokens
    )

    target_phonetic = _phonetic_key(target_core)
    candidate_phonetic = _phonetic_key(candidate_core)
    phonetic_score = 0.0
    if target_phonetic and candidate_phonetic:
        if target_phonetic == candidate_phonetic:
            phonetic_score = 1.0
        elif min(len(target_phonetic), len(candidate_phonetic)) >= 3:
            short_phonetic, long_phonetic = sorted(
                (target_phonetic, candidate_phonetic), key=len
            )
            if long_phonetic.startswith(short_phonetic) or long_phonetic.endswith(short_phonetic):
                phonetic_score = len(short_phonetic) / len(long_phonetic)

    accepted = False
    confidence = max(similarity_score, partial_ratio)

    if len(target_tokens) == 1 and len(candidate_tokens) == 1:
        if target_length <= 2:
            accepted = False
        elif target_length <= _PLACE_SHORT:
            # Short names are dangerous: require an almost exact spelling.
            accepted = (
                similarity_score >= 0.82
                and distance <= 1
                and length_ratio >= 0.75
            )
        elif candidate_length < _PLACE_MIN_PARTIAL:
            accepted = False
        elif length_ratio < _PLACE_PARTIAL_RATIO:
            accepted = False
        elif (
            candidate_length > target_length
            and target_length / candidate_length < _PLACE_EXTRA_LENGTH_RATIO
        ):
            accepted = False
        else:
            truncation_ok = contained and containment_ratio >= _PLACE_PARTIAL_RATIO
            single_edit_ok = (
                distance <= 1
                and length_ratio >= 0.75
                and min(target_length, candidate_length) >= _PLACE_MIN_PARTIAL
            )
            strong_ok = similarity_score >= _PLACE_SIMILARITY
            phonetic_ok = (
                phonetic_score >= 0.75
                and similarity_score >= _PLACE_PHONETIC_SIMILARITY
                and min(len(target_phonetic), len(candidate_phonetic)) >= _PLACE_MIN_PHONETIC
                and length_ratio >= 0.60
            )
            accepted = truncation_ok or single_edit_ok or strong_ok or phonetic_ok
            if phonetic_ok:
                confidence = max(confidence, min(0.90, similarity_score + 0.05 * phonetic_score))
    elif len(target_tokens) > 1:
        accepted = (
            (target_coverage >= 0.60 and candidate_coverage >= 0.60 and similarity_score >= 0.65)
            or similarity_score >= 0.90
        )
        confidence = max(similarity_score, token_average * 0.95)
    else:
        accepted = (
            (target_coverage >= 1.0 and candidate_coverage >= 0.75 and similarity_score >= 0.70)
            or similarity_score >= 0.90
        )
        confidence = max(similarity_score, token_average * 0.95)

    if not accepted:
        return {
            "verdict": "none", "candidate": str(candidate),
            "target": target_text, "confidence": round(confidence, 3),
        }
    return {
        "verdict": "fuzzy", "candidate": str(candidate),
        "target": target_text, "confidence": round(min(confidence, 0.99), 3),
    }


def best_place_match(target: str, candidates: list) -> dict:
    results = []
    for entry in candidates or []:
        if isinstance(entry, dict):
            candidate = entry.get("text") or ""
            source = entry.get("source") or ""
        else:
            candidate = str(entry)
            source = ""
        result = place_match(target, candidate)
        result["source"] = source
        if result["verdict"] in ("exact", "fuzzy"):
            results.append(result)

    if not results:
        return {
            "verdict": "none", "candidate": None,
            "confidence": 0.0, "alternatives": [],
        }

    exact = [result for result in results if result["verdict"] == "exact"]
    if exact:
        return {**exact[0], "alternatives": results}

    results.sort(key=lambda result: result["confidence"], reverse=True)
    top = results[0]
    if len(results) > 1:
        second = results[1]
        top_name = _core_place(_normalise_place(top["candidate"]))
        second_name = _core_place(_normalise_place(second["candidate"]))
        if (
            top_name != second_name
            and top["confidence"] - second["confidence"] < _PLACE_AMBIGUITY_MARGIN
        ):
            return {
                "verdict": "ambiguous", "candidate": top["candidate"],
                "confidence": top["confidence"], "alternatives": results,
            }
    return {**top, "alternatives": results}


class Watch:
    """One thing the user is waiting for."""

    def __init__(self, kind: str, value: str, label: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        if not value or not str(value).strip():
            raise ValueError("a watch needs something to watch for")
        self.kind = kind
        self.value = str(value).strip()
        from app.extract import token_key
        if kind == "train" and (
            not self.value.isascii()
            or not self.value.isdigit()
            or len(self.value) != 5
        ):
            raise ValueError("train watch values must be exactly five digits")
        if kind == "token" and token_key(self.value) is None:
            raise ValueError("token watch values must be digits, optionally with a block letter")
        self.label = label or self.value

    # -- matching ---------------------------------------------------------

    def matches(self, slots: dict) -> bool:
        """True when this announcement is the one the user is waiting for."""
        if not slots:
            return False

        if self.kind == "train":
            return self._match_train(slots)
        if self.kind == "token":
            return self._match_token(slots)
        if self.kind == "bus":
            return self._match_place(slots)
        return False

    def _match_train(self, slots: dict) -> bool:
        train_no = slots.get("train_no")

        # A number was heard. It decides the answer either way — if the
        # announcement names a different train, this is not our announcement,
        # even if the destination happens to match.
        if train_no:
            return str(train_no) == self.value

        return False

    def _match_token(self, slots: dict) -> bool:
        token = slots.get("token")
        if not token:
            return False
        from app.extract import token_matches
        return token_matches(self.value, str(token))

    def _match_place(self, slots: dict) -> bool:
        candidates = slots.get("destination_candidates")
        if candidates:
            result = best_place_match(self.value, candidates)
            return result["verdict"] in ("exact", "fuzzy")
        destination = slots.get("destination")
        if not destination:
            return False
        result = best_place_match(self.value, [{"text": str(destination), "source": "known"}])
        return result["verdict"] in ("exact", "fuzzy")

    # -- plumbing ---------------------------------------------------------

    def __repr__(self) -> str:
        return f"Watch({self.kind!r}, {self.value!r})"

    def describe_te(self) -> str:
        """One short Telugu line for the UI: what we are listening for."""
        if self.kind == "train":
            return f"రైలు {self.value} కోసం వింటున్నాను"
        if self.kind == "token":
            return f"టోకెన్ {self.value} కోసం వింటున్నాను"
        return f"{self.value} బస్సు కోసం వింటున్నాను"


# --------------------------------------------------------------------------
# self test — python -m app.watch
# --------------------------------------------------------------------------

if __name__ == "__main__":
    print("watch.py self test")
    print("=" * 70)

    CASES = [
        # (watch, slots, expected, why)
        (Watch("train", "12739"),
         {"train_no": "12739", "platform": "4", "destination": "Hyderabad"},
         True, "exact train number"),

        (Watch("train", "12739"),
         {"train_no": "17201", "platform": "2", "destination": "Hyderabad"},
         False, "different train, same destination -> NOT ours"),

        (Watch("train", "12739"),
         {"train_no": None, "platform": None, "destination": None},
         False, "nothing heard at all"),

        (Watch("token", "47"),
         {"token": "47"}, True, "exact numeric token"),

        (Watch("token", "47"),
         {"token": "847"}, False, "longer token is not ours"),

        (Watch("token", "47"),
         {"token": "41"}, False, "one digit different -> NOT ours"),

        (Watch("token", "047"),
         {"token": "47"}, False, "leading zero is significant"),

        (Watch("bus", "Vijayawada"),
         {"destination": "vijaywada"}, True, "bus destination fuzzy"),

        (Watch("bus", "Vijayawada"),
         {"destination": "Visakhapatnam"}, False, "different city"),
    ]

    failed = 0
    for watch, slots, want, why in CASES:
        got = watch.matches(slots)
        ok = got == want
        failed += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {str(watch):<28} -> "
              f"{str(got):<5} ({why})")

    # The number rule must never be loosened.
    strict = Watch("train", "12739")
    for near in ("12738", "1273", "127390", "12749"):
        ok = not strict.matches({"train_no": near})
        failed += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  near-miss {near} rejected")

    print("=" * 70)
    print(f"{len(CASES) + 4 - failed} passed, {failed} failed")
