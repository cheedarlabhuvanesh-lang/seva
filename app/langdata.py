"""Vocabulary for the ten announcement languages Seva already supports.

This module is data only. `app.extract` uses the full tables (including
romanised Telugu/Hindi), while `app.matcher.normalize_text` uses only the
native-script tables so that ordinary English words such as "do" and "teen"
are never mistaken for numbers.

Coverage is deliberately conservative. Number words 0-19, tens and hundreds
are included for every language. Natural compounds that are regular or that we
can state without guessing are included; where a compound is irregular and not
listed, extraction misses the number rather than inventing one.
"""

from __future__ import annotations

LANGUAGES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "ta-IN": "Tamil",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
}

DIGIT_ZEROES = (
    0x0966,  # Devanagari (Hindi, Marathi)
    0x09E6,  # Bengali
    0x0A66,  # Gurmukhi (Punjabi)
    0x0AE6,  # Gujarati
    0x0BE6,  # Tamil
    0x0C66,  # Telugu
    0x0CE6,  # Kannada
    0x0D66,  # Malayalam
)

ZERO_WIDTH = (
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff", "\u00ad",
)


def to_ascii_digits(text: str) -> str:
    """Convert every supported script's digits to ASCII without touching text."""
    for zero in DIGIT_ZEROES:
        for offset in range(10):
            text = text.replace(chr(zero + offset), str(offset))
    return text


def strip_zero_width(text: str) -> str:
    for char in ZERO_WIDTH:
        text = text.replace(char, "")
    return text


def _merge(*tables: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for table in tables:
        for word, value in table.items():
            previous = merged.get(word)
            if previous is not None and previous != value:
                raise ValueError(f"conflicting value for {word!r}: {previous} vs {value}")
            merged[word] = value
    return merged


_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_EN_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_TE_UNITS = {
    "సున్నా": 0, "ఒకటి": 1, "ఒక": 1, "రెండు": 2, "మూడు": 3,
    "నాలుగు": 4, "ఐదు": 5, "అయిదు": 5, "ఆరు": 6, "ఏడు": 7,
    "ఎనిమిది": 8, "తొమ్మిది": 9,
    "జీరో": 0, "వన్": 1, "ఒన్": 1, "టూ": 2, "త్రీ": 3,
    "ఫోర్": 4, "ఫైవ్": 5, "సిక్స్": 6, "సెవెన్": 7, "ఎయిట్": 8, "నైన్": 9,
}
_TE_TEENS = {
    "పది": 10, "పదకొండు": 11, "పన్నెండు": 12, "పదమూడు": 13,
    "పద్నాలుగు": 14, "పదిహేను": 15, "పదహారు": 16, "పదిహేడు": 17,
    "పద్దెనిమిది": 18, "పంతొమ్మిది": 19,
    "టెన్": 10, "ఎలెవెన్": 11, "ట్వెల్వ్": 12, "థర్టీన్": 13,
    "ఫోర్టీన్": 14, "ఫిఫ్టీన్": 15, "సిక్స్టీన్": 16, "సిక్స్‌టీన్": 16,
    "సెవెంటీన్": 17, "ఎయిటీన్": 18, "నైంటీన్": 19,
}
_TE_TENS = {
    "ఇరవై": 20, "ముప్పై": 30, "నలభై": 40, "యాభై": 50,
    "అరవై": 60, "డెబ్బై": 70, "ఎనభై": 80, "తొంభై": 90,
    "ట్వంటీ": 20, "థర్టీ": 30, "ఫార్టీ": 40, "ఫోర్టీ": 40,
    "ఫిఫ్టీ": 50, "సిక్స్టీ": 60, "సెవెంటీ": 70, "ఎయిటీ": 80, "నైంటీ": 90,
}

_HI_UNITS = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4,
    "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9,
    "ज़ीरो": 0, "जीरो": 0, "वन": 1, "टू": 2, "थ्री": 3,
    "फोर": 4, "फाइव": 5, "सिक्स": 6, "सेवन": 7, "एट": 8, "ऐट": 8, "नाइन": 9,
}
_HI_TEENS = {
    "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13,
    "चौदह": 14, "पंद्रह": 15, "सोलह": 16, "सत्रह": 17,
    "अठारह": 18, "उन्नीस": 19, "टेन": 10,
}
_HI_TENS = {
    "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50,
    "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90,
    "ट्वेंटी": 20, "थर्टी": 30, "फोर्टी": 40, "फार्टी": 40,
    "फिफ्टी": 50, "सिक्सटी": 60, "सेवंटी": 70, "एटी": 80, "नाइंटी": 90,
}

