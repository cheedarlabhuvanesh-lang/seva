"""Gnani Timbre v2.5 voice catalogue + language codes.

Source: https://docs.gnani.ai/api/TTS/available-voices (42 voices)
"""

from __future__ import annotations

# BCP-47 codes accepted by both Prisma (STT language_code) and Timbre (TTS language).
LANGUAGES = {
    "auto": "Auto-detect from script (TTS only, good for mixed content)",
    "en-IN": "English (India)",
    "hi-IN": "Hindi",
    "hi-en": "Hinglish (code-mixed Hindi-English, TTS only)",
    "te-IN": "Telugu",
    "ta-IN": "Tamil",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
}

# Voices grouped by their preferred language. Using a voice with a mismatched
# `language` value degrades quality, so always pick from the matching list.
VOICES_BY_LANGUAGE = {
    "en-IN": ["Kaveri", "Trupti", "Devika", "Pranav", "Shlok", "Girish"],
    "hi-IN": [
        "Nalini", "Bhavna", "Yashvi", "Urmila", "Jwala", "Chitra", "Ambuja",
        "Deepak", "Roopesh", "Vikrant", "Hemraj", "Jalaj", "Omkar",
    ],
    "hi-en": ["Poorvi"],
    "ta-IN": ["Asmita", "Trisha", "Brinda", "Vedika", "Noopur"],
    "te-IN": ["Suhana", "Lehara", "Lavanya", "Yukti", "Varuni"],
    "kn-IN": ["Saanvi", "Kavin"],
    "ml-IN": ["Reshma", "Riyaan"],
    "mr-IN": ["Zahira", "Ishaan"],
    "bn-IN": ["Kirra", "Dhruva"],
    "gu-IN": ["Falak", "Veera"],
    "pa-IN": ["Mehuli", "Zayan"],
}

# Sensible default voice per language (warm / conversational personas).
DEFAULT_VOICE = {
    "en-IN": "Shlok",
    "hi-IN": "Vikrant",
    "hi-en": "Poorvi",
    "ta-IN": "Trisha",
    "te-IN": "Varuni",     # "Clear, natural voice for everyday use"
    "kn-IN": "Saanvi",
    "ml-IN": "Reshma",
    "mr-IN": "Ishaan",
    "bn-IN": "Kirra",
    "gu-IN": "Falak",
    "pa-IN": "Zayan",
    "auto": "Varuni",
}

# Persona notes for the Telugu voices, so you can pick by feel, not by guessing.
TELUGU_VOICE_NOTES = {
    "Suhana": "Warm / empathetic - calm, soothing, confident",
    "Lehara": "Warm / empathetic - gentle, reassuring, builds trust",
    "Lavanya": "Warm / empathetic - soft, for care and support flows",
    "Yukti": "Friendly - warm, welcoming, puts listeners at ease",
    "Varuni": "Conversational - clear and natural for everyday use",
}


# ---------------------------------------------------------------- Timbre v3.0
# Launched 14 Oct 2026. The big change: every voice speaks ALL 10 languages, so
# you pick a voice for its persona and set `language` separately. v2.5 tied each
# voice to one preferred language.
#
# Two gotchas:
#   - No Hinglish (hi-en) in v3.0. Use v2.5 + Poorvi for code-mixed text.
#   - Voices are NOT interchangeable between models. Sending Varuni with
#     timbre-v3.0, or Shivani with timbre-v2.5, returns 400 Bad Request.
V3_MODEL = "timbre-v3.0"
V25_MODEL = "timbre-v2.5"

V3_LANGUAGES = ["hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN",
                "ml-IN", "mr-IN", "pa-IN", "bn-IN", "gu-IN"]

