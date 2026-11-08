#!/usr/bin/env python3
"""Prajaa Mitra - a Telugu voice helpline for government schemes.

    python -m app.server
    python -m app.server --port 5000 --model gemma4

Your laptop is both the server and the microphone, which means the browser
never has to encode audio. It just asks the server to listen. That keeps the
whole pipeline in formats Prisma already accepts.

    mic -> Prisma STT -> scheme lookup -> local gemma4 -> Timbre TTS -> speaker
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import secrets
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import (  # noqa: E402
    Flask,
    abort,
    jsonify,
    make_response,
    redirect,
    request,
    send_from_directory,
)

from gnani.client import load_dotenv  # noqa: E402

load_dotenv()

from app import device_audio, gemini, hospital  # noqa: E402
from app.alert import (  # noqa: E402
    alert_is_certain,
    build_alert,
    language_supported,
    supported_languages,
)
from app.brain import BrainError  # noqa: E402
from app.gemini import GeminiError, ask as gemini_ask  # noqa: E402
from app.gemini import normalize_language  # noqa: E402
from app.schemes import SchemeBook  # noqa: E402
from gnani import GnaniClient, GnaniError  # noqa: E402
from gnani.client import TTS_MODEL  # noqa: E402

DATA_DIR = Path(os.environ.get("SEVA_DATA_DIR", str(ROOT))).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = (ROOT / DATA_DIR).resolve()
AUDIO_DIR = DATA_DIR / "samples" / "web"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
ALERT_AUDIO_DIR = DATA_DIR / "samples" / "alerts"
ALERT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = Path(__file__).resolve().parent.parent


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))
app.config["MODEL"] = "gemma4"
app.config["GEMINI_MODEL"] = gemini.configured_model()
app.config["DEBUG"] = False
app.config["SEVA_ENV"] = os.environ.get("SEVA_ENV", "development").strip().lower()
app.config["SERVER_MIC_ENABLED"] = _env_flag(
    "SEVA_ENABLE_SERVER_MIC", default=app.config["SEVA_ENV"] != "production"
)
app.config["SIMULATE_ENABLED"] = _env_flag(
    "SEVA_ENABLE_SIMULATE", default=app.config["SEVA_ENV"] != "production"
)
app.config["SYNTHETIC_DEMO_ENABLED"] = _env_flag(
    "SEVA_ENABLE_SYNTHETIC_DEMO", default=True
)
app.config["USAGE_API_ENABLED"] = _env_flag(
    "SEVA_ENABLE_USAGE_API", default=app.config["SEVA_ENV"] != "production"
)
app.config["DEVICE_MIC_ENABLED"] = _env_flag(
    "SEVA_ENABLE_DEVICE_MIC", default=True
)
app.config["HOSPITAL_DATA_APPROVED"] = _env_flag(
    "SEVA_HOSPITAL_DATA_APPROVED", default=False
)
app.config["HOSPITAL_SOURCE_ID"] = os.environ.get(
    "SEVA_HOSPITAL_SOURCE_ID", ""
).strip()
app.config["HOSPITAL_SOURCE_NAME"] = os.environ.get(
    "SEVA_HOSPITAL_SOURCE_NAME", ""
).strip()
app.config["PREVIEW_AUTH_REQUIRED"] = _env_flag(
    "SEVA_PREVIEW_AUTH_REQUIRED", default=app.config["SEVA_ENV"] == "production"
)
# A bounded 20 s mono 16 kHz 16-bit WAV is under 640 KB. The extra headroom is
# for multipart boundaries; JSON routes still validate their own lengths.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


def _normalize_allowed_hosts(hosts_raw: str) -> list[str] | None:
    raw = [h.strip() for h in hosts_raw.split(",") if h.strip()]
    if not raw:
        return None
    normalized = []
    for h in raw:
        # Support wildcard *.example.com by normalizing to .example.com
        # which Werkzeug's host_is_trusted accepts for subdomain suffix matching.
        if h.startswith("*."):
            normalized.append("." + h[2:])
        else:
            normalized.append(h)
    return normalized


app.config["TRUSTED_HOSTS"] = _normalize_allowed_hosts(
    os.environ.get("SEVA_ALLOWED_HOSTS", "")
)

ASK_RATE_WINDOW_SECONDS = 60
ASK_RATE_LIMIT = 10
ASK_RATE_LOCK = threading.Lock()
ASK_RATE: dict[str, deque[float]] = defaultdict(deque)

#: Speech synthesis is billable at the provider. Replay is a GET route because
#: the History screen streams it through an <audio> element, so it can also be
#: triggered by a third-party page. A per-IP ceiling limits accidental or
#: malicious credit use; it is not a substitute for authentication.
SPEECH_RATE_WINDOW_SECONDS = 60
SPEECH_RATE_LIMIT = 30
SPEECH_RATE_LOCK = threading.Lock()
SPEECH_RATE: dict[str, deque[float]] = defaultdict(deque)

#: Device chunks are billable STT calls. Keep a per-IP ceiling well above a
#: normal conversation (one chunk every few seconds) but low enough to stop a
#: runaway page or a script from burning the whole quota.
DEVICE_RATE_WINDOW_SECONDS = 60
DEVICE_RATE_LIMIT = 45
DEVICE_RATE_LOCK = threading.Lock()
DEVICE_RATE: dict[str, deque[float]] = defaultdict(deque)

#: Raw device audio is written only to this short-lived directory. Files are
#: deleted immediately after transcription, never served, never cached.
DEVICE_TEMP_DIR = DATA_DIR / "samples" / "device-tmp"

# --- serve the 17 mobile screens from this same server (no CORS, one origin) ---
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/app/")
def seva_home():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/app/<path:name>")
def seva_page(name: str):
    return send_from_directory(WEB_DIR, name)


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Every page is served from this origin. Inline scripts and styles are
    # covered by 'unsafe-inline' because the PWA is intentionally build-free;
    # external script/style/connect origins are still refused.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; img-src 'self' data:; media-src 'self' blob:; "
        "font-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "form-action 'self'; manifest-src 'self'",
    )
    # The device microphone is the primary input on a phone. It is allowed only
    # for this origin (self), only after the visitor grants permission, and
    # only while the listening screen is open. Camera and location stay denied.
    response.headers.setdefault(
        "Permissions-Policy", "microphone=(self), camera=(), geolocation=()"
    )
    if request.path.startswith(("/api/device/", "/api/hospital/")):
        # Session state, verdicts, and announcement payloads must not be stored
        # by a browser or proxy.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    if app.config["SEVA_ENV"] == "production" and request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.before_request
def require_production_https():
    if (
        app.config["SEVA_ENV"] == "production"
        and request.endpoint != "healthz"
        and not request.is_secure
    ):
        return redirect(request.url.replace("http://", "https://", 1), code=308)
    if (
        app.config["PREVIEW_AUTH_REQUIRED"]
        and request.endpoint != "healthz"
    ):
        username = os.environ.get("SEVA_PREVIEW_USERNAME", "").strip()
        password = os.environ.get("SEVA_PREVIEW_PASSWORD", "")
        auth = request.authorization
        if not username or not password:
            return jsonify({"error": "Preview authentication is not configured."}), 503
        if (
            auth is None
            or not secrets.compare_digest(auth.username or "", username)
            or not secrets.compare_digest(auth.password or "", password)
        ):
            response = make_response(jsonify({"error": "Authentication required."}), 401)
            response.headers["WWW-Authenticate"] = (
                'Basic realm="Seva preview", charset="UTF-8"'
            )
            return response
    return None


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


app.config["VOICE"] = "Chetana"   # v3.0 healthcare voice

book = SchemeBook(
    enabled=(
        app.config["SEVA_ENV"] != "production"
        or _env_flag("SEVA_HOSPITAL_DATA_APPROVED", default=False)
    )
)
client: GnaniClient | None = None


def get_client() -> GnaniClient:
    global client
    if client is None:
        client = GnaniClient()
    return client


def record(seconds: float) -> Path:
    """Capture `seconds` of mono 16 kHz audio - exactly what Prisma wants."""
    import sounddevice as sd
    import wave

    rate = 16000
    frames = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
    sd.wait()
    path = AUDIO_DIR / f"q-{uuid.uuid4().hex[:8]}.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())
    return path


#: A temporary reply must still be in the language the person selected.
#: English is only used when English is the selected language.
FALLBACK_MESSAGES = {
    "en": "Sorry, Nova cannot answer right now. For an emergency, alert hospital staff or call 108.",
    "te": "క్షమించండి, Nova ప్రస్తుతం సమాధానం ఇవ్వలేకపోతోంది. అత్యవసరమైతే ఆసుపత్రి సిబ్బందిని సంప్రదించండి లేదా 108కు కాల్ చేయండి.",
    "hi": "क्षमा करें, Nova अभी उत्तर नहीं दे पा रही है। आपातकाल में अस्पताल के कर्मचारियों से कहें या 108 पर कॉल करें।",
    "ta": "மன்னிக்கவும், Nova இப்போது பதிலளிக்க முடியவில்லை. அவசரமானால் மருத்துவமனை ஊழியர்களை அணுகவும் அல்லது 108-ஐ அழைக்கவும்.",
    "kn": "ಕ್ಷಮಿಸಿ, Nova ಈಗ ಉತ್ತರಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ. ತುರ್ತು ಪರಿಸ್ಥಿತಿಯಲ್ಲಿ ಆಸ್ಪತ್ರೆ ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸಿ ಅಥವಾ 108 ಗೆ ಕರೆ ಮಾಡಿ.",
    "ml": "ക്ഷമിക്കണം, Nova ഇപ്പോൾ ഉത്തരം നൽകാൻ കഴിയുന്നില്ല. അടിയന്തരമാണെങ്കിൽ ആശുപത്രി ജീവനക്കാരെ അറിയിക്കുക അല്ലെങ്കിൽ 108-ൽ വിളിക്കുക.",
    "mr": "क्षमस्व, Nova आता उत्तर देऊ शकत नाही. आपत्कालीन परिस्थितीत रुग्णालयातील कर्मचाऱ्यांना सांगा किंवा 108 वर कॉल करा.",
    "bn": "দুঃখিত, Nova এখন উত্তর দিতে পারছে না। জরুরি হলে হাসপাতালের কর্মীদের জানান অথবা 108-এ কল করুন।",
    "gu": "માફ કરશો, Nova હવે જવાબ આપી શકતું નથી. કટોકટીમાં હોસ્પિટલના કર્મચારીઓને જાણ કરો અથવા 108 પર કૉલ કરો.",
    "pa": "ਮਾਫ਼ ਕਰਨਾ, Nova ਹੁਣ ਜਵਾਬ ਨਹੀਂ ਦੇ ਸਕਦਾ। ਐਮਰਜੈਂਸੀ ਹੋਵੇ ਤਾਂ ਹਸਪਤਾਲ ਦੇ ਸਟਾਫ਼ ਨੂੰ ਦੱਸੋ ਜਾਂ 108 'ਤੇ ਕਾਲ ਕਰੋ।",
}


def _localized_fallback(language: str, has_local_match: bool = False) -> str:
    code = (normalize_language(language) or "en-IN").split("-", 1)[0]
    return FALLBACK_MESSAGES.get(code, FALLBACK_MESSAGES["en"])


def _local_fallback(matches: list[dict], language: str) -> str:
    """Reply without Gemini, still in the selected language.

    The hospital JSON stores Telugu names but English fact text. Speaking that
    English text with a Telugu voice is exactly the bug we are fixing, so only
    an English selection may include the stored facts. Every other language
    gets a complete, localized apology instead of a silent English reply.
    """
    code = (normalize_language(language) or "en-IN").split("-", 1)[0]
    if code == "en" and matches:
        scheme = matches[0]
        fact = f"{scheme['name_en']}. {scheme['benefit_te']} {scheme['how_te']}"
        return "Gemini is unavailable. Relevant local hospital information: " + fact
    return _localized_fallback(language, bool(matches))


def _allow_ask(remote_addr: str) -> bool:
    now = time.monotonic()
    with ASK_RATE_LOCK:
        recent = ASK_RATE[remote_addr]
        while recent and now - recent[0] >= ASK_RATE_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= ASK_RATE_LIMIT:
            return False
        recent.append(now)
        return True


def _allow_speech(remote_addr: str) -> bool:
    now = time.monotonic()
    with SPEECH_RATE_LOCK:
        recent = SPEECH_RATE[remote_addr]
        while recent and now - recent[0] >= SPEECH_RATE_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= SPEECH_RATE_LIMIT:
            return False
        recent.append(now)
        return True


_MARKDOWN_HEADING = re.compile(r"^\s*#{1,6}\s*")
_MARKDOWN_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")
_MARKDOWN_EMPHASIS = re.compile(r"(\*\*|__|`)")


def _for_speech(text: str) -> str:
    """Strip Markdown that sounds wrong aloud, but keep URLs and identifiers."""
    lines: list[str] = []
    for raw in str(text or "").replace("\r", "").splitlines():
        line = _MARKDOWN_HEADING.sub("", raw.strip())
        line = _MARKDOWN_BULLET.sub("", line)
        line = _MARKDOWN_EMPHASIS.sub("", line)
        if line.strip():
            lines.append(line.strip())
    return " ".join(lines)


def _tts_voice(language: str) -> str:
    """Pick a Timbre voice that is valid for this model and language.

    v3.0 voices speak every supported language, so the configured persona
    (Chetana by default) is kept. If someone configures a v2.5 model, the
    per-language default is used instead of a voice that cannot speak it.
    """
    from gnani.voices import V3_MODEL, default_voice, validate

    model = getattr(get_client(), "tts_model", V3_MODEL)
    configured = app.config.get("VOICE")
    try:
        return validate(language, configured, model)
    except ValueError:
        return default_voice(language, model)


def speak_answer(reply: str, language: str) -> str | None:
    """Synthesise the answer in the selected language.

    Raises on failure so the caller can report a clear audio error instead of
    silently letting the browser read the text with an English voice.
    """
    selected_language = normalize_language(language)
    if selected_language is None:
        raise ValueError(f"No voice is configured for {language!r}.")
    text = _for_speech(reply)
    if not text:
        return None
    audio = AUDIO_DIR / f"a-{uuid.uuid4().hex[:8]}.wav"
    get_client().speak(text, language=selected_language, voice=_tts_voice(selected_language),
                       sample_rate=16000, out_path=audio)
    return f"/audio/{audio.name}"


def answer(question: str, language: str = "te-IN") -> dict:
    """Answer with Gemini first and use matching hospital facts on failure."""
    timings: dict[str, int] = {}
    selected_language = normalize_language(language)
    if selected_language is None:
        raise ValueError(
            f"No voice is configured for {language!r}. "
            f"Choose one of: {', '.join(sorted(gemini.SUPPORTED_LANGUAGES))}."
        )

    started = time.time()
    matches = book.search(question)
    context = book.as_context(matches)
    timings["lookup_ms"] = int((time.time() - started) * 1000)

    started = time.time()
    source = "gemini"
    fallback = False
    try:
        reply = gemini_ask(
            question, selected_language, context,
            model=app.config["GEMINI_MODEL"], timeout=gemini.REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Provider failures must not turn an answer into a traceback or 5xx.
        app.logger.warning("[nova] Gemini query failed (%s: %s). Using fallback.", type(exc).__name__, exc)
        reply = _local_fallback(matches, selected_language)
        source = "hospital_faq" if matches else "fallback"
        fallback = True
    timings["think_ms"] = int((time.time() - started) * 1000)

    started = time.time()
    audio_url = None
    audio_error = None
    try:
        audio_url = speak_answer(reply, selected_language)
    except (GnaniError, ValueError, OSError) as exc:
        # Do not hide it: the screen offers a retry and says the language.
        audio_error = str(exc) or "Voice synthesis failed."
    timings["speak_ms"] = int((time.time() - started) * 1000)

    return {
        "question": question,
        "answer": reply,
        "answer_language": selected_language,
        "language": selected_language,
        "spoken_text": _for_speech(reply),
        "audio": audio_url,
        "audio_error": audio_error,
        "schemes": [{"name": s["name_te"], "url": s["url"]} for s in matches],
        "timings": timings,
        "source": source,
        "fallback": fallback,
    }


MAX_SPEECH_CHARS = 2000


def _speech_request(data: dict) -> tuple[str, str]:
    """Validate text/language for a speech request, raising ValueError."""
    text = data.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    text = text.strip()
    language = data.get("language") or data.get("lang") or "en-IN"
    if not isinstance(language, str):
        raise ValueError("language must be a string")
    if not text:
        raise ValueError("nothing to speak")
    if len(text) > MAX_SPEECH_CHARS:
        raise ValueError(f"text is too long (limit {MAX_SPEECH_CHARS} characters)")
    selected_language = normalize_language(language)
    if selected_language is None or not language_supported(selected_language):
        raise ValueError(
            f"No voice is configured for {language!r}. "
            f"Choose one of: {', '.join(sorted(supported_languages()))}."
        )
    return text, selected_language


def _speech_file(text: str, language: str, prefix: str) -> Path:
    """Return a deterministic audio file, synthesising it only once.

    Replaying the same sentence must not pay Gnani twice. The hash covers the
    exact text and language, so a changed sentence always gets a new file.
    """
    digest = hashlib.sha256(f"{language}\0{text}".encode("utf-8")).hexdigest()[:20]
    out = ALERT_AUDIO_DIR / f"{prefix}-{digest}.wav"
    if out.is_file() and out.stat().st_size > 0:
        return out
    get_client().speak(
        text, language=language, voice=_tts_voice(language),
        sample_rate=16000, out_path=out,
    )
    if not out.is_file() or out.stat().st_size == 0:
        raise OSError("voice service produced no audio")
    return out


def _speech_response(data: dict, prefix: str):
    try:
        text, language = _speech_request(data)
        out = _speech_file(text, language, prefix)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (GnaniError, OSError):
        return jsonify({"error": "Voice synthesis failed."}), 502
    response = send_from_directory(ALERT_AUDIO_DIR, out.name, mimetype="audio/wav")
    # Generated speech can contain a token, room or address. Keep it out of the
    # browser's disk cache; the server still caches the file to save credits.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    """The PWA starts at /app/; keep a single canonical entry point."""
    return redirect("/app/", code=302)


@app.get("/audio/<path:name>")
def audio(name: str):
    if not re.fullmatch(r"a-[0-9a-f]{8}\.wav", name):
        abort(404)
    response = make_response(send_from_directory(AUDIO_DIR, name))
    response.headers["Cache-Control"] = "no-store"
    return response


ALERT_AUDIO_NAME = re.compile(
    r"(?:alert-\d+-[0-9a-f]{6,32}|(?:tts|replay)-[0-9a-f]{20})\.wav"
)


@app.get("/alerts/<path:name>")
def alert_audio(name: str):
    if not ALERT_AUDIO_NAME.fullmatch(name):
        abort(404)
    response = make_response(send_from_directory(ALERT_AUDIO_DIR, name))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/tts")
def tts():
    """Synthesise arbitrary text in the requested language.

    The confirmation screen calls this when an alert was not pre-synthesised.
    An unsupported language is a clear 400, never a silent English fallback.
    """
    if not _allow_speech(request.remote_addr or "unknown"):
        return jsonify({"error": "too many speech requests; please wait a minute"}), 429
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    return _speech_response(body, "tts")


@app.route("/api/replay", methods=["GET", "POST"])
def api_replay():
    """Replay a previously spoken line as server-generated audio.

    History stores the sentence, not a media file. Synthesising on demand from
    the stored text keeps replays working after a restart, and the hash cache
    means the same line is only paid for once.
    """
    if not _allow_speech(request.remote_addr or "unknown"):
        return jsonify({"error": "too many speech requests; please wait a minute"}), 429
    data = dict(request.args)
    body = request.get_json(silent=True)
    if request.method == "POST" and not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    if isinstance(body, dict):
        data.update(body)
    return _speech_response(data, "replay")


@app.post("/api/ask")
def api_ask():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    question = body.get("question", "")
    if not isinstance(question, str):
        return jsonify({"error": "invalid question"}), 400
    question = question.strip()
    raw_language = body.get("language", "en-IN")
    language = normalize_language(raw_language if isinstance(raw_language, str) else "")
    if not question:
        return jsonify({"error": "empty question"}), 400
    if len(question) > 500:
        return jsonify({"error": "question is too long"}), 400
    if language is None:
        return jsonify({
            "error": f"No voice is configured for {raw_language!r}. "
                     f"Choose one of: {', '.join(sorted(gemini.SUPPORTED_LANGUAGES))}."
        }), 400
    if not _allow_ask(request.remote_addr or "unknown"):
        return jsonify({"error": "too many questions; please wait a minute"}), 429
    try:
        return jsonify(answer(question, language))
    except Exception as e:
        resp = {
            "question": question,
            "answer": _localized_fallback(language, False),
            "answer_language": language,
            "language": language,
            "spoken_text": _localized_fallback(language, False),
            "audio": None,
            "audio_error": "Voice synthesis failed.",
            "schemes": [],
            "timings": {},
            "source": "fallback",
            "fallback": True,
        }
        if (
            app.config["SEVA_ENV"] != "production"
            and os.environ.get("SEVA_DEBUG") == "1"
        ):
            reason = type(e).__name__
            if isinstance(e, GeminiError):
                reason = "gemini_error"
            elif isinstance(e, GnaniError):
                reason = "gnani_error"
            resp["debug"] = {"reason": reason}
            import sys
            print(f"[api_ask] {reason}: {str(e)[:100]}", file=sys.stderr)
        return jsonify(resp)


@app.post("/api/listen")
def api_listen():
    if not app.config["SERVER_MIC_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    try:
        seconds = float(body.get("seconds", 6))
    except (TypeError, ValueError):
        return jsonify({"error": "seconds must be between 1 and 20"}), 400
    if not math.isfinite(seconds) or not 1 <= seconds <= 20:
        return jsonify({"error": "seconds must be between 1 and 20"}), 400
    raw_language = body.get("language", "te-IN")
    language = normalize_language(raw_language if isinstance(raw_language, str) else "")
    if language is None:
        return jsonify({
            "error": f"No voice is configured for {raw_language!r}. "
                     f"Choose one of: {', '.join(sorted(gemini.SUPPORTED_LANGUAGES))}."
        }), 400
    wav = None
    try:
        started = time.time()
        wav = record(seconds)
        heard = get_client().transcribe(wav, language_code=language)
        hear_ms = int((time.time() - started) * 1000) - int(seconds * 1000)
        if not heard.strip():
            return jsonify({"error": "వినిపించలేదు. మళ్ళీ చెప్పండి."}), 422
        result = answer(heard, language)
        result["timings"]["hear_ms"] = max(hear_ms, 0)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "sounddevice missing - run: pip install sounddevice"}), 500
    except (GnaniError, BrainError):
        return jsonify({"error": "Speech or answer service failed."}), 502
    finally:
        if wav is not None:
            try:
                wav.unlink(missing_ok=True)
            except OSError as exc:
                app.logger.error(
                    "Could not remove temporary microphone recording (%s)",
                    type(exc).__name__,
                )




# --------------------------------------------------------------------------
# announcement watch mode — the ear
# --------------------------------------------------------------------------

WATCH: dict = {
    "active": False,
    "kind": None,
    "value": None,
    "language": "en-IN",
    "alerts": [],
    "heard": 0,
    "error": None,
    "thread": None,
    "mic_device": None,
    "input_level": 0.0,
    "input_db": None,
    "background_level": None,
    "gate_threshold_db": None,
    "gate_state": None,
    "gate_open": False,
    "clips": 0,
    "stt_calls": 0,
    "last_clip_seconds": None,
    "last_stt_language": None,
    "last_stt_error": None,
    "last_stt_empty": None,
    "last_heard_text": None,
    "last_match_verdict": None,
    "last_match_token": None,
    "last_match_room": None,
    "key_found": None,
}
WATCH_LOCK = threading.Lock()
ALERT_REPEAT_WINDOW_SECONDS = 20
_RECENT_ALERTS: dict[tuple, float] = {}


def _claim_alert(kind: str, value: str, match: dict) -> bool:
    """Claim a semantic alert once per short repeat window."""
    verdict = match.get("verdict")
    if kind in ("hospital", "token"):
        details = (match.get("token"), match.get("room"))
    elif kind == "train":
        details = (match.get("train_no"), match.get("platform"))
    else:
        details = (value, match.get("platform"))
    signature = (kind, value, verdict, *details)
    now = time.time()
    with WATCH_LOCK:
        expired = [
            key for key, timestamp in _RECENT_ALERTS.items()
            if now - timestamp >= ALERT_REPEAT_WINDOW_SECONDS
        ]
        for key in expired:
            del _RECENT_ALERTS[key]
        previous = _RECENT_ALERTS.get(signature)
        if previous is not None and now - previous < ALERT_REPEAT_WINDOW_SECONDS:
            return False
        _RECENT_ALERTS[signature] = now
        return True


def _input_device_name() -> str:
    """Return a readable input-device name without assuming sounddevice returns a dict."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("sounddevice is not installed on the server.") from exc

    devices = sd.query_devices(kind="input")
    if isinstance(devices, dict):
        name = devices.get("name") or devices.get("device")
        if name:
            return str(name)
    elif isinstance(devices, list):
        for device in devices:
            if isinstance(device, dict):
                name = device.get("name")
                if name:
                    return str(name)

    default = getattr(sd, "default", None)
    if default is not None:
        device = getattr(default, "device", None)
        if isinstance(device, (list, tuple)) and len(device) >= 2:
            return f"Default input device ({device[0]}, {device[1]})"
        if device not in (None, ""):
            return str(device)
    return "Default Microphone"