_TA_UNITS = {
    "பூஜ்யம்": 0, "சுழியம்": 0, "ஒன்று": 1, "இரண்டு": 2, "மூன்று": 3,
    "நான்கு": 4, "ஐந்து": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8,
    "ஒன்பது": 9,
}
_TA_TEENS = {
    "பத்து": 10, "பதினொன்று": 11, "பன்னிரண்டு": 12, "பதின்மூன்று": 13,
    "பதினான்கு": 14, "பதினைந்து": 15, "பதினாறு": 16, "பதினேழு": 17,
    "பதினெட்டு": 18, "பத்தொன்பது": 19,
}
_TA_TENS = {
    "இருபது": 20, "முப்பது": 30, "நாற்பது": 40, "ஐம்பது": 50,
    "அறுபது": 60, "எழுபது": 70, "எண்பது": 80, "தொண்ணூறு": 90,
    "இருபத்து": 20, "முப்பத்து": 30, "நாற்பத்து": 40, "ஐம்பத்து": 50,
    "அறுபத்து": 60, "எழுபத்து": 70, "எண்பத்து": 80, "தொண்ணூற்று": 90,
}

_KN_UNITS = {
    "ಶೂನ್ಯ": 0, "ಸೊನ್ನೆ": 0, "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3,
    "ನಾಲ್ಕು": 4, "ಐದು": 5, "ಆರು": 6, "ಏಳು": 7, "ಎಂಟು": 8,
    "ಒಂಬತ್ತು": 9,
}
_KN_TEENS = {
    "ಹತ್ತು": 10, "ಹನ್ನೊಂದು": 11, "ಹನ್ನೆರಡು": 12, "ಹದಿಮೂರು": 13,
    "ಹದಿನಾಲ್ಕು": 14, "ಹದಿನೈದು": 15, "ಹದಿನಾರು": 16, "ಹದಿನೇಳು": 17,
    "ಹದಿನೆಂಟು": 18, "ಹತ್ತೊಂಬತ್ತು": 19,
}
_KN_TENS = {
    "ಇಪ್ಪತ್ತು": 20, "ಮೂವತ್ತು": 30, "ನಲವತ್ತು": 40, "ಐವತ್ತು": 50,
    "ಅರವತ್ತು": 60, "ಎಪ್ಪತ್ತು": 70, "ಎಂಬತ್ತು": 80, "ತೊಂಬತ್ತು": 90,
    "ಇಪ್ಪತ್ತ": 20, "ಮೂವತ್ತ": 30, "ನಲವತ್ತ": 40, "ಐವತ್ತ": 50,
    "ಅರವತ್ತ": 60, "ಎಪ್ಪತ್ತ": 70, "ಎಂಬತ್ತ": 80, "ತೊಂಬತ್ತ": 90,
}

_ML_UNITS = {
    "പൂജ്യം": 0, "ഒന്ന്": 1, "രണ്ട്": 2, "മൂന്ന്": 3, "നാല്": 4,
    "അഞ്ച്": 5, "ആറ്": 6, "ഏഴ്": 7, "എട്ട്": 8, "ഒമ്പത്": 9,
}
_ML_TEENS = {
    "പത്ത്": 10, "പതിനൊന്ന്": 11, "പന്ത്രണ്ട്": 12, "പതിമൂന്ന്": 13,
    "പതിനാല്": 14, "പതിനഞ്ച്": 15, "പതിനാറ്": 16, "പതിനേഴ്": 17,
    "പതിനെട്ട്": 18, "പത്തൊമ്പത്": 19,
}
_ML_TENS = {
    "ഇരുപത്": 20, "മുപ്പത്": 30, "നാല്പത്": 40, "അമ്പത്": 50,
    "അറുപത്": 60, "എഴുപത്": 70, "എൺപത്": 80, "തൊണ്ണൂറ്": 90,
    "ഇരുപത്തി": 20, "മുപ്പത്തി": 30, "നാല്പത്തി": 40, "അമ്പത്തി": 50,
    "അറുപത്തി": 60, "എഴുപത്തി": 70, "എൺപത്തി": 80, "തൊണ്ണൂറ്റി": 90,
}

