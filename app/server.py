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
import os
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from app import gemini  # noqa: E402
from app.brain import BrainError  # noqa: E402
from app.gemini import GeminiError, ask as gemini_ask  # noqa: E402
from app.schemes import SchemeBook  # noqa: E402
from gnani import GnaniClient, GnaniError  # noqa: E402

AUDIO_DIR = ROOT / "samples" / "web"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

PROJECT = Path(__file__).resolve().parent.parent

app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))
app.config["MODEL"] = "gemma4"
app.config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", gemini.DEFAULT_MODEL)

ASK_RATE_WINDOW_SECONDS = 60
ASK_RATE_LIMIT = 10
ASK_RATE_LOCK = threading.Lock()
ASK_RATE: dict[str, deque[float]] = defaultdict(deque)

# --- serve the 17 mobile screens from this same server (no CORS, one origin) ---
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/app/")
def seva_home():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/app/<path:name>")
def seva_page(name: str):
    return send_from_directory(WEB_DIR, name)

app.config["VOICE"] = "Chetana"   # v3.0 healthcare voice

book = SchemeBook()
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


def _localized_fallback(language: str, has_local_match: bool) -> str:
    code = (language or "en").split("-", 1)[0].lower()
    if has_local_match:
        messages = {
            "te": "Gemini అందుబాటులో లేదు. స్థానిక ఆసుపత్రి సమాచారం: ",
            "hi": "Gemini अभी उपलब्ध नहीं है। स्थानीय अस्पताल की जानकारी: ",
            "en": "Gemini is unavailable. Relevant local hospital information: ",
        }
    else:
        messages = {
            "te": "క్షమించండి, Nova ప్రస్తుతం సమాధానం ఇవ్వలేకపోతోంది. అత్యవసరమైతే ఆసుపత్రి సిబ్బందిని సంప్రదించండి లేదా 108కు కాల్ చేయండి.",
            "hi": "क्षमा करें, Nova अभी उत्तर नहीं दे पा रही है। आपातकाल में अस्पताल के कर्मचारियों से कहें या 108 पर कॉल करें।",
            "en": "Sorry, Nova cannot answer right now. For an emergency, alert hospital staff or call 108.",
        }
    return messages.get(code, messages["en"])


def _local_fallback(matches: list[dict], language: str) -> str:
    prefix = _localized_fallback(language, True)
    if not matches:
        return _localized_fallback(language, False)
    scheme = matches[0]
    if (language or "en").split("-", 1)[0].lower() == "te":
        fact = f"{scheme['name_te']}. {scheme['benefit_te']} {scheme['how_te']}"
    else:
        # The existing hospital JSON carries English keywords but Telugu facts.
        # Do not fabricate a translation if Gemini is unavailable.
        return prefix + "please ask hospital staff to confirm the listed information."
    return prefix + fact


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


def speak_answer(reply: str, language: str) -> str | None:
    """Attempt voice synthesis without breaking the text-answer API."""
    try:
        audio = AUDIO_DIR / f"a-{uuid.uuid4().hex[:8]}.wav"
        get_client().speak(reply, language=language, voice=app.config["VOICE"],
                           sample_rate=16000, out_path=audio)
        return f"/audio/{audio.name}"
    except (GnaniError, OSError):
        return None


def answer(question: str, language: str = "te-IN") -> dict:
    """Answer with Gemini first and use matching hospital facts on failure."""
    timings: dict[str, int] = {}

    started = time.time()
    matches = book.search(question)
    context = book.as_context(matches)
    timings["lookup_ms"] = int((time.time() - started) * 1000)

    started = time.time()
    source = "gemini"
    fallback = False
    try:
        reply = gemini_ask(
            question, language, context,
            model=app.config["GEMINI_MODEL"], timeout=gemini.REQUEST_TIMEOUT_SECONDS,
        )
    except Exception:
        # Provider failures must not turn an answer into a traceback or 5xx.
        reply = _local_fallback(matches, language)
        source = "hospital_faq" if matches else "fallback"
        fallback = True
    timings["think_ms"] = int((time.time() - started) * 1000)

    started = time.time()
    audio_url = speak_answer(reply, language)
    timings["speak_ms"] = int((time.time() - started) * 1000)

    return {
        "question": question,
        "answer": reply,
        "audio": audio_url,
        "schemes": [{"name": s["name_te"], "url": s["url"]} for s in matches],
        "timings": timings,
        "source": source,
        "fallback": fallback,
    }


