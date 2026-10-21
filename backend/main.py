"""Seva backend (Phase 3): Gnani speech-to-text, text-to-speech and announcement detection.
Rules: the API key stays here (.env). Numbers are returned ONLY if found in the transcript.
Transcripts and audio are never logged or stored."""
import os
import re
from pathlib import Path
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

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


# ---------- / : serve the website from the same address as the API ----------
HERE = Path(__file__).resolve().parent
INDEX_CANDIDATES = [HERE.parent / "index.html", HERE / "index.html"]


@app.get("/", include_in_schema=False)
def home():
    for page in INDEX_CANDIDATES:
        if page.is_file():
            return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-store"})
    return fail(404, "no_page", "index.html was not found next to the backend folder.")


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


# ---------- /detect : transcript -> structured announcement ----------
class DetectIn(BaseModel):
    transcript: str = Field(max_length=2000)
    my_token: str | None = Field(default=None, max_length=12)


ROOM_WORDS = r"(?:room(?:\s*(?:number|no\.?))?|గది|రూమ్|कमरा|कक्ष)"
TOKEN_RE = re.compile(r"\b([A-Za-z])\s*-?\s*(\d{1,3})\b")
ROOM_RE = re.compile(ROOM_WORDS + r"\s*(?:number|no\.?|నంబర్|नंबर)?\s*[:\-]?\s*(\d{1,4})", re.IGNORECASE)


def norm(token: str | None):
    return re.sub(r"[\s\-]", "", token).upper() if token else None


@app.post("/detect")
def detect(body: DetectIn):
    text = body.transcript
    t = TOKEN_RE.search(text)
    token = f"{t.group(1).upper()}-{t.group(2)}" if t else None
    r = ROOM_RE.search(text)
    room = r.group(1) if r else None
    mine = norm(body.my_token)
    match = None
    if token and mine:
        match = "yours" if norm(token) == mine else "other"
    return {
        "schemaVersion": 1,
        "type": "announcement" if token else "unclear",
        "token": token,
        "room": room,
        "match": match,
    }


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


# ---------- /ask : help-desk question -> answer ----------
# SAMPLE DATA for the demo. Replace the facts with your real hospital's details before real use.
# If nothing matches, Seva says it does not know. It never invents an answer.
FAQ = [
    {"keys": ["opd", "outpatient", "ఓపీ", "ఓపి", "ఓ.పి"],
     "te": "OPD ఉదయం 9 గంటల నుంచి మధ్యాహ్నం 12 గంటల వరకు ఉంటుంది.",
     "en": "OPD is open from 9 AM to 12 noon."},
    {"keys": ["pharmacy", "medicine", "ఫార్మసీ", "మందుల", "మెడికల్"],
     "te": "ఫార్మసీ గ్రౌండ్ ఫ్లోర్‌లో ఉంది.",
     "en": "The pharmacy is on the ground floor."},
    {"keys": ["lab", "blood test", "ల్యాబ్", "రక్త పరీక్ష"],
     "te": "ల్యాబ్ మొదటి అంతస్తులో ఉంది.",
     "en": "The lab is on the first floor."},
    {"keys": ["emergency", "casualty", "ఎమర్జెన్సీ", "అత్యవసర"],
     "te": "ఎమర్జెన్సీ విభాగం ప్రధాన ద్వారం పక్కన ఉంది, 24 గంటలు తెరిచి ఉంటుంది.",
     "en": "The emergency department is next to the main entrance and is open 24 hours."},
    {"keys": ["billing", "bill", "payment", "బిల్లు", "బిల్లింగ్"],
     "te": "బిల్లింగ్ కౌంటర్ గ్రౌండ్ ఫ్లోర్‌లో రిసెప్షన్ దగ్గర ఉంది.",
     "en": "The billing counter is on the ground floor near reception."},
]


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    lang: str = "te"


@app.post("/ask")
def ask(body: AskIn):
    if body.lang not in ("te", "en"):
        return fail(400, "bad_language", "Supported answer languages are te and en.")
    q = body.question.lower()
    best, score = None, 0
    for item in FAQ:
        s = sum(1 for k in item["keys"] if k in q)
        if s > score:
            best, score = item, s
    if not best:
        msg = {"te": "క్షమించండి, ఈ సమాచారం నా దగ్గర లేదు. దయచేసి హెల్ప్ డెస్క్‌ను అడగండి.",
               "en": "Sorry, I do not have this information. Please ask the help desk."}[body.lang]
        return {"found": False, "answer": msg, "lang": body.lang, "source": None}
    return {"found": True, "answer": best[body.lang], "lang": body.lang, "source": "sample FAQ (demo data)"}