_MR_UNITS = {
    "शून्य": 0, "एक": 1, "दोन": 2, "तीन": 3, "चार": 4,
    "पाच": 5, "सहा": 6, "सात": 7, "आठ": 8, "नऊ": 9,
}
_MR_TEENS = {
    "दहा": 10, "अकरा": 11, "बारा": 12, "तेरा": 13, "चौदा": 14,
    "पंधरा": 15, "सोळा": 16, "सतरा": 17, "अठरा": 18, "एकोणीस": 19,
}
_MR_TENS = {
    "वीस": 20, "तीस": 30, "चाळीस": 40, "पन्नास": 50,
    "साठ": 60, "सत्तर": 70, "ऐंशी": 80, "नव्वद": 90,
}

_BN_UNITS = {
    "শূন্য": 0, "এক": 1, "দুই": 2, "তিন": 3, "চার": 4,
    "পাঁচ": 5, "ছয়": 6, "সাত": 7, "আট": 8, "নয়": 9,
}
_BN_TEENS = {
    "দশ": 10, "এগারো": 11, "বারো": 12, "তেরো": 13, "চৌদ্দ": 14,
    "পনেরো": 15, "ষোলো": 16, "সতেরো": 17, "আঠারো": 18, "উনিশ": 19,
}
_BN_TENS = {
    "বিশ": 20, "ত্রিশ": 30, "চল্লিশ": 40, "পঞ্চাশ": 50,
    "ষাট": 60, "সত্তর": 70, "আশি": 80, "নব্বই": 90,
}

_GU_UNITS = {
    "શૂન્ય": 0, "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4,
    "પાંચ": 5, "છ": 6, "સાત": 7, "આઠ": 8, "નવ": 9,
}
_GU_TEENS = {
    "દસ": 10, "અગિયાર": 11, "બાર": 12, "તેર": 13, "ચૌદ": 14,
    "પંદર": 15, "સોળ": 16, "સત્તર": 17, "અઢાર": 18, "ઓગણીસ": 19,
}
_GU_TENS = {
    "વીસ": 20, "ત્રીસ": 30, "ચાલીસ": 40, "પચાસ": 50,
    "સાઠ": 60, "સિત્તેર": 70, "એંસી": 80, "નેવું": 90,
}

_PA_UNITS = {
    "ਸਿਫ਼ਰ": 0, "ਸੁੰਨਾ": 0, "ਇੱਕ": 1, "ਦੋ": 2, "ਤਿੰਨ": 3, "ਚਾਰ": 4,
    "ਪੰਜ": 5, "ਛੇ": 6, "ਸੱਤ": 7, "ਅੱਠ": 8, "ਨੌਂ": 9,
}
_PA_TEENS = {
    "ਦਸ": 10, "ਗਿਆਰਾਂ": 11, "ਬਾਰਾਂ": 12, "ਤੇਰਾਂ": 13, "ਚੌਦਾਂ": 14,
    "ਪੰਦਰਾਂ": 15, "ਸੋਲਾਂ": 16, "ਸਤਾਰਾਂ": 17, "ਅਠਾਰਾਂ": 18, "ਉੱਨੀ": 19,
}
_PA_TENS = {
    "ਵੀਹ": 20, "ਤੀਹ": 30, "ਚਾਲੀ": 40, "ਪੰਜਾਹ": 50,
    "ਸੱਠ": 60, "ਸੱਤਰ": 70, "ਅੱਸੀ": 80, "ਨੱਬੇ": 90,
}

NATIVE_UNITS = _merge(
    _EN_UNITS, _TE_UNITS, _HI_UNITS, _TA_UNITS, _KN_UNITS,
    _ML_UNITS, _MR_UNITS, _BN_UNITS, _GU_UNITS, _PA_UNITS,
)
NATIVE_TEENS = _merge(
    _EN_TEENS, _TE_TEENS, _HI_TEENS, _TA_TEENS, _KN_TEENS,
    _ML_TEENS, _MR_TEENS, _BN_TEENS, _GU_TEENS, _PA_TEENS,
)
NATIVE_TENS = _merge(
    _EN_TENS, _TE_TENS, _HI_TENS, _TA_TENS, _KN_TENS,
    _ML_TENS, _MR_TENS, _BN_TENS, _GU_TENS, _PA_TENS,
)

NATIVE_HUNDREDS = frozenset({
    "hundred", "hundreds",
    "వంద", "వందలు", "వందల", "నూరు", "హండ్రెడ్",
    "सौ", "हंड्रेड",
    "நூறு",
    "ನೂರು",
    "നൂറ്", "നൂറു",
    "शंभर",
    "শত", "একশ", "একশো",
    "સો",
    "ਸੌ",
})