@app.get("/")
def index():
    from flask import render_template
    return render_template("index.html", scheme_count=len(book),
                           last_checked=book.last_checked)


@app.get("/audio/<path:name>")
def audio(name: str):
    return send_from_directory(AUDIO_DIR, name)


@app.post("/api/ask")
def api_ask():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid request"}), 400
    question = body.get("question", "")
    if not isinstance(question, str):
        return jsonify({"error": "invalid question"}), 400
    question = question.strip()
    language = body.get("language", "en-IN")
    if not question:
        return jsonify({"error": "empty question"}), 400
    if len(question) > 500:
        return jsonify({"error": "question is too long"}), 400
    if not isinstance(language, str) or len(language) > 16:
        language = "en-IN"
    if not _allow_ask(request.remote_addr or "unknown"):
        return jsonify({"error": "too many questions; please wait a minute"}), 429
    try:
        return jsonify(answer(question, language))
    except Exception:
        # Keep internal details out of responses, even if response formatting fails.
        return jsonify({
            "question": question,
            "answer": _localized_fallback(language, False),
            "audio": None,
            "schemes": [],
            "timings": {},
            "source": "fallback",
            "fallback": True,
        })


@app.post("/api/listen")
def api_listen():
    body = request.json or {}
    seconds = float(body.get("seconds", 6))
    language = body.get("language", "te-IN")
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
    except (GnaniError, BrainError) as exc:
        return jsonify({"error": str(exc)}), 502




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
    "last_heard_text": None,
}
WATCH_LOCK = threading.Lock()


def _synthesize_alert(alert_text: str, language: str) -> str | None:
    """Generate Timbre TTS audio for the alert and return audio filename."""
    if not alert_text:
        return None
    try:
        audio_dir = ROOT / "samples" / "alerts"
        audio_dir.mkdir(parents=True, exist_ok=True)
        out_path = audio_dir / f"alert-{int(time.time())}-{uuid.uuid4().hex[:6]}.wav"
        get_client().speak(alert_text, language=language, voice=app.config.get("VOICE", "Chetana"), out_path=out_path)
        return out_path.name
    except Exception:
        return None


def _compose_alert(verdict: str, kind: str, token: str | None, room: str | None, language: str) -> tuple[str, str]:
    """Returns (spoken_text, screen_room_text) for the announcement."""
    lang_code = language.split("-")[0].lower() if language else "en"
    if lang_code not in ("te", "hi", "en"):
        lang_code = "en"

    if kind in ("hospital", "token"):
        if verdict == "mine":
            if lang_code == "te":
                spoken = f"మీ నంబర్ వచ్చింది. {token}. గది నంబర్ {room}. సామాను తీసుకొని ఇప్పుడే బయలుదేరండి."
            elif lang_code == "hi":
                spoken = f"आपका नंबर आ गया है. {token}. कमरा नंबर {room}. अपना सामान लेकर अभी निकलिए."
            else:
                spoken = f"Your number has been called. {token}. Room number {room}. Take your things and leave now."
            display_room = str(room) if room else ""
        elif verdict == "missing":
            if lang_code == "te":
                spoken = f"<slow>మీ నంబర్ వచ్చింది. {token}. గది నంబర్ వినపడలేదు. డిస్‌ప్లే బోర్డు చూడండి."
                display_room = "గది: వినపడలేదు. డిస్‌ప్లే బోర్డు చూడండి"
            elif lang_code == "hi":
                spoken = f"<slow>आपका नंबर आ गया है. {token}. कमरा नंबर सुनाई नहीं दिया. कृपया डिस्प्ले बोर्ड देखें."
                display_room = "कमरा: सुनाई नहीं दिया, डिस्प्ले बोर्ड देखें"
            else:
                spoken = f"<slow>Your number has been called. {token}. The room number was not heard. Please check the display board."
                display_room = "Room: not heard, check the display board"
        else:
            spoken = ""
            display_room = str(room) if room else ""
        return spoken, display_room

    return "", str(room or "")