#: Kinds the matcher and alert builder understand. "token" is kept as a
#: legacy alias for hospital tokens.
WATCH_KINDS = ("hospital", "token", "train", "bus")


def _new_alert_id() -> str:
    """Return a unique id so the listening screen can skip an alert it has
    already shown after returning to the listening page."""
    return uuid.uuid4().hex


def _synthesize_alert(alert_text: str, language: str) -> str | None:
    """Generate Timbre TTS audio for the alert and return audio filename.

    The language must have a phrase set and a Timbre v3.0 voice. Callers
    validate first; this guard exists so a bad language can never quietly
    produce English audio.
    """
    if not alert_text:
        return None
    if not language_supported(language):
        raise ValueError(f"No voice is configured for {language!r}.")
    try:
        out_path = ALERT_AUDIO_DIR / f"alert-{int(time.time())}-{uuid.uuid4().hex[:20]}.wav"
        get_client().speak(
            alert_text, language=language,
            voice=_tts_voice(language), out_path=out_path,
        )
        return out_path.name
    except (GnaniError, ValueError, OSError):
        return None


def _spoken_language(language: str | None) -> str:
    base = (language or "").split("-")[0].lower()
    return f"{base}-IN" if base else ""


def _compose_alert(verdict: str, kind: str, token: str | None,
                   room: str | None, language: str,
                   slots: dict | None = None) -> tuple[str, str, str]:
    """Returns (spoken_text, screen_room_text, spoken_language).

    All phrasing lives in app.alert so every UI language is covered by the
    same tested templates. Train and bus alerts carry their platform and
    destination through ``slots`` instead of being dropped.
    """
    if not language_supported(language):
        raise ValueError(
            f"No voice is configured for {language!r}. "
            f"Choose one of: {', '.join(sorted(supported_languages()))}."
        )
    spoken_language = _spoken_language(language)
    if verdict not in ("mine", "missing"):
        return "", str(room or ""), spoken_language

    if slots is None:
        slots = {}
    slots = dict(slots)
    if token is not None:
        slots["token"] = token
    if room is not None:
        slots["room"] = room
    if kind in ("hospital", "token"):
        slots.pop("platform", None)
        slots.pop("destination", None)
        slots.pop("train_no", None)

    spoken = build_alert(slots, kind=kind, language=language)
    display_room = str(room) if room else ""
    return spoken, display_room, spoken_language