NATIVE_AND = frozenset({
    "and", "మరియు", "औ", "और", "மற்றும்", "ಮತ್ತು",
    "आणि", "এবং", "અને", "ਅਤੇ",
})

_HI_COMPOUND = {
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25,
    "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "अठ्ठाईस": 28, "उनतीस": 29,
    "इकतीस": 31, "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35,
    "छत्तीस": 36, "सैंतीस": 37, "अड़तीस": 38, "उनतालीस": 39,
    "इकतालीस": 41, "एकतालीस": 41, "बयालीस": 42, "तैंतालीस": 43,
    "चवालीस": 44, "पैंतालीस": 45, "छियालीस": 46, "सैंतालीस": 47,
    "अड़तालीस": 48, "उनचास": 49,
    "इक्यावन": 51, "बावन": 52, "तिरपन": 53, "चौवन": 54, "पचपन": 55,
    "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58, "उनसठ": 59,
    "इकसठ": 61, "बासठ": 62, "तिरसठ": 63, "चौंसठ": 64, "पैंसठ": 65,
    "छियासठ": 66, "सड़सठ": 67, "अड़सठ": 68, "उनहत्तर": 69,
    "इकहत्तर": 71, "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74,
    "पचहत्तर": 75, "छिहत्तर": 76, "सतहत्तर": 77, "अठहत्तर": 78,
    "उनासी": 79,
    "इक्यासी": 81, "बयासी": 82, "तिरासी": 83, "चौरासी": 84,
    "पचासी": 85, "छियासी": 86, "सत्तासी": 87, "अट्ठासी": 88, "नवासी": 89,
    "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94,
    "पंचानवे": 95, "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98,
    "निन्यानवे": 99,
}

COMPOUND_NUMBERS = dict(_HI_COMPOUND)

_ROMAN_UNITS = {
    "sunna": 0, "okati": 1, "oka": 1, "rendu": 2, "moodu": 3, "mudu": 3,
    "nalugu": 4, "aidu": 5, "ayidu": 5, "aaru": 6, "aru": 6, "edu": 7,
    "enimidi": 8, "tommidi": 9,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5,
    "paanch": 5, "chhe": 6, "chhah": 6, "saat": 7, "aath": 8, "nau": 9,
}
_ROMAN_TEENS = {
    "padi": 10, "padakondu": 11, "pannendu": 12, "padamudu": 13,
    "padnalugu": 14, "padihenu": 15, "padaharu": 16, "padihedu": 17,
    "paddhenimidi": 18, "panthommidi": 19,
    "das": 10, "gyarah": 11, "baarah": 12, "terah": 13, "chaudah": 14,
    "pandrah": 15, "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19,
}
_ROMAN_TENS = {
    "iravai": 20, "muppai": 30, "nalabhai": 40, "nalabai": 40, "yabai": 50,
    "aravai": 60, "debbai": 70, "enabhai": 80, "tombhai": 90,
    "bees": 20, "tees": 30, "chalis": 40, "chaalis": 40, "pachaas": 50,
    "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
}
ROMAN_UNITS = _ROMAN_UNITS
ROMAN_TEENS = _ROMAN_TEENS
ROMAN_TENS = _ROMAN_TENS
ROMAN_HUNDREDS = frozenset({
    "vandala", "vandalu", "vanda", "nuru", "sau", "hundred",
})

NUMBER_TABLES = (
    NATIVE_UNITS, NATIVE_TEENS, NATIVE_TENS, COMPOUND_NUMBERS,
    ROMAN_UNITS, ROMAN_TEENS, ROMAN_TENS,
)

PLATFORM_WORDS = (
    "platform", "plaatform", "plat form", "pf",
    "ప్లాట్ఫారం", "ప్లాట్ఫామ్", "ప్లాట్ఫాం", "ప్లాట్ ఫారం", "ప్లాట్ఫారమ్", "వేదిక",
    "లాట్ ఫారమ్", "లాట్ ఫారం", "లాట్ఫారమ్",
    "प्लेटफॉर्म", "प्लेटफार्म", "प्लेटफ़ॉर्म",
    "பிளாட்பாரம்", "பிளாட்ஃபாரம்", "நடைமேடை",
    "ಪ್ಲಾಟ್ಫಾರ್ಮ್", "ಪ್ಲಾಟ್ಫಾರಂ", "ವೇದಿಕೆ",
    "പ്ലാറ്റ്ഫോം", "പ്ലാറ്റ്ഫോര്ം", "പ്ലാറ്റ്ഫോർം",
    "प्लॅटफॉर्म", "फलाट",
    "প্ল্যাটফর্ম", "প্লাটফর্ম", "প্ল্যাটফোরাম",
    "પ્લેટફોર્મ",
    "ਪਲੇਟਫਾਰਮ",
)