def _process_transcript(text: str, kind: str, value: str, language: str, pending_missing: dict) -> dict | None:
    """
    Process transcript through matcher.
    Handles 16s repeat window for missing room.
    Returns alert dict or None.
    """
    from app.matcher import match_announcement
    match = match_announcement(kind, text, value)
    verdict = match.get("verdict")

    if verdict == "none":
        return None

    if verdict == "other":
        return {
            "schemaVersion": 1,
            "verdict": "other",
            "token": match.get("token"),
            "room": match.get("room"),
            "train_no": match.get("train_no"),
            "platform": match.get("platform"),
            "destination": match.get("destination"),
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
        # Token matched and room was heard
        token = match.get("token") or str(value)
        room = match.get("room")
        pending_missing["active"] = False
        spoken, display_room = _compose_alert("mine", kind, token, room, language)
        audio_name = _synthesize_alert(spoken, language)
        return {
            "schemaVersion": 1,
            "at": time.strftime("%H:%M:%S"),
            "verdict": "mine",
            "certain": True,
            "token": token,
            "room": display_room,
            "train_no": match.get("train_no"),
            "platform": match.get("platform") or display_room,
            "destination": match.get("destination"),
            "spoken": spoken,
            "lang": language,
            "audio": audio_name,
            "transcript": text,
        }

    return None


def _watch_loop(kind: str, value: str, language: str, trigger_db: float,
                max_calls: int) -> None:
    """Background microphone listener. Writes into WATCH for the UI to poll."""
    import numpy as np
    from app.gate import Gate, rms
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
        input_info = sd.query_devices(kind="input")
        mic_name = input_info.get("name", "Default Microphone")
        with WATCH_LOCK:
            WATCH["mic_device"] = mic_name
    except Exception:
        with WATCH_LOCK:
            WATCH["error"] = "Microphone not detected. Please check your microphone connection."
            WATCH["active"] = False
        return

    g = Gate(trigger_db=trigger_db)
    buffer: list = []
    pre: list = []
    clips = 0
    tmp_dir = ROOT / "samples" / "clips"
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
                with WATCH_LOCK:
                    WATCH["input_level"] = round(lvl, 4)

                # 16-second timeout check for pending missing room
                if pending_missing.get("active") and time.time() - pending_missing["started_at"] >= 16.0:
                    pending_missing["active"] = False
                    tok = pending_missing["token"]
                    spoken, display_room = _compose_alert("missing", kind, tok, None, language)
                    audio_name = _synthesize_alert(spoken, language)
                    with WATCH_LOCK:
                        WATCH["alerts"].append({
                            "schemaVersion": 1,
                            "verdict": "missing",
                            "token": tok,
                            "room": None,
                            "train_no": None,
                            "platform": None,
                            "destination": None,
                            "certain": False,
                            "spoken": spoken,
                            "lang": language,
                            "audio": audio_name,
                        })

                state = g.push(frame)
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

                        try:
                            client = get_client()
                            text = client.transcribe(clip_path, language_code=language)
                            calls += 1
                            try:
                                clip_path.unlink()
                            except OSError:
                                pass
                            if text and text.strip():
                                with WATCH_LOCK:
                                    WATCH["heard"] += 1
                                    WATCH["last_heard_text"] = text.strip()

                                alert_record = _process_transcript(
                                    text, kind, value, language, pending_missing
                                )
                                if alert_record:
                                    with WATCH_LOCK:
                                        WATCH["alerts"].append(alert_record)
                        except GnaniError as gerr:
                            msg = str(gerr)
                            if "401" in msg or "403" in msg or "FORBIDDEN" in msg:
                                clean_err = "The speech service rejected the API key or credits are finished."
                            elif "timed out" in msg.lower() or "connection" in msg.lower():
                                clean_err = "Could not reach the speech service. Check your internet connection."
                            else:
                                clean_err = f"Speech service error: {gerr}"
                            with WATCH_LOCK:
                                WATCH["error"] = clean_err
                                WATCH["active"] = False
                            break
                        except Exception as exc:
                            with WATCH_LOCK:
                                WATCH["error"] = f"Transcription error: {exc}"
                                WATCH["active"] = False
                            break

                    buffer = []
                    if calls >= max_calls:
                        break
    except Exception as exc:
        with WATCH_LOCK:
            WATCH["error"] = f"Microphone error: {exc}"
            WATCH["active"] = False
    finally:
        with WATCH_LOCK:
            WATCH["active"] = False


@app.post("/api/watch/start")
def api_watch_start():
    body = request.json or {}
    kind = body.get("kind", "hospital")
    value = (body.get("value") or "").strip()
    language = body.get("language", "en-IN")
    trigger_db = float(body.get("trigger_db", 15))
    if not value:
        return jsonify({"error": "nothing to watch for"}), 400
    if kind in ("hospital", "token", "train") and (not value.isascii() or not value.isdigit()):
        return jsonify({"error": f"{kind} values must contain digits only"}), 400

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
            "last_heard_text": None,
        })

    thread = threading.Thread(
        target=_watch_loop,
        args=(kind, value, language, trigger_db, 40),
        daemon=True,
    )
    WATCH["thread"] = thread
    thread.start()
    return jsonify({"ok": True, "kind": kind, "value": value})