def _process_transcript(text: str, kind: str, value: str, language: str, pending_missing: dict) -> dict | None:
    """
    Process transcript through matcher.
    Handles 16s repeat window for missing room.
    Returns alert dict or None.
    """
    from app.matcher import match_announcement
    match = match_announcement(kind, text, value)
    verdict = match.get("verdict")

    with WATCH_LOCK:
        WATCH["last_match_verdict"] = verdict
        WATCH["last_match_token"] = match.get("token")
        WATCH["last_match_room"] = match.get("room")

    if verdict == "none":
        return None

    if verdict == "other":
        return {
            "schemaVersion": 1,
            "verdict": "other",
            "token": match.get("token"),
            "room": match.get("room"),
            "train_no": match.get("train_no"),
            "train_name": match.get("train_name"),
            "platform": match.get("platform"),
            "destination": match.get("destination"),
            "specialist": match.get("specialist"),
            "certain": True,
        }

    if verdict == "missing":
        # Token matched, but room not heard / garbled.
        now = time.time()
        if not pending_missing.get("active"):
            pending_missing["active"] = True
            pending_missing["started_at"] = now
            pending_missing["token"] = match.get("token") or str(value)
            pending_missing["transcript"] = text
            # Wait up to 16s for a repeat before declaring missing
            return None
        return None

    if verdict == "mine":
        if not _claim_alert(kind, value, match):
            return None
        token = match.get("token") or str(value)
        room = match.get("room")
        pending_missing["active"] = False
        slots = {
            "train_no": match.get("train_no"),
            "platform": match.get("platform"),
            "destination": match.get("destination"),
            "token": token,
            "room": room,
        }
        spoken, display_room, spoken_language = _compose_alert(
            "mine", kind, token, room, language, slots=slots
        )
        certain = alert_is_certain(slots, kind)
        audio_name = _synthesize_alert(spoken, spoken_language)
        tts_error = None
        if spoken and not audio_name:
            tts_error = (
                f"Voice audio could not be generated in {spoken_language}. "
                "Check the Gnani key, credits and voice configuration."
            )
        return {
            "schemaVersion": 1,
            "at": time.strftime("%H:%M:%S"),
            "verdict": "mine",
            "certain": certain,
            "token": token,
            "room": display_room,
            "train_no": match.get("train_no"),
            "train_name": match.get("train_name"),
            "platform": match.get("platform") or display_room,
            "destination": match.get("destination"),
            "specialist": match.get("specialist"),
            "spoken": spoken,
            "lang": spoken_language,
            "requested_language": language,
            "audio": audio_name,
            "audio_url": f"/alerts/{audio_name}" if audio_name else None,
            "tts_error": tts_error,
            "transcript": text,
        }

    return None


