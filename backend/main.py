"""Seva backend (Phase 3): Gnani speech-to-text, text-to-speech and announcement detection.
Rules: the API key stays here (.env). Numbers are returned ONLY if found in the transcript.
Transcripts and audio are never logged or stored."""
import os
import re
import unicodedata
from pathlib import Path
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db

load_dotenv()
STT_URL = "https://api.vachana.ai/stt/v3"
TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
STT_LANGS = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"}
MAX_AUDIO_BYTES = 5 * 1024 * 1024
TIMEOUT = 30

app = FastAPI(title="Seva backend")
origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])


def fail(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def api_key():
    return os.getenv("GNANI_API_KEY", "").strip()


def gnani_error(status: int):
    """Friendly messages only; no server internals are shown to the user."""
    if status in (401, 403):
        return fail(502, "auth", "The speech service rejected the API key or credits are finished.")
    if status == 429:
        return fail(429, "rate_limit", "Too many requests. Please wait a moment and try again.")
    if status == 400:
        return fail(400, "bad_request", "The speech service could not use this input.")
    return fail(503, "service_unavailable", "The speech service is not available right now. Please retry.")


@app.get("/health")
def health():
    return {"status": "ok", "gnani_key_configured": bool(api_key())}


# ---------- /listen : audio -> transcript (Gnani Prisma) ----------
@app.post("/listen")
async def listen(audio: UploadFile = File(...), language_code: str = Form("te-IN")):
    if not api_key():
        return fail(500, "config", "Server is missing the Gnani API key.")
    if language_code not in STT_LANGS:
        return fail(400, "bad_language", "Unsupported language code.")
    data = await audio.read()
    if not data:
        return fail(400, "empty_audio", "No audio was received.")
    if len(data) > MAX_AUDIO_BYTES:
        return fail(413, "too_large", "Audio is too large. Send clips of 30 seconds or less.")
    try:
        r = requests.post(
            STT_URL,
            headers={"X-API-Key-ID": api_key()},
            files={"audio_file": (audio.filename or "clip", data, audio.content_type or "application/octet-stream")},
            data={"language_code": language_code, "format": "transcribe"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return fail(503, "network", "Could not reach the speech service. Check the internet and retry.")
    if r.status_code != 200:
        return gnani_error(r.status_code)
    try:
        body = r.json()
        transcript = body["transcript"]
    except (ValueError, KeyError, TypeError):
        return fail(502, "bad_response", "The speech service sent an unexpected reply.")
    if not isinstance(transcript, str):
        return fail(502, "bad_response", "The speech service sent an unexpected reply.")
    return {"transcript": transcript}


# ---------- /detect : transcript -> structured announcement (hospital, train, bus) ----------
class DetectIn(BaseModel):
    transcript: str = Field(max_length=2000)
    mode: str = "hospital"
    value: str | None = Field(default=None, max_length=40)       # token / train number / bus destination
    my_token: str | None = Field(default=None, max_length=12)    # older name, still accepted


# Speech-to-text sometimes writes numbers as words ("forty seven", "నలభై ఏడు") and letters as sounds ("ay").
# normalize() turns all of that into plain digits and lower-case letters before anything is matched.
_U = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
      "సున్నా": 0, "ఒకటి": 1, "ఒక్కటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5, "అయిదు": 5, "ఆరు": 6, "ఏడు": 7,
      "ఎనిమిది": 8, "తొమ్మిది": 9}
_T = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
      "eighteen": 18, "nineteen": 19, "పది": 10, "పదకొండు": 11, "పన్నెండు": 12, "పదమూడు": 13, "పద్నాలుగు": 14, "పధ్నాలుగు": 14,
      "పదిహేను": 15, "పదహారు": 16, "పదిహేడు": 17, "పద్దెనిమిది": 18, "పంతొమ్మిది": 19}
_Y = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
      "ఇరవై": 20, "ముప్పై": 30, "నలభై": 40, "నలబై": 40, "యాభై": 50, "ఏభై": 50, "అరవై": 60, "డెబ్బై": 70, "ఎనభై": 80, "ఎనబై": 80,
      "తొంభై": 90, "తొంబై": 90}