@app.get("/api/watch/state")
def api_watch_state():
    with WATCH_LOCK:
        state = {k: v for k, v in WATCH.items() if k != "thread"}
        if os.environ.get("SEVA_DEBUG") == "1":
            state["debug"] = True
        else:
            state.pop("mic_device", None)
            state.pop("input_level", None)
            state.pop("last_heard_text", None)
            state["debug"] = False
        return jsonify(state)


@app.post("/api/watch/stop")
def api_watch_stop():
    with WATCH_LOCK:
        WATCH["active"] = False
    return jsonify({"ok": True})


@app.get("/api/mic/status")
def api_mic_status():
    """Verify if server microphone is available without browser getUserMedia."""
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "error": "Microphone not detected on the server."})


@app.post("/api/watch/simulate")
def api_watch_simulate():
    """Feed a typed announcement through the real pipeline. No microphone."""
    body = request.json or {}
    kind = body.get("kind", "hospital")
    value = (body.get("value") or "").strip()
    text = (body.get("text") or "").strip()
    language = body.get("language", "en-IN")
    speak = bool(body.get("speak", False))
    if not value or not text:
        return jsonify({"error": "need both a watch value and a transcript"}), 400
    if kind in ("hospital", "token", "train") and (not value.isascii() or not value.isdigit()):
        return jsonify({"error": f"{kind} values must contain digits only"}), 400

    from app.matcher import match_announcement
    match = match_announcement(kind, text, value)
    verdict = match.get("verdict", "none")

    with WATCH_LOCK:
        WATCH["heard"] += 1
        WATCH["last_heard_text"] = text

    record: dict = {
        "schemaVersion": 1,
        "at": time.strftime("%H:%M:%S"),
        "verdict": verdict,
        "certain": verdict in ("mine", "other"),
        "token": match.get("token"),
        "room": match.get("room"),
        "train_no": match.get("train_no"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "transcript": text,
    }

    if verdict in ("mine", "missing"):
        spoken, display_room = _compose_alert(verdict, kind, record["token"], record["room"], language)
        record["spoken"] = spoken
        record["room"] = display_room
        record["lang"] = language
        if speak:
            record["audio"] = _synthesize_alert(spoken, language)
    elif verdict == "other":
        record["token"] = match.get("token")
        record["room"] = match.get("room")

    with WATCH_LOCK:
        if verdict != "none":
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