def _watch_loop(kind: str, value: str, language: str, trigger_db: float,
                max_calls: int) -> None:
    """Background microphone listener. Writes into WATCH for the UI to poll."""
    import numpy as np
    from app.gate import Gate, rms, db_above
    from app.listen import FRAME_LEN, SAMPLE_RATE, FRAME_MS, MAX_CLIP_SECONDS, PRE_ROLL_FRAMES, write_clip

    pending_missing: dict = {"active": False, "started_at": 0, "token": None, "transcript": None}

    try:
        import sounddevice as sd
    except ImportError:
        with WATCH_LOCK:
            WATCH["error"] = "sounddevice is not installed on the server."
            WATCH["active"] = False
        return

    try:
        mic_name = _input_device_name()
        with WATCH_LOCK:
            WATCH["mic_device"] = mic_name
    except Exception:
        with WATCH_LOCK:
            WATCH["error"] = "Microphone not detected. Please check your microphone connection."
            WATCH["active"] = False
        return

    g = Gate(trigger_db=trigger_db)
    with WATCH_LOCK:
        WATCH["gate_threshold_db"] = trigger_db
        WATCH["gate_state"] = "waiting"
        WATCH["gate_open"] = False
        WATCH["last_stt_error"] = None
    buffer: list = []
    pre: list = []
    clips = 0
    tmp_dir = DATA_DIR / "samples" / "clips"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    calls = 0

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=FRAME_LEN) as stream:
            while True:
                with WATCH_LOCK:
                    if not WATCH["active"]:
                        break

                block, _ = stream.read(FRAME_LEN)
                frame = block[:, 0]
                lvl = rms(frame)
                before_bg = g.background

                # 16-second timeout check for pending missing room
                if pending_missing.get("active") and time.time() - pending_missing["started_at"] >= 16.0:
                    pending_missing["active"] = False
                    tok = pending_missing["token"]
                    slots = {}
                    record_token = record_train = record_dest = None
                    if kind in ("hospital", "token"):
                        slots["token"] = tok
                        record_token = tok
                    elif kind == "train":
                        slots["train_no"] = tok
                        record_train = tok
                    elif kind == "bus":
                        slots["destination"] = tok
                        record_dest = tok
                    missing_match = {
                        "verdict": "missing", "token": record_token,
                        "train_no": record_train, "destination": record_dest,
                        "room": None, "platform": None,
                    }
                    if _claim_alert(kind, value, missing_match):
                        spoken, display_room, spoken_language = _compose_alert(
                            "missing", kind, record_token, None, language, slots=slots
                        )
                        audio_name = _synthesize_alert(spoken, spoken_language)
                        tts_error = None
                        if spoken and not audio_name:
                            tts_error = (
                                f"Voice audio could not be generated in {spoken_language}. "
                                "Check the Gnani key, credits and voice configuration."
                            )
                        with WATCH_LOCK:
                            WATCH["alerts"].append({
                                "schemaVersion": 1,
                                "id": _new_alert_id(),
                                "verdict": "missing",
                                "token": record_token,
                                "room": None,
                                "train_no": record_train,
                                "train_name": None,
                                "platform": None,
                                "destination": record_dest,
                                "specialist": None,
                                "certain": False,
                                "spoken": spoken,
                                "lang": spoken_language,
                                "requested_language": language,
                                "audio": audio_name,
                                "audio_url": f"/alerts/{audio_name}" if audio_name else None,
                                "tts_error": tts_error,
                            })

                state = g.push(frame)
                with WATCH_LOCK:
                    WATCH["input_level"] = round(lvl, 4)
                    WATCH["input_db"] = round(db_above(lvl, before_bg), 1)
                    WATCH["background_level"] = round(g.background, 4)
                    WATCH["gate_state"] = state
                    WATCH["gate_open"] = g.open

                if state == "idle":
                    pre.append(frame)
                    pre[:] = pre[-PRE_ROLL_FRAMES:]
                elif state == "start":
                    buffer = list(pre) + [frame]
                elif state == "open":
                    buffer.append(frame)
                    if len(buffer) * FRAME_MS / 1000 > MAX_CLIP_SECONDS:
                        state = "end"

                if state == "end" and buffer:
                    full_clip = np.concatenate(buffer)
                    if rms(full_clip) >= 0.003:
                        clips += 1
                        clip_path = tmp_dir / f"watch-{clips:03d}.wav"
                        write_clip(buffer, clip_path)
                        with WATCH_LOCK:
                            WATCH["clips"] = clips
                            WATCH["last_clip_seconds"] = round(len(buffer) * FRAME_MS / 1000, 2)
                            WATCH["last_stt_language"] = None
                            WATCH["last_stt_empty"] = None
                            WATCH["last_stt_error"] = None
                            WATCH["last_match_verdict"] = None
                            WATCH["last_match_token"] = None
                            WATCH["last_match_room"] = None

                        try:
                            client = get_client()
                            # The reply language is the listener's choice; the
                            # announcement language is whatever was actually
                            # spoken. Prisma v3.0 auto-detects it.
                            calls += 1
                            stt = client.transcribe_multilingual(clip_path)
                            text = stt.get("transcript", "")
                            with WATCH_LOCK:
                                WATCH["stt_calls"] = calls
                                WATCH["last_stt_language"] = stt.get("detected_language_code")
                                WATCH["last_stt_error"] = None
                                WATCH["last_stt_empty"] = not bool(text and text.strip())
                            if text and text.strip():
                                with WATCH_LOCK:
                                    WATCH["heard"] += 1
                                    WATCH["last_heard_text"] = text.strip()

                                alert_record = _process_transcript(
                                    text, kind, value, language, pending_missing
                                )
                                if alert_record:
                                    with WATCH_LOCK:
                                        alert_record["id"] = _new_alert_id()
                                        WATCH["alerts"].append(alert_record)
                        except GnaniError as gerr:
                            msg = str(gerr)
                            lowered = msg.lower()
                            if "401" in msg or "403" in msg or "forbidden" in lowered:
                                clean_err = "The speech service rejected the API key or credits are finished."
                                with WATCH_LOCK:
                                    WATCH["stt_calls"] = calls
                                    WATCH["error"] = clean_err
                                    WATCH["last_stt_error"] = clean_err
                                    WATCH["active"] = False
                                break
                            if ("timed out" in lowered or "connection" in lowered
                                    or "504" in msg or "503" in msg):
                                # Transient upstream hiccup: keep the mic open.
                                with WATCH_LOCK:
                                    WATCH["stt_calls"] = calls
                                    WATCH["last_stt_error"] = (
                                        "Speech service was busy; still listening."
                                    )
                            else:
                                clean_err = "The speech service returned an error."
                                if os.environ.get("SEVA_DEBUG") == "1":
                                    clean_err = f"Speech service error: {gerr}"
                                with WATCH_LOCK:
                                    WATCH["stt_calls"] = calls
                                    WATCH["error"] = clean_err
                                    WATCH["last_stt_error"] = clean_err
                                    WATCH["active"] = False
                                break
                        except Exception as exc:
                            message = "Transcription failed. Check the speech service."
                            if os.environ.get("SEVA_DEBUG") == "1":
                                message = f"Transcription error: {exc}"
                            with WATCH_LOCK:
                                WATCH["stt_calls"] = calls
                                WATCH["error"] = message
                                WATCH["last_stt_error"] = message
                                WATCH["active"] = False
                            break
                        finally:
                            try:
                                clip_path.unlink()
                            except OSError:
                                pass

                    buffer = []
                    if calls >= max_calls:
                        break
    except Exception as exc:
        message = "Microphone error. Check the device connection."
        if os.environ.get("SEVA_DEBUG") == "1":
            message = f"Microphone error: {exc}"
        with WATCH_LOCK:
            WATCH["error"] = message
            WATCH["active"] = False
    finally:
        with WATCH_LOCK:
            WATCH["active"] = False