TRAIN_WORDS = (
    "train", "railu", "రైలు", "ట్రైన్",
    "ट्रेन", "रेलगाड़ी", "गाड़ी",
    "ரயில்", "தொடர்வண்டி",
    "ರೈಲು", "ರೈಲ್",
    "ട്രെയിൻ", "തീവണ്ടി",
    "रेल्वे", "गाडी",
    "ট্রেন", "রেলগাড়ি",
    "ટ્રેન", "રેલગાડી",
    "ਟ੍ਰੇਨ", "ਰੇਲਗੱਡੀ",
)

TOKEN_WORDS = (
    "token", "tokan",
    "టోకెన్", "తోకెన్", "టోకన్", "ట్రోకెన్",
    "टोकन", "टोकेन",
    "டோக்கன்", "டோகன்", "டோக்கென்",
    "ಟೋಕನ್", "ಟೋಕೆನ್",
    "ടോക്കൺ", "ടോക്കെൻ",
    "টোকেন",
    "ટોકન",
    "ਟੋਕਨ",
)

ROOM_WORDS = (
    "room",
    "రూమ్", "గది",
    "कमरा", "कक्ष", "रूम",
    "அறை", "ரூம்",
    "ಕೊಠಡಿ", "ರೂಂ", "ಕೋಣೆ",
    "മുറി", "റൂം",
    "खोली",
    "ঘর", "রুম",
    "રૂમ", "ઓરડો",
    "ਕਮਰਾ", "ਰੂਮ",
)

NUMBER_CONNECTORS = frozenset({
    "number", "num", "no", "nambar", "nambaru", "sankhya",
    "is", "are", "was", "were", "be", "being", "called", "announced",
    "numbered", "equals", "equal", "for",
    "నంబర్", "నంబరు", "సంఖ్య",
    "नंबर", "नम्बर", "संख्या", "क्रमांक",
    "எண்", "நம்பர்",
    "ಸಂಖ್ಯೆ", "ನಂಬರ್",
    "നമ്പർ", "നമ്പര്",
    "নম্বর", "সংখ্যা",
    "નંબર", "સંખ્યા",
    "ਨੰਬਰ", "ਸੰਖਿਆ",
})

PLATFORM_CONNECTORS = frozenset({
    *NUMBER_CONNECTORS,
    "on", "at", "ke", "par", "pe",
})

DEPARTMENT_MARKERS = frozenset({
    "department", "dept", "specialist", "consultant", "unit", "ward", "opd",
    "విభాగం", "విభాగ", "డిపార్ట్మెంట్", "నిపుణుడు", "నిపుణులు",
    "विभाग", "डिपार्टमेंट", "विशेषज्ञ",
    "துறை", "பிரிவு", "நிபுணர்",
    "ವಿಭಾಗ", "ಇಲಾಖೆ", "ತಜ್ಞ",
    "വിഭാഗം", "വിഭാഗത്തിൽ", "സ്പെഷ്യലിസ്റ്റ്",
    "तज्ज्ञ",
    "বিভাগ", "বিশেষজ্ঞ",
    "વિભાગ", "નિષ્ણાત",
    "ਵਿਭਾਗ", "ਮਾਹਰ",
})

DEPARTMENT_FILLERS = frozenset({
    "of", "the", "in", "at", "for", "our", "your", "is", "are",
    "లో", "కు", "కి", "మా",
    "के", "का", "की", "में", "हमारा",
    "இன்", "இல்", "நமது",
    "ನ", "ನಲ್ಲಿ", "ನಮ್ಮ",
    "ന്റെ", "ഇൽ", "ഞങ്ങളുടെ",
    "चा", "ची", "चे", "मध्ये",
    "এর", "এ",
    "નો", "ની", "નું", "માં",
    "ਦਾ", "ਦੀ", "ਦੇ", "ਵਿੱਚ",
})