# voice -> (gender, persona, description)
V3_VOICES = {
    "Shivani":  ("Female", "Warm, empathetic",        "Warm, patient, empathy-first voice for inbound customer support"),
    "Chetana":  ("Female", "Warm, empathetic",        "Gentle, empathetic voice for healthcare and telecom customer service"),
    "Kalpana":  ("Female", "Warm, empathetic",        "Empathetic, composed voice for customer care and insurance"),
    "Tarika":   ("Female", "Warm, empathetic",        "Warm, reassuring voice for banking and insurance customer care"),
    "Lakshmi":  ("Female", "Expressive, energetic",   "Expressive, warm voice for storytelling and e-learning narration"),
    "Vasudha":  ("Female", "Expressive, energetic",   "Expressive, composed voice for narration and e-learning"),
    "Kavitha":  ("Female", "Professional",            "Professional, firm voice for collections and payment recovery"),
    "Riyaa":    ("Female", "Confident, authoritative", "Confident, lively voice for outbound sales and insurance"),
    "Yashraj":  ("Male",   "Warm, empathetic",        "Warm narrative voice for e-learning and IVR narration"),
    "Avinash":  ("Male",   "Professional",            "Professional, firm voice with deliberate pacing for collections and BFSI"),
    "Prakash":  ("Male",   "Professional",            "Steady, deliberate voice for payment recovery and BFSI"),
    "Devansh":  ("Male",   "Conversational",          "Energetic, young conversational voice for sales and fintech"),
    "Shubham":  ("Male",   "Expressive, energetic",   "Energetic, confident voice for outbound sales and lead qualification"),
}

# For a hospital, empathy beats polish. Chetana is literally described as the
# healthcare voice, so that is the default everywhere.
V3_DEFAULT = "Chetana"

V3_BY_PERSONA: dict[str, list[str]] = {}
for _name, (_gender, _persona, _desc) in V3_VOICES.items():
    V3_BY_PERSONA.setdefault(_persona, []).append(_name)


def default_voice(language: str, model: str = V25_MODEL) -> str:
    """Pick a safe default voice for a language code and model."""
    if model == V3_MODEL:
        return V3_DEFAULT
    return DEFAULT_VOICE.get(language, "Varuni")


def validate(language: str, voice: str | None, model: str = V25_MODEL) -> str:
    """Return a voice valid for this model + language, raising on a mismatch.

    Catches the 400 before it costs a round trip: v2.5 voices rejected by v3.0
    and vice versa, plus v2.5's per-language voice lists.
    """
    if model == V3_MODEL:
        if language not in V3_LANGUAGES and language != "auto":
            extra = (" Timbre v3.0 has no Hinglish voice - use timbre-v2.5 "
                     "with Poorvi for hi-en.") if language == "hi-en" else ""
            raise ValueError(
                f"Timbre v3.0 does not support language {language!r}. "
                f"Supported: {', '.join(V3_LANGUAGES)}.{extra}"
            )
        if voice is None:
            return V3_DEFAULT
        if voice not in V3_VOICES:
            hint = ""
            if any(voice in names for names in VOICES_BY_LANGUAGE.values()):
                hint = (f" {voice!r} is a Timbre v2.5 voice - the two catalogues "
                        "are separate and mixing them returns 400.")
            raise ValueError(
                f"Voice {voice!r} is not available on timbre-v3.0.{hint} "
                f"Choose one of: {', '.join(sorted(V3_VOICES))}"
            )
        return voice

    if voice is None:
        return DEFAULT_VOICE.get(language, "Varuni")
    if voice in V3_VOICES:
        raise ValueError(
            f"Voice {voice!r} is a Timbre v3.0 voice and will be rejected by "
            f"{model!r} with 400. Either pass model='timbre-v3.0' or pick a "
            f"v2.5 voice: {', '.join(VOICES_BY_LANGUAGE.get(language, []))}"
        )
    allowed = VOICES_BY_LANGUAGE.get(language)
    if allowed and voice not in allowed:
        raise ValueError(
            f"Voice {voice!r} is not a preferred voice for {language!r}. "
            f"Choose one of: {', '.join(allowed)}"
        )
    return voice