MAX_WATCH_VALUE_CHARS = 120
MAX_TRANSCRIPT_CHARS = 2000


def _valid_watch_value(kind: str, value: str) -> bool:
    if len(value) > MAX_WATCH_VALUE_CHARS:
        return False
    if kind == "train":
        return value.isascii() and value.isdigit() and len(value) == 5
    if kind in ("hospital", "token"):
        from app.extract import token_key
        return token_key(value) is not None
    return True


def _watch_value_error(kind: str) -> str:
    if kind == "train":
        return "train values must be exactly five digits"
    if kind in ("hospital", "token"):
        return "token values must be digits, optionally with a block letter"
    return f"{kind} values must be digits, optionally with a block letter"


def _client_ip() -> str:
    return str(request.remote_addr or "")


def _device_rate_ok(ip: str) -> bool:
    now = time.time()
    with DEVICE_RATE_LOCK:
        bucket = DEVICE_RATE[ip]
        while bucket and now - bucket[0] >= DEVICE_RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= DEVICE_RATE_LIMIT:
            return False
        bucket.append(now)
        if len(DEVICE_RATE) > 1000:
            stale = [
                key for key, values in DEVICE_RATE.items()
                if not values or now - values[-1] >= DEVICE_RATE_WINDOW_SECONDS
            ]
            for key in stale:
                DEVICE_RATE.pop(key, None)
        return True