_NUM = {}
for _d, _k in ((_U, "u"), (_T, "t"), (_Y, "y")):
    for _w, _v in _d.items():
        _NUM[_w] = (_k, _v)
for _w in ("hundred", "వంద", "వందల", "నూరు"):
    _NUM[_w] = ("h", 100)
for _w, _v in (("double", 2), ("triple", 3), ("డబుల్", 2), ("ట్రిపుల్", 3)):
    _NUM[_w] = ("d", _v)


def _run_value(run):
    if all(k in ("u", "d") for k, _ in run):          # digits read one by one: "two oh four" -> 204
        out, rep = "", 1
        for k, v in run:
            if k == "d":
                rep = v
            else:
                out, rep = out + str(v) * rep, 1
        return out
    cur = 0
    for k, v in run:
        cur = (cur or 1) * 100 if k == "h" else cur + v
    return str(cur)


def normalize(text: str) -> str:
    text = "".join(str(unicodedata.digit(c)) if unicodedata.category(c) == "Nd" else c for c in text.lower()).replace("-", " ")
    words = re.split(r"(\s+)", text)
    key = lambda w: w.strip(",.;:!?\"'()[]")
    isnum = lambda j: j < len(words) and key(words[j]) in _NUM
    out, run = [], []
    for i, w in enumerate(words):
        if key(w) in _NUM:
            run.append(_NUM[key(w)])
        elif run and w.isspace():
            # stay inside the number if the next word is a number word, or "and" followed by one
            if not (isnum(i + 1) or (i + 2 < len(words) and key(words[i + 1]) == "and" and isnum(i + 3))):
                out.append(_run_value(run))
                run = []
                out.append(w)
        elif run and key(w) == "and":
            continue
        else:
            if run:
                out.append(_run_value(run))
                run = []
            out.append(w)
    if run:
        out.append(_run_value(run))
    text = "".join(out)
    return re.sub(r"(?<!\d)\d(?:\s\d)+(?!\d)", lambda m: re.sub(r"\D", "", m.group(0)), text)   # "4 7" -> "47"


_LETTERS = {"ay": "a", "ey": "a", "eh": "a", "ఏ": "a", "ఎ": "a", "bee": "b", "బీ": "b", "see": "c", "సీ": "c",
            "dee": "d", "డీ": "d", "ee": "e", "ఈ": "e", "ef": "f", "ఎఫ్": "f"}
TOKEN_RE = re.compile(r"(?<![a-z0-9\u0c00-\u0c7f])([a-z]|" + "|".join(sorted(map(re.escape, _LETTERS), key=len, reverse=True))
                      + r")[\s\-]*0*(\d{1,3})(?!\d)")
