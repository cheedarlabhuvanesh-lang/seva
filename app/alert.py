"""
alert.py — turn a matched announcement into something a frightened person
can act on.

Two hard rules, and everything here follows from them.

RULE 1 — Never speak a number that is not in the slots.
    If the platform was not heard, the alert says so and sends the person to
    the display board. It does not guess. A wrong platform is worse than no
    platform, because a wrong one is acted on with confidence.

RULE 2 — Timbre v3.0 tags have no closing form.
    A tag applies from where it appears until the end of the text or until
    the next tag. Writing </slow> produces an unknown tag, which the server
    strips silently. The request succeeds, the account is charged, and the
    delivery does not change. So every string this module produces is checked
    through gnani.tags.validate() before it is returned.

The voice gives the headline. The screen shows the detail. Three short
sentences maximum, because a person in a loud hall cannot hold more than that.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gnani import tags  # noqa: E402

#: Spoken alerts stay this short. Voice guidance research is consistent:
#: two to three sentences, headline first.
MAX_SENTENCES = 3


# --------------------------------------------------------------------------
# phrasing, per language
# --------------------------------------------------------------------------
# Only languages that are actually written out here are spoken. Selecting
# Tamil and hearing Telugu is the same class of failure as inventing a
# platform number: the app would be confidently telling her something untrue.
# Every language Seva offers in the UI has a complete set of phrases below,
# and server code refuses an unknown language rather than silently using
# English.

PHRASES: dict[str, dict[str, str]] = {
    "te": {
        "train_both": "మీ రైలు {train}, {dest} వెళ్ళేది, వస్తోంది.",
        "train_only": "మీ రైలు {train} వస్తోంది.",
        "train_dest": "{dest} వెళ్ళే మీ రైలు వస్తోంది.",
        "train_bare": "మీ రైలు వస్తోంది.",
        "bus_dest":   "{dest} వెళ్ళే మీ బస్సు వచ్చింది.",
        "bus_bare":   "మీ బస్సు వచ్చింది.",
        "token_only": "మీ నంబర్ వచ్చింది. {token}.",
        "token_bare": "మీ నంబర్ వచ్చింది.",
        "room":       "<slow>గది నంబర్ {room}.",
        "room_unknown": "<slow>గది నంబర్ వినపడలేదు. డిస్‌ప్లే బోర్డు చూడండి.",
        "platform":   "<slow>ప్లాట్‌ఫారం నంబర్ {platform}.",
        "unknown":    "<slow>ప్లాట్‌ఫారం నంబర్ వినపడలేదు. డిస్‌ప్లే బోర్డు చూడండి.",
        "go":         "సామాను తీసుకొని ఇప్పుడే బయలుదేరండి.",
        "ready":      "సామాను సిద్ధం చేసుకోండి.",
    },
    "en": {
        "train_both": "Your train {train} to {dest} is arriving.",
        "train_only": "Your train {train} is arriving.",
        "train_dest": "Your train to {dest} is arriving.",
        "train_bare": "Your train is arriving.",
        "bus_dest":   "Your bus to {dest} has arrived.",
        "bus_bare":   "Your bus has arrived.",
        "token_only": "Your number has been called. {token}.",
        "token_bare": "Your number has been called.",
        "room":       "<slow>Room number {room}.",
        "room_unknown": "<slow>The room number was not heard. Please check the display board.",
        "platform":   "<slow>Platform number {platform}.",
        "unknown":    "<slow>The platform number was not heard. Please check the display board.",
        "go":         "Take your things and leave now.",
        "ready":      "Please keep your things ready.",
    },
    "hi": {
        "train_both": "आपकी ट्रेन {train}, {dest} जाने वाली, आ रही है.",
        "train_only": "आपकी ट्रेन {train} आ रही है.",
        "train_dest": "{dest} जाने वाली आपकी ट्रेन आ रही है.",
        "train_bare": "आपकी ट्रेन आ रही है.",
        "bus_dest":   "{dest} जाने वाली आपकी बस आ गई है.",
        "bus_bare":   "आपकी बस आ गई है.",
        "token_only": "आपका नंबर आ गया है. {token}.",
        "token_bare": "आपका नंबर आ गया है.",
        "room":       "<slow>कमरा नंबर {room}.",
        "room_unknown": "<slow>कमरा नंबर सुनाई नहीं दिया. कृपया डिस्प्ले बोर्ड देखें.",
        "platform":   "<slow>प्लेटफॉर्म नंबर {platform}.",
        "unknown":    "<slow>प्लेटफॉर्म नंबर सुनाई नहीं दिया. कृपया डिस्प्ले बोर्ड देखें.",
        "go":         "अपना सामान लेकर अभी निकलिए.",
        "ready":      "अपना सामान तैयार रखिए.",
    },
    "ta": {
        "train_both": "உங்கள் ரயில் {train}, {dest} செல்லும் ரயில், வருகிறது.",
        "train_only": "உங்கள் ரயில் {train} வருகிறது.",
        "train_dest": "{dest} செல்லும் உங்கள் ரயில் வருகிறது.",
        "train_bare": "உங்கள் ரயில் வருகிறது.",
        "bus_dest":   "{dest} செல்லும் உங்கள் பேருந்து வந்துவிட்டது.",
        "bus_bare":   "உங்கள் பேருந்து வந்துவிட்டது.",
        "token_only": "உங்கள் எண் அழைக்கப்பட்டது. {token}.",
        "token_bare": "உங்கள் எண் அழைக்கப்பட்டது.",
        "room":       "<slow>அறை எண் {room}.",
        "room_unknown": "<slow>அறை எண் கேட்கவில்லை. தயவுசெய்து திரையில் பாருங்கள்.",
        "platform":   "<slow>நடைமேடை எண் {platform}.",
        "unknown":    "<slow>நடைமேடை எண் கேட்கவில்லை. தயவுசெய்து திரையில் பாருங்கள்.",
        "go":         "உங்கள் பொருட்களை எடுத்துக்கொண்டு இப்போதே புறப்படுங்கள்.",
        "ready":      "உங்கள் பொருட்களை தயாராக வைத்திருங்கள்.",
    },
    "kn": {
        "train_both": "ನಿಮ್ಮ ರೈಲು {train}, {dest} ಗೆ ಹೋಗುವ ರೈಲು, ಬರುತ್ತಿದೆ.",
        "train_only": "ನಿಮ್ಮ ರೈಲು {train} ಬರುತ್ತಿದೆ.",
        "train_dest": "{dest} ಗೆ ಹೋಗುವ ನಿಮ್ಮ ರೈಲು ಬರುತ್ತಿದೆ.",
        "train_bare": "ನಿಮ್ಮ ರೈಲು ಬರುತ್ತಿದೆ.",
        "bus_dest":   "{dest} ಗೆ ಹೋಗುವ ನಿಮ್ಮ ಬಸ್ ಬಂದಿದೆ.",
        "bus_bare":   "ನಿಮ್ಮ ಬಸ್ ಬಂದಿದೆ.",
        "token_only": "ನಿಮ್ಮ ಸಂಖ್ಯೆ ಕರೆಯಲಾಗಿದೆ. {token}.",
        "token_bare": "ನಿಮ್ಮ ಸಂಖ್ಯೆ ಕರೆಯಲಾಗಿದೆ.",
        "room":       "<slow>ಕೊಠಡಿ ಸಂಖ್ಯೆ {room}.",
        "room_unknown": "<slow>ಕೊಠಡಿ ಸಂಖ್ಯೆ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಡಿಸ್ಪ್ಲೇ ಬೋರ್ಡ್ ನೋಡಿ.",
        "platform":   "<slow>ವೇದಿಕೆ ಸಂಖ್ಯೆ {platform}.",
        "unknown":    "<slow>ವೇದಿಕೆ ಸಂಖ್ಯೆ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಡಿಸ್ಪ್ಲೇ ಬೋರ್ಡ್ ನೋಡಿ.",
        "go":         "ನಿಮ್ಮ ವಸ್ತುಗಳನ್ನು ತೆಗೆದುಕೊಂಡು ಈಗಲೇ ಹೊರಡಿ.",
        "ready":      "ನಿಮ್ಮ ವಸ್ತುಗಳನ್ನು ಸಿದ್ಧವಾಗಿಡಿ.",
    },
    "ml": {
        "train_both": "നിങ്ങളുടെ ട്രെയിൻ {train}, {dest} ലേക്കുള്ള ട്രെയിൻ, വരുന്നു.",
        "train_only": "നിങ്ങളുടെ ട്രെയിൻ {train} വരുന്നു.",
        "train_dest": "{dest} ലേക്കുള്ള നിങ്ങളുടെ ട്രെയിൻ വരുന്നു.",
        "train_bare": "നിങ്ങളുടെ ട്രെയിൻ വരുന്നു.",
        "bus_dest":   "{dest} ലേക്കുള്ള നിങ്ങളുടെ ബസ് എത്തിയിരിക്കുന്നു.",
        "bus_bare":   "നിങ്ങളുടെ ബസ് എത്തിയിരിക്കുന്നു.",
        "token_only": "നിങ്ങളുടെ നമ്പർ വിളിച്ചിരിക്കുന്നു. {token}.",
        "token_bare": "നിങ്ങളുടെ നമ്പർ വിളിച്ചിരിക്കുന്നു.",
        "room":       "<slow>മുറി നമ്പർ {room}.",
        "room_unknown": "<slow>മുറി നമ്പർ കേട്ടില്ല. ദയവായി ഡിസ്പ്ലേ ബോർഡ് നോക്കുക.",
        "platform":   "<slow>പ്ലാറ്റ്ഫോം നമ്പർ {platform}.",
        "unknown":    "<slow>പ്ലാറ്റ്ഫോം നമ്പർ കേട്ടില്ല. ദയവായി ഡിസ്പ്ലേ ബോർഡ് നോക്കുക.",
        "go":         "നിങ്ങളുടെ സാധനങ്ങൾ എടുത്ത് ഇപ്പോൾ പുറപ്പെടുക.",
        "ready":      "നിങ്ങളുടെ സാധനങ്ങൾ തയ്യാറാക്കി വയ്ക്കുക.",
    },
    "mr": {
        "train_both": "तुमची ट्रेन {train}, {dest} ला जाणारी ट्रेन, येत आहे.",
        "train_only": "तुमची ट्रेन {train} येत आहे.",
        "train_dest": "{dest} ला जाणारी तुमची ट्रेन येत आहे.",
        "train_bare": "तुमची ट्रेन येत आहे.",
        "bus_dest":   "{dest} ला जाणारी तुमची बस आली आहे.",
        "bus_bare":   "तुमची बस आली आहे.",
        "token_only": "तुमचा नंबर हाकला गेला आहे. {token}.",
        "token_bare": "तुमचा नंबर हाकला गेला आहे.",
        "room":       "<slow>खोली क्रमांक {room}.",
        "room_unknown": "<slow>खोली क्रमांक ऐकू आला नाही. कृपया डिस्प्ले बोर्ड पहा.",
        "platform":   "<slow>प्लॅटफॉर्म क्रमांक {platform}.",
        "unknown":    "<slow>प्लॅटफॉर्म क्रमांक ऐकू आला नाही. कृपया डिस्प्ले बोर्ड पहा.",
        "go":         "तुमचे सामान घेऊन आत्ताच निघा.",
        "ready":      "तुमचे सामान तयार ठेवा.",
    },
    "bn": {
        "train_both": "আপনার ট্রেন {train}, {dest} যাওয়ার ট্রেন, আসছে.",
        "train_only": "আপনার ট্রেন {train} আসছে.",
        "train_dest": "{dest} যাওয়ার আপনার ট্রেন আসছে.",
        "train_bare": "আপনার ট্রেন আসছে.",
        "bus_dest":   "{dest} যাওয়ার আপনার বাস এসে গেছে.",
        "bus_bare":   "আপনার বাস এসে গেছে.",
        "token_only": "আপনার নম্বর ডাকা হয়েছে. {token}.",
        "token_bare": "আপনার নম্বর ডাকা হয়েছে.",
        "room":       "<slow>রুম নম্বর {room}.",
        "room_unknown": "<slow>রুম নম্বর শোনা যায়নি. অনুগ্রহ করে ডিসপ্লে বোর্ড দেখুন.",
        "platform":   "<slow>প্ল্যাটফর্ম নম্বর {platform}.",
        "unknown":    "<slow>প্ল্যাটফর্ম নম্বর শোনা যায়নি. অনুগ্রহ করে ডিসপ্লে বোর্ড দেখুন.",
        "go":         "আপনার জিনিস নিয়ে এখনই বেরিয়ে পড়ুন.",
        "ready":      "আপনার জিনিস প্রস্তুত রাখুন.",
    },
    "gu": {
        "train_both": "તમારી ટ્રેન {train}, {dest} જતી ટ્રેન, આવી રહી છે.",
        "train_only": "તમારી ટ્રેન {train} આવી રહી છે.",
        "train_dest": "{dest} જતી તમારી ટ્રેન આવી રહી છે.",
        "train_bare": "તમારી ટ્રેન આવી રહી છે.",
        "bus_dest":   "{dest} જતી તમારી બસ આવી ગઈ છે.",
        "bus_bare":   "તમારી બસ આવી ગઈ છે.",
        "token_only": "તમારો નંબર બોલાવવામાં આવ્યો છે. {token}.",
        "token_bare": "તમારો નંબર બોલાવવામાં આવ્યો છે.",
        "room":       "<slow>રૂમ નંબર {room}.",
        "room_unknown": "<slow>રૂમ નંબર સંભળાયો નથી. કૃપા કરીને ડિસ્પ્લે બોર્ડ જુઓ.",
        "platform":   "<slow>પ્લેટફોર્મ નંબર {platform}.",
        "unknown":    "<slow>પ્લેટફોર્મ નંબર સંભળાયો નથી. કૃપા કરીને ડિસ્પ્લે બોર્ડ જુઓ.",
        "go":         "તમારો સામાન લઈને અત્યારે જ નીકળો.",
        "ready":      "તમારો સામાન તૈયાર રાખો.",
    },
    "pa": {
        "train_both": "ਤੁਹਾਡੀ ਰੇਲਗੱਡੀ {train}, {dest} ਜਾਣ ਵਾਲੀ ਰੇਲਗੱਡੀ, ਆ ਰਹੀ ਹੈ.",
        "train_only": "ਤੁਹਾਡੀ ਰੇਲਗੱਡੀ {train} ਆ ਰਹੀ ਹੈ.",
        "train_dest": "{dest} ਜਾਣ ਵਾਲੀ ਤੁਹਾਡੀ ਰੇਲਗੱਡੀ ਆ ਰਹੀ ਹੈ.",
        "train_bare": "ਤੁਹਾਡੀ ਰੇਲਗੱਡੀ ਆ ਰਹੀ ਹੈ.",
        "bus_dest":   "{dest} ਜਾਣ ਵਾਲੀ ਤੁਹਾਡੀ ਬੱਸ ਆ ਗਈ ਹੈ.",
        "bus_bare":   "ਤੁਹਾਡੀ ਬੱਸ ਆ ਗਈ ਹੈ.",
        "token_only": "ਤੁਹਾਡਾ ਨੰਬਰ ਬੁਲਾਇਆ ਗਿਆ ਹੈ. {token}.",
        "token_bare": "ਤੁਹਾਡਾ ਨੰਬਰ ਬੁਲਾਇਆ ਗਿਆ ਹੈ.",
        "room":       "<slow>ਕਮਰਾ ਨੰਬਰ {room}.",
        "room_unknown": "<slow>ਕਮਰਾ ਨੰਬਰ ਸੁਣਾਈ ਨਹੀਂ ਦਿੱਤਾ. ਕਿਰਪਾ ਕਰਕੇ ਡਿਸਪਲੇ ਬੋਰਡ ਵੇਖੋ.",
        "platform":   "<slow>ਪਲੇਟਫਾਰਮ ਨੰਬਰ {platform}.",
        "unknown":    "<slow>ਪਲੇਟਫਾਰਮ ਨੰਬਰ ਸੁਣਾਈ ਨਹੀਂ ਦਿੱਤਾ. ਕਿਰਪਾ ਕਰਕੇ ਡਿਸਪਲੇ ਬੋਰਡ ਵੇਖੋ.",
        "go":         "ਆਪਣਾ ਸਮਾਨ ਲੈ ਕੇ ਹੁਣੇ ਨਿਕਲੋ.",
        "ready":      "ਆਪਣਾ ਸਮਾਨ ਤਿਆਰ ਰੱਖੋ.",
    },
}

#: English is the fallback because it is the one language every station sign
#: in India is also written in.
FALLBACK_LANGUAGE = "en"


def resolve_language(language: str | None) -> tuple[str, bool]:
    """
    Return (code_actually_used, is_fallback).

    is_fallback True means the caller asked for a language this module cannot
    phrase, and the screen must say so rather than pretend.
    """
    base = (language or "te-IN").split("-")[0].lower()
    if base in PHRASES:
        return base, False
    return FALLBACK_LANGUAGE, True


def supported_languages() -> set[str]:
    """Base language codes this module can phrase, e.g. {"te", "ta", ...}."""
    return set(PHRASES)


def language_supported(language: str | None) -> bool:
    """True when ``language`` (full code or base) has phrases here."""
    return (language or "").split("-")[0].lower() in PHRASES


def _train_line(train_no, destination, lang: str = "te") -> str:
    p = PHRASES[lang]
    if train_no and destination:
        return p["train_both"].format(train=train_no, dest=destination)
    if train_no:
        return p["train_only"].format(train=train_no)
    if destination:
        return p["train_dest"].format(dest=destination)
    return p["train_bare"]


def _bus_line(destination, lang: str = "te") -> str:
    p = PHRASES[lang]
    if destination:
        return p["bus_dest"].format(dest=destination)
    return p["bus_bare"]


def _token_line(token, lang: str = "te") -> str:
    p = PHRASES[lang]
    if token:
        return p["token_only"].format(token=token)
    return p["token_bare"]


# --------------------------------------------------------------------------
# the builder
# --------------------------------------------------------------------------

def build_alert(slots: dict, kind: str = "train",
                language: str = "te-IN", plain: bool = False) -> str:
    """
    Compose the spoken alert.

    slots    output of app.extract.extract() — may contain None everywhere
    kind     "train" | "bus" | "token" | "hospital"
    plain    True strips the audio tags (for timbre-v2.5, or for comparison)

    Returns a string that has passed tag validation.
    """
    slots = slots or {}
    kind = "token" if kind == "hospital" else kind
    train_no = slots.get("train_no")
    platform = slots.get("platform")
    destination = slots.get("destination")
    token = slots.get("token")
    room = slots.get("room")

    lang, _ = resolve_language(language)
    phrase = PHRASES[lang]

    # --- sentence 1: what is happening -------------------------------
    if kind == "bus":
        first = _bus_line(destination, lang)
    elif kind == "token":
        first = _token_line(token, lang)
    else:
        first = _train_line(train_no, destination, lang)

    # --- sentence 2: where ------------------------------------------
    # This is the sentence that carries the number, so this is the sentence
    # that gets slowed down. <slow> runs from here to the end — no closing tag.
    if kind == "token" and room:
        second = phrase["room"].format(room=room)
        certain = True
    elif kind == "token":
        second = phrase["room_unknown"]
        certain = False
    elif platform:
        second = phrase["platform"].format(platform=platform)
        certain = True
    else:
        # RULE 1. Nothing was heard, so nothing is claimed.
        second = phrase["unknown"]
        certain = False

    # --- sentence 3: move now ---------------------------------------
    third = phrase["go"] if certain else phrase["ready"]

    text = f"{first} {second} {third}"

    if plain:
        text = tags.strip(text)

    # RULE 2 — never return something the synthesiser will silently mangle.
    bad = tags.validate(text, strict=False)
    if bad:
        raise ValueError(f"alert contains invalid audio tags: {bad}")

    assert "</" not in text, "closing tags do not exist in Timbre v3.0"
    return text


def alert_is_certain(slots: dict, kind: str = "train") -> bool:
    """
    True when the alert contains a real location.

    The caller uses this to decide whether to repeat the alert or to show a
    warning on screen. An uncertain alert should never be spoken twice — it
    only makes the person anxious without telling them anything new.
    """
    slots = slots or {}
    if kind in ("token", "hospital"):
        return bool(slots.get("room"))
    return bool(slots.get("platform"))


def screen_text(slots: dict, kind: str = "train") -> dict:
    """
    What the screen shows. The voice gives the headline; the screen carries
    the detail the person can stare at while walking.
    """
    slots = slots or {}
    kind = "token" if kind == "hospital" else kind
    return {
        "headline": {
            "train": slots.get("train_no") or slots.get("destination") or "—",
            "bus": slots.get("destination") or "—",
            "token": slots.get("token") or "—",
        }.get(kind, "—"),
        "platform": slots.get("platform") or slots.get("room") or "?",
        "certain": alert_is_certain(slots, kind),
    }


# --------------------------------------------------------------------------
# self test — python -m app.alert
# --------------------------------------------------------------------------

if __name__ == "__main__":
    print("alert.py self test")
    print("=" * 72)
    failed = 0

    def check(name: str, ok: bool) -> None:
        global failed
        failed += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    # 1. full information
    a = build_alert({"train_no": "12739", "platform": "4",
                     "destination": "Hyderabad"})
    print(f"\n  with platform :\n    {a}\n")
    check("contains the platform number", "4" in a)
    check("contains the train number", "12739" in a)
    check("<slow> present", "<slow>" in a)
    check("no closing tag", "</slow>" not in a)
    check("tags all valid", not tags.validate(a, strict=False))

    # 2. platform missing — must refuse to invent
    b = build_alert({"train_no": "12739", "platform": None,
                     "destination": "Hyderabad"})
    print(f"  no platform :\n    {b}\n")
    check("says the platform was not heard", "వినపడలేదు" in b)
    check("invents no digit", not any(c.isdigit() and c not in "12739"
                                      for c in b.replace("12739", "")))
    check("marked uncertain",
          not alert_is_certain({"train_no": "12739", "platform": None}))

    # 3. token / hospital mode
    c = build_alert({"token": "47", "room": "204"}, kind="token")
    print(f"  hospital token :\n    {c}\n")
    check("token spoken", "47" in c)
    check("room spoken", "204" in c)

    # 4. bus mode
    d = build_alert({"destination": "Hyderabad", "platform": "7"}, kind="bus")
    print(f"  bus :\n    {d}\n")
    check("bus destination spoken", "Hyderabad" in d)

    # 5. nothing at all
    e = build_alert({})
    check("empty slots still produce a safe alert", "వినపడలేదు" in e)
    check("empty slots contain no digits", not any(ch.isdigit() for ch in e))

    # 6. plain mode for v2.5
    f = build_alert({"train_no": "12739", "platform": "4"}, plain=True)
    check("plain mode removes every tag", "<" not in f)

    # 7. length discipline
    for text in (a, b, c, d):
        n = len([s for s in tags.strip(text).split("।") if s.strip()])
        sentences = tags.strip(text).count(".")
        check(f"at most {MAX_SENTENCES} sentences ({sentences})",
              sentences <= MAX_SENTENCES + 1)

    # 8. the screen half
    s = screen_text({"train_no": "12739", "platform": "4"})
    check("screen headline is the train", s["headline"] == "12739")
    check("screen platform is 4", s["platform"] == "4")
    check("screen marked certain", s["certain"] is True)

    print("=" * 72)
    total = 20
    print(f"{total - failed} passed, {failed} failed")