def _device_client():
    """Return (client, error_response). Never logs or returns the key."""
    try:
        client = get_client()
    except Exception as exc:
        app.logger.error(
            "Speech service setup failed (%s)", type(exc).__name__
        )
        return None, (jsonify({
            "error": "The speech service is not configured correctly. "
                     "Check the server's environment."
        }), 500)
    if not getattr(client, "api_key", None):
        return None, (jsonify({
            "error": "The speech service key was not found. "
                     "Add GNANI_API_KEY to .env and restart Seva."
        }), 503)
    return client, None


def _finalize_device_record(record: dict) -> dict:
    """Attach a unique id and synthesized audio to a real detection."""
    if record.get("verdict") not in ("mine", "missing") or record.get("duplicate"):
        return record
    spoken = record.get("spoken") or ""
    language = record.get("lang") or record.get("language") or "en-IN"
    record["id"] = _new_alert_id()
    if spoken:
        audio_name = _synthesize_alert(spoken, language)
        record["audio"] = audio_name
        record["audio_url"] = f"/alerts/{audio_name}" if audio_name else None
        if not audio_name:
            record["tts_error"] = (
                f"Voice audio could not be generated in {language}. "
                "Check the Gnani key, credits and voice configuration."
            )
    else:
        record["audio"] = None
        record["audio_url"] = None
    return record


@app.get("/api/providers")
def api_providers():
    """Configuration visibility without exposing keys or making paid calls."""
    return jsonify({
        "gnani": {
            "configured": GnaniClient.is_configured(),
            "tts_model": TTS_MODEL,
        },
        "gemini": {
            "configured": gemini.is_configured(),
            "model": app.config["GEMINI_MODEL"],
        },
        "device_microphone": {
            "enabled": bool(app.config["DEVICE_MIC_ENABLED"]),
            "transmits_audio": bool(app.config["DEVICE_MIC_ENABLED"]),
        },
        "synthetic_demo": {
            "enabled": bool(app.config["SYNTHETIC_DEMO_ENABLED"]),
        },
        "hospital_data": {
            "approved": bool(app.config["HOSPITAL_DATA_APPROVED"]),
            "source_configured": bool(app.config["HOSPITAL_SOURCE_ID"]),
        },
    })


def _hospital_adapters():
    return hospital.build_adapters(
        bool(app.config["HOSPITAL_DATA_APPROVED"]),
        source_id=app.config["HOSPITAL_SOURCE_ID"],
        source_name=app.config["HOSPITAL_SOURCE_NAME"],
    )


@app.get("/api/hospital/sources")
def api_hospital_sources():
    return jsonify({
        "sources": [adapter.public() for adapter in _hospital_adapters()],
        "real_data_approved": bool(app.config["HOSPITAL_DATA_APPROVED"]),
        "persistence": "none",
        "network_connections": "none",
    })


@app.post("/api/hospital/announcement")
def api_hospital_announcement():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object expected."}), 400
    source_id = payload.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        return jsonify({"error": "source_id is required."}), 400
    adapter = hospital.find_adapter(_hospital_adapters(), source_id.strip())
    if adapter is None:
        return jsonify({"error": "Unknown hospital source."}), 404
    try:
        announcement = adapter.parse(payload)
    except hospital.HospitalError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify({
        "announcement": announcement.to_dict(),
        "stored": False,
        "provider_calls": 0,
    })


@app.get("/api/device/capabilities")
def api_device_capabilities():
    return jsonify({
        "device_microphone": bool(app.config["DEVICE_MIC_ENABLED"]),
        "server_microphone": bool(app.config["SERVER_MIC_ENABLED"]),
        "synthetic_demo": bool(app.config["SYNTHETIC_DEMO_ENABLED"]),
        "audio_provider": "gnani" if app.config["DEVICE_MIC_ENABLED"] else None,
        "transmits_audio": bool(app.config["DEVICE_MIC_ENABLED"]),
        "chunk_seconds": 6,
        "max_chunk_seconds": device_audio.MAX_CHUNK_SECONDS,
        "max_chunk_bytes": device_audio.MAX_CHUNK_BYTES,
        "secure_context_required": True,
        "background_listening": False,
    })