ROOM_RE = re.compile(r"(?:room(?:\s*(?:number|no\.?))?|గది|రూమ్|कमरा|कक्ष)\s*(?:number|no\.?|నంబర్|नंबर)?\s*[:\-]?\s*(\d{1,4})", re.IGNORECASE)
PLAT_RE = re.compile(r"(?:platform|ప్లాట్\u200cఫామ్|ప్లాట్ఫామ్|ప్లాట్\u200cఫాం|प्लेटफॉर्म|प्लेटफार्म)\s*(?:number|no\.?|నంబర్|नंबर)?\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE)
BAY_RE = re.compile(r"(?:bay|platform|బే)\s*(?:number|no\.?|నంబర్)?\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE)
BUS_WORD = re.compile(r"\bbus\b|బస్సు|बस", re.IGNORECASE)


def find_trains(text: str):
    return [m.group(1) for m in re.finditer(r"(?<!\d)(\d{5})(?!\d)", text)]


@app.post("/detect")
def detect(body: DetectIn):
    text = normalize(body.transcript)
    value = (body.value or body.my_token or "").strip()
    mode = body.mode if body.mode in ("hospital", "train", "bus") else "hospital"
    out = {"schemaVersion": 1, "verdict": "none", "certain": False, "token": None, "room": None,
           "train_no": None, "platform": None, "destination": None}
    if mode == "hospital":
        found = [(m.start(), m.end(), _LETTERS.get(m.group(1), m.group(1)) + str(int(m.group(2)))) for m in TOKEN_RE.finditer(text)]
        v = re.match(r"^\s*([A-Za-z])[\s\-]*0*(\d{1,3})\s*$", value)
        want = (v.group(1).lower() + str(int(v.group(2)))) if v else None
        shown = lambda t: t[0].upper() + "-" + t[1:]
        if found:
            out["token"] = shown(found[0][2])
            out["verdict"] = "other"
            for n, (st, en, tok) in enumerate(found):
                if tok == want:      # your token anywhere in the announcement, even after someone else's
                    seg = text[en:found[n + 1][0] if n + 1 < len(found) else len(text)]
                    r = ROOM_RE.search(seg)
                    out.update(token=shown(tok), room=r.group(1) if r else None, verdict="mine" if r else "missing")
                    break
    elif mode == "train":
        trains = find_trains(text)
        want = re.sub(r"\D", "", value)
        p = PLAT_RE.search(text)
        if trains:
            if want and want in trains:
                out.update(verdict="mine", train_no=want, platform=p.group(1) if p else None)
            else:
                out.update(verdict="other", train_no=trains[0])
    else:
        v = value.lower()
        if v and v.isascii():
            hit = re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", text)
        else:
            hit = bool(v) and v in text
        if hit:
            b = BAY_RE.search(text)
            out.update(verdict="mine", destination=(value.title() if value.isascii() else value), platform=b.group(1) if b else None)
        elif BUS_WORD.search(text):
            out["verdict"] = "other"
    out["certain"] = out["verdict"] == "mine"
    out["type"] = "unclear" if out["verdict"] == "none" else "announcement"          # older fields
    out["match"] = {"mine": "yours", "missing": "yours", "other": "other"}.get(out["verdict"])
    return out


# ---------- /tts : text -> audio (Gnani Timbre) ----------
class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    language: str = "te-IN"


@app.post("/tts")
def tts(body: TtsIn):
    if not api_key():
        return fail(500, "config", "Server is missing the Gnani API key.")
    if body.language not in STT_LANGS:
        return fail(400, "bad_language", "Unsupported language code.")
    payload = {
        "text": body.text,
        "voice": os.getenv("GNANI_TTS_VOICE", "Suhana"),
        "model": os.getenv("GNANI_TTS_MODEL", "timbre-v2.5"),
        "language": body.language,
        "speed": 1.0,
        "audio_config": {"container": "mp3", "sample_rate": 24000, "bitrate": "64k"},
    }
    try:
        r = requests.post(TTS_URL, json=payload, headers={"X-API-Key-ID": api_key()}, timeout=TIMEOUT)
    except requests.RequestException:
        return fail(503, "network", "Could not reach the voice service. Check the internet and retry.")
    if r.status_code != 200:
        return gnani_error(r.status_code)
    return Response(content=r.content, media_type="audio/mpeg")


# ---------- /ask : help-desk question -> answer, from the database (faq.csv -> seva.db) ----------
db.init()


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    lang: str = "te"
    venue: str | None = None        # hospital, train or bus; empty searches everything


@app.post("/ask")
def ask(body: AskIn):
    if body.lang not in ("te", "en"):
        return fail(400, "bad_language", "Supported answer languages are te and en.")
    row = db.search(body.question, body.venue)
    if not row:
        msg = {"te": "క్షమించండి, ఈ సమాచారం నా దగ్గర లేదు. దయచేసి హెల్ప్ డెస్క్‌ను అడగండి.",
               "en": "Sorry, I do not have this information. Please ask the help desk."}[body.lang]
        return {"found": False, "answer": msg, "lang": body.lang, "source": None}
    return {"found": True, "answer": row["answer_" + body.lang], "lang": body.lang,
            "source": "sample FAQ (demo data)" if row["is_sample"] else "hospital list"}


# ---------- the website itself: the pages in the ../web folder (keep this at the very end) ----------
WEB = Path(__file__).resolve().parent.parent / "web"
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