SPECIALIST_BLOCKERS = frozenset({
    "token", "number", "room", "platform", "train", "bus", "please", "go",
    "to", "at", "on", "now", "will", "is", "are", "the", "a", "an",
    "your", "my", "our", "see", "meet", "visit", "consult", "patient",
    "patients", "called", "announcement",
    *TOKEN_WORDS, *ROOM_WORDS, *PLATFORM_WORDS, *TRAIN_WORDS,
    *DEPARTMENT_MARKERS,
})

SPECIALIST_GENERIC = frozenset({
    "general", "private", "male", "female", "emergency", "icu", "opd",
    "casualty", "duty", "ward", "room", "department", "dept", "unit",
})

HOSPITAL_CUES = frozenset({
    "hospital", "clinic", "opd", "patient", "doctor", "appointment",
    "ఆసుపత్రి", "अस्पताल", "மருத்துவமனை", "ಆಸ್ಪತ್ರೆ",
    "ആശുപത്രി", "रुग्णालय", "হাসপাতাল", "હોસ્પિટલ", "ਹਸਪਤਾਲ",
    *TOKEN_WORDS, *ROOM_WORDS, *DEPARTMENT_MARKERS,
})

TRAIN_NAME_STOPWORDS = frozenset({
    "to", "via", "from", "at", "on", "in", "for", "the", "a", "an",
    "is", "are", "was", "were", "will", "now", "please", "attention",
    "arriving", "arrive", "arrives", "arrived", "departing", "depart",
    "departs", "departure", "delayed", "late", "running", "cancelled",
    "canceled", "passengers", "route", "station", "stop", "stops",
    "towards", "bound", "service", "today",
    "కు", "కి", "నుండి", "వెళ్ళే", "వస్తోంది", "వస్తుంది", "ఆలస్యం",
    "ఆగుతుంది", "ఇప్పుడు", "దయచేసి",
    "को", "से", "जाने", "आ", "रही", "देरी", "अब", "कृपया",
    "க்கு", "இருந்து", "வழியாக", "செல்லும்", "வரும்", "தாமதம்",
    "நிற்கும்", "இப்போது", "தயவுசெய்து",
    "ಗೆ", "ಇಂದ", "ಮೂಲಕ", "ಹೋಗುವ", "ಬರುವ", "ತಡ", "ನಿಲ್ಲುವ", "ಈಗ",
    "ದಯವಿಟ್ಟು",
    "ലേക്ക്", "നിന്ന്", "വഴി", "പോകുന്ന", "വരുന്ന", "കാലതാമസം",
    "നിൽക്കുന്ന", "ഇപ്പോൾ", "ദയവായി",
    "ला", "पासून", "मार्गे", "जाणारी", "येणारी", "उशीर", "थांबणारी",
    "आता", "कृपया",
    "তে", "থেকে", "হয়ে", "যাচ্ছে", "আসছে", "দেরি", "দাঁড়াবে", "এখন",
    "অনুগ্রহ করে",
    "તરફ", "થી", "મારફતે", "જતી", "આવતી", "મોડી", "ઊભી", "હવે",
    "કૃપા",
    "ਨੂੰ", "ਤੋਂ", "ਰਾਹੀਂ", "ਜਾਣ", "ਵਾਲੀ", "ਆ", "ਰਹੀ", "ਦੇਰ",
    "ਖੜ੍ਹੀ", "ਹੁਣ", "ਕਿਰਪਾ",
    *PLATFORM_WORDS, *TRAIN_WORDS, *NUMBER_CONNECTORS,
})

TRAIN_NAME_LEAD = frozenset({
    "the", "is", "named", "known", "as", "called",
    *TRAIN_WORDS, "number", "no",
    "नंबर", "नम्बर", "क्रमांक", "संख्या",
    "నంబర్", "నంబరు", "సంఖ్య",
    "ನಂಬರ್", "ಸಂಖ್ಯೆ",
    "നമ്പർ", "നമ്പര്",
    "எண்", "நம்பர்",
    "সংখ্যা", "নম্বর",
    "નંબર", "સંખ્યા",
    "ਨੰਬਰ", "ਸੰਖਿਆ",
})

TRAIN_NAME_SUFFIXES = frozenset({
    "express", "superfast", "rajdhani", "shatabdi", "duronto", "janata",
    "intercity", "memu", "demu", "passenger", "garib", "rath", "humsafar",
    "tejas", "vande", "bharat",
})