@app.post("/api/device/session")
def api_device_session():
    if not app.config["DEVICE_MIC_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    kind = body.get("kind", "hospital")
    raw_value = body.get("value", "")
    value = raw_value.strip() if isinstance(raw_value, str) else ""
    language = body.get("language", "en-IN")
    if not isinstance(kind, str):
        return jsonify({"error": "kind must be hospital, train or bus"}), 400
    if not isinstance(language, str):
        return jsonify({"error": "language must be text"}), 400

    if not _device_rate_ok(_client_ip()):
        return jsonify({"error": "too many listening requests; wait a minute"}), 429

    try:
        session = device_audio.create_session(
            kind, value, language, client_ip=_client_ip()
        )
    except device_audio.DeviceAudioError as exc:
        return jsonify({"error": str(exc)}), 400

    _client, error = _device_client()
    if error is not None:
        device_audio.stop_session(session.id, client_ip=_client_ip())
        return error

    return jsonify({
        "ok": True,
        "session_id": session.id,
        "expires_in": device_audio.SESSION_TTL_SECONDS,
        "kind": session.kind,
        "value": session.value,
        "language": session.language,
        "source": "device_microphone",
        "audio": {
            "provider": "gnani",
            "transmits_audio": True,
            "format": "wav",
            "sample_rate": 16000,
            "channels": 1,
            "bits_per_sample": 16,
            "chunk_seconds": 6,
            "max_chunk_seconds": device_audio.MAX_CHUNK_SECONDS,
            "max_chunk_bytes": device_audio.MAX_CHUNK_BYTES,
        },
    })


@app.post("/api/device/chunk")
def api_device_chunk():
    if not app.config["DEVICE_MIC_ENABLED"]:
        abort(404)
    session_id = (
        request.form.get("session_id")
        or request.headers.get("X-Seva-Session")
        or ""
    ).strip()
    if not session_id:
        return jsonify({"error": "missing session_id"}), 400

    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"error": "missing audio chunk"}), 400
    data = upload.read(device_audio.MAX_CHUNK_BYTES + 1)
    if len(data) > device_audio.MAX_CHUNK_BYTES:
        return jsonify({"error": "audio chunk is too large"}), 413

    if not _device_rate_ok(_client_ip()):
        return jsonify({"error": "too many audio chunks; wait a minute"}), 429

    session = device_audio.get_session(session_id, client_ip=_client_ip())
    if session is None:
        return jsonify({
            "error": "listening session expired",
            "code": "session_expired",
        }), 410
    if not device_audio.begin_chunk(session):
        return jsonify({
            "error": "listening session reached its limit; start again",
            "code": "session_limit",
        }), 429

    client, error = _device_client()
    if error is not None:
        return error

    try:
        result = device_audio.transcribe_chunk(client, data, DEVICE_TEMP_DIR)
    except device_audio.DeviceAudioError as exc:
        return jsonify({"error": str(exc)}), 400
    except GnaniError as exc:
        app.logger.warning("Device STT failed (%s)", type(exc).__name__)
        return jsonify({
            "error": "The speech service could not understand this audio chunk. "
                     "Seva will keep listening.",
            "code": "stt_error",
        }), 502
    except OSError:
        app.logger.error("Device audio could not be processed")
        return jsonify({
            "error": "The server could not process that audio chunk.",
            "code": "audio_error",
        }), 500

    transcript = (result.get("transcript") or "").strip()
    if not transcript:
        return jsonify({
            "ok": True,
            "heard": False,
            "verdict": "empty",
            "alert": None,
            "stats": device_audio.stats(session),
        })

    try:
        record = device_audio.process_transcript(session, transcript)
    except device_audio.DeviceAudioError as exc:
        return jsonify({"error": str(exc)}), 400

    alert = None
    if record.get("verdict") in ("mine", "missing") and not record.get("duplicate"):
        alert = _finalize_device_record(record)
        device_audio.set_last_alert(session, alert)

    return jsonify({
        "ok": True,
        "heard": True,
        "verdict": record.get("verdict"),
        "duplicate": bool(record.get("duplicate")),
        "pending": bool(record.get("pending")),
        "alert": alert,
        "stats": device_audio.stats(session),
    })


@app.get("/api/device/state")
def api_device_state():
    if not app.config["DEVICE_MIC_ENABLED"]:
        abort(404)
    session_id = (request.args.get("session_id") or "").strip()
    session = device_audio.get_session(session_id, client_ip=_client_ip())
    if session is None:
        return jsonify({
            "error": "listening session expired",
            "code": "session_expired",
        }), 410

    alert = device_audio.check_pending(session)
    if alert is not None:
        alert = _finalize_device_record(alert)
        device_audio.set_last_alert(session, alert)
    elif session.last_alert is not None:
        alert = session.last_alert

    return jsonify({
        "ok": True,
        "alert": alert,
        "stats": device_audio.stats(session),
    })


@app.post("/api/device/stop")
def api_device_stop():
    if not app.config["DEVICE_MIC_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    session_id = ""
    if isinstance(body, dict):
        session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        session_id = (request.form.get("session_id") or "").strip()
    device_audio.stop_session(session_id, client_ip=_client_ip())
    return jsonify({"ok": True})


@app.post("/api/watch/start")
def api_watch_start():
    if not app.config["SERVER_MIC_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    kind = body.get("kind", "hospital")
    raw_value = body.get("value", "")
    value = raw_value.strip() if isinstance(raw_value, str) else ""
    language = body.get("language", "en-IN")
    if kind not in WATCH_KINDS:
        return jsonify({"error": "kind must be hospital, train or bus"}), 400
    try:
        trigger_db = float(body.get("trigger_db", 15))
    except (TypeError, ValueError):
        return jsonify({"error": "trigger_db must be a number"}), 400
    if not math.isfinite(trigger_db) or not 0 <= trigger_db <= 60:
        return jsonify({"error": "trigger_db must be between 0 and 60 dB"}), 400
    if not value:
        return jsonify({"error": "nothing to watch for"}), 400
    if not _valid_watch_value(kind, value):
        return jsonify({"error": _watch_value_error(kind)}), 400
    if not isinstance(language, str) or not language_supported(language):
        return jsonify({
            "error": f"No voice is configured for {language!r}. "
                     f"Choose one of: {', '.join(sorted(supported_languages()))}."
        }), 400
    try:
        key_found = bool(get_client().api_key)
    except Exception as exc:
        app.logger.error(
            "Speech service setup failed (%s)", type(exc).__name__
        )
        return jsonify({
            "error": "The speech service is not configured correctly. Check the server's environment."
        }), 500
    if not key_found:
        return jsonify({
            "error": "The speech service key was not found. Add GNANI_API_KEY to .env and restart Seva."
        }), 500

    with WATCH_LOCK:
        if WATCH["active"]:
            return jsonify({"error": "already listening"}), 409
        WATCH.update({
            "active": True,
            "kind": kind,
            "value": value,
            "language": language,
            "alerts": [],
            "heard": 0,
            "error": None,
            "mic_device": None,
            "input_level": 0.0,
            "input_db": None,
            "background_level": None,
            "gate_threshold_db": trigger_db,
            "gate_state": None,
            "gate_open": False,
            "clips": 0,
            "stt_calls": 0,
            "last_clip_seconds": None,
            "last_stt_language": None,
            "last_stt_error": None,
            "last_stt_empty": None,
            "last_heard_text": None,
            "last_match_verdict": None,
            "last_match_token": None,
            "last_match_room": None,
            "key_found": key_found,
        })
        _RECENT_ALERTS.clear()

    thread = threading.Thread(
        target=_watch_loop,
        args=(kind, value, language, trigger_db, 40),
        daemon=True,
    )
    WATCH["thread"] = thread
    thread.start()
    return jsonify({"ok": True, "kind": kind, "value": value})


@app.get("/api/watch/capabilities")
def api_watch_capabilities():
    return jsonify({
        "server_microphone": app.config["SERVER_MIC_ENABLED"],
        "device_microphone": app.config["DEVICE_MIC_ENABLED"],
        "synthetic_demo": app.config["SYNTHETIC_DEMO_ENABLED"],
        "device_audio_provider": (
            "gnani" if app.config["DEVICE_MIC_ENABLED"] else None
        ),
        "device_transmits_audio": bool(app.config["DEVICE_MIC_ENABLED"]),
    })


@app.get("/api/watch/state")
def api_watch_state():
    if not app.config["SERVER_MIC_ENABLED"]:
        abort(404)
    with WATCH_LOCK:
        state = {k: v for k, v in WATCH.items() if k != "thread"}
        if os.environ.get("SEVA_DEBUG") == "1":
            state["debug"] = True
        else:
            for key in (
                "mic_device", "input_level", "input_db", "background_level",
                "gate_threshold_db", "gate_state", "gate_open", "clips",
                "stt_calls", "last_clip_seconds", "last_stt_language",
                "last_stt_error", "last_stt_empty", "last_heard_text",
                "last_match_verdict", "last_match_token", "last_match_room",
                "key_found",
            ):
                state.pop(key, None)
            state["debug"] = False
        return jsonify(state)


@app.post("/api/watch/stop")
def api_watch_stop():
    if not app.config["SERVER_MIC_ENABLED"]:
        abort(404)
    with WATCH_LOCK:
        WATCH["active"] = False
    return jsonify({"ok": True})


@app.get("/api/mic/status")
def api_mic_status():
    """Verify if server microphone is available without browser getUserMedia."""
    if not app.config["SERVER_MIC_ENABLED"]:
        abort(404)
    try:
        _input_device_name()
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Microphone not detected on the server."})


@app.post("/api/watch/demo")
def api_watch_demo():
    """Run a deterministic sample through the matcher without microphone or providers."""
    if not app.config["SYNTHETIC_DEMO_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    kind = body.get("kind", "hospital")
    raw_value = body.get("value", "")
    value = " ".join(raw_value.split()) if isinstance(raw_value, str) else ""
    language = body.get("language", "en-IN")
    if kind not in WATCH_KINDS:
        return jsonify({"error": "kind must be hospital, train or bus"}), 400
    if not value:
        return jsonify({"error": "need a watch value"}), 400
    if not _valid_watch_value(kind, value):
        return jsonify({"error": _watch_value_error(kind)}), 400
    if kind == "bus" and not re.fullmatch(r"[\w .'-]+", value):
        return jsonify({"error": "destination contains unsupported characters"}), 400
    if not isinstance(language, str) or not language_supported(language):
        return jsonify({
            "error": f"No voice is configured for {language!r}. "
                     f"Choose one of: {', '.join(sorted(supported_languages()))}."
        }), 400

    if kind in ("hospital", "token"):
        transcript = f"Token number {value}, room 203. Synthetic demonstration only."
    elif kind == "train":
        transcript = f"Train number {value} arriving on platform 4. Synthetic demonstration only."
    else:
        transcript = f"Bus to {value} is arriving on platform 4. Synthetic demonstration only."

    from app.matcher import match_announcement
    match = match_announcement(kind, transcript, value)
    if match.get("verdict") != "mine":
        app.logger.error("Synthetic announcement did not match its selected value")
        return jsonify({"error": "synthetic demonstration could not be matched"}), 500

    token = match.get("token") or (value if kind in ("hospital", "token") else None)
    room = match.get("room")
    slots = {
        "train_no": match.get("train_no"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "token": token,
        "room": room,
    }
    spoken, display_room, spoken_language = _compose_alert(
        "mine", kind, token, room, language, slots=slots
    )
    return jsonify({
        "schemaVersion": 1,
        "at": time.strftime("%H:%M:%S"),
        "verdict": "mine",
        "certain": alert_is_certain(match, kind),
        "token": token,
        "room": display_room or None,
        "train_no": match.get("train_no"),
        "train_name": match.get("train_name"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "specialist": match.get("specialist"),
        "spoken": spoken,
        "lang": spoken_language,
        "requested_language": language,
        "audio": None,
        "audio_url": None,
        "synthetic": True,
        "transcript": transcript,
        "duplicate": False,
    })


@app.post("/api/watch/simulate")
def api_watch_simulate():
    """Feed a typed announcement through the real pipeline. No microphone."""
    if not app.config["SIMULATE_ENABLED"]:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    kind = body.get("kind", "hospital")
    raw_value = body.get("value", "")
    raw_text = body.get("text", "")
    value = raw_value.strip() if isinstance(raw_value, str) else ""
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    language = body.get("language", "en-IN")
    speak = bool(body.get("speak", False))
    if kind not in WATCH_KINDS:
        return jsonify({"error": "kind must be hospital, train or bus"}), 400
    if not value or not text:
        return jsonify({"error": "need both a watch value and a transcript"}), 400
    if len(text) > MAX_TRANSCRIPT_CHARS:
        return jsonify({"error": "transcript is too long"}), 400
    if not _valid_watch_value(kind, value):
        return jsonify({"error": _watch_value_error(kind)}), 400
    if not isinstance(language, str) or not language_supported(language):
        return jsonify({
            "error": f"No voice is configured for {language!r}. "
                     f"Choose one of: {', '.join(sorted(supported_languages()))}."
        }), 400

    from app.matcher import match_announcement
    match = match_announcement(kind, text, value)
    verdict = match.get("verdict", "none")
    duplicate = (
        verdict in ("mine", "missing")
        and not _claim_alert(kind, value, match)
    )

    with WATCH_LOCK:
        WATCH["heard"] += 1
        WATCH["last_heard_text"] = text

    record: dict = {
        "schemaVersion": 1,
        "at": time.strftime("%H:%M:%S"),
        "verdict": verdict,
        "certain": (alert_is_certain(match, kind)
                    if verdict in ("mine", "missing") else verdict == "other"),
        "token": match.get("token"),
        "room": match.get("room"),
        "train_no": match.get("train_no"),
        "train_name": match.get("train_name"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "specialist": match.get("specialist"),
        "transcript": text,
        "duplicate": duplicate,
    }

    if duplicate:
        return jsonify(record)

    if verdict in ("mine", "missing"):
        slots = {
            "train_no": match.get("train_no"),
            "platform": match.get("platform"),
            "destination": match.get("destination"),
            "token": match.get("token"),
            "room": match.get("room"),
        }
        spoken, display_room, spoken_language = _compose_alert(
            verdict, kind, record["token"], record["room"], language, slots=slots
        )
        record["spoken"] = spoken
        record["room"] = display_room
        record["lang"] = spoken_language
        record["requested_language"] = language
        if speak:
            audio_name = _synthesize_alert(spoken, spoken_language)
            record["audio"] = audio_name
            record["audio_url"] = f"/alerts/{audio_name}" if audio_name else None
            if spoken and not audio_name:
                record["tts_error"] = (
                    f"Voice audio could not be generated in {spoken_language}. "
                    "Check the Gnani key, credits and voice configuration."
                )
    elif verdict == "other":
        record["token"] = match.get("token")
        record["room"] = match.get("room")

    with WATCH_LOCK:
        if verdict != "none":
            record["id"] = _new_alert_id()
            WATCH["alerts"].append(record)

    return jsonify(record)


@app.get("/api/languages")
def api_languages():
    from app.alert import PHRASES
    def _spoken(code):
        return code.split("-")[0] in PHRASES
    out = [
        {"code": "te-IN", "name": "తెలుగు", "english": "Telugu"},
        {"code": "hi-IN", "name": "हिंदी", "english": "Hindi"},
        {"code": "en-IN", "name": "English", "english": "English"},
        {"code": "ta-IN", "name": "தமிழ்", "english": "Tamil"},
        {"code": "kn-IN", "name": "ಕನ್ನಡ", "english": "Kannada"},
        {"code": "ml-IN", "name": "മലയാളം", "english": "Malayalam"},
        {"code": "mr-IN", "name": "मराठी", "english": "Marathi"},
        {"code": "bn-IN", "name": "বাংলা", "english": "Bengali"},
        {"code": "gu-IN", "name": "ગુજરાતી", "english": "Gujarati"},
        {"code": "pa-IN", "name": "ਪੰਜਾਬੀ", "english": "Punjabi"},
    ]
    for row in out:
        # True  = alerts are written in this language
        # False = alerts fall back to English, and the screen must say so
        row["alert_text"] = _spoken(row["code"])
    return jsonify(out)


@app.get("/api/usage")
def api_usage():
    if not app.config["USAGE_API_ENABLED"]:
        abort(404)
    return jsonify(get_client().usage_summary())


def main() -> int:
    parser = argparse.ArgumentParser(description="Seva - hospital help desk and announcement ear")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--voice", default="Chetana")
    args = parser.parse_args()

    app.config["MODEL"] = args.model
    app.config["VOICE"] = args.voice

    try:
        get_client()
    except GnaniError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\n  Seva - {len(book)} entries loaded")
    print(f"  model {args.model}   voice {args.voice}")
    print(f"\n  open http://127.0.0.1:{args.port} in your browser\n")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
