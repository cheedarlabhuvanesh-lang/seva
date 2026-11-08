"""Device-side microphone support for Seva.

The Flask process on a cloud host cannot reach a visitor's phone microphone.
This module owns the server half of the correct architecture:

    phone microphone -> browser PCM capture -> bounded WAV chunk
        -> /api/device/chunk -> Gnani Prisma STT
        -> app.matcher -> alert verdict -> temporary file deleted

Design rules
------------
* A chunk is processed in memory and in a short-lived temporary file. It is
  deleted in ``finally``; no raw announcement audio is kept.
* Sessions are short-lived and in-memory. They are owned by one process, which
  is why the production Gunicorn configuration keeps ``workers = 1``.
* The matcher is the same one used by the server-microphone and synthetic
  flows, so exact-number behaviour (47 never matches 847) is shared.
* Duplicate announcements are suppressed per session, not globally.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.alert import alert_is_certain, build_alert, language_supported
from app.extract import token_key
from app.gemini import normalize_language
from app.matcher import match_announcement, normalize_text
from app.watch import KINDS as WATCH_KINDS

#: A session lives ten minutes without a chunk. A waiting user may spend longer
#: than that in a hall, so the client creates a new session when this expires.
SESSION_TTL_SECONDS = 600
#: Hard ceilings. They protect both memory and the billable STT quota.
MAX_ACTIVE_SESSIONS = 200
MAX_CHUNKS_PER_SESSION = 60
MAX_CHUNK_BYTES = 1_000_000
MAX_CHUNK_SECONDS = 20.0
MIN_CHUNK_SECONDS = 0.2
MAX_TRANSCRIPT_CHARS = 2000
MAX_WATCH_VALUE_CHARS = 120
#: A repeated transcript inside this window is treated as the same announcement.
DUPLICATE_WINDOW_SECONDS = 30.0
#: Wait for a room number to arrive in a following sentence before reporting it
#: as missing, matching the server-microphone flow.
PENDING_MISSING_SECONDS = 16.0
ALERT_REPEAT_WINDOW_SECONDS = 20.0

VALID_KINDS = ("hospital", "token", "train", "bus")
assert set(WATCH_KINDS) <= set(VALID_KINDS)


class DeviceAudioError(ValueError):
    """A client-side input problem that is safe to return as HTTP 400."""


@dataclass
class DeviceSession:
    id: str
    kind: str
    value: str
    language: str
    client_ip: str
    created_at: float
    last_seen: float
    chunks: int = 0
    heard: int = 0
    duplicate_chunks: int = 0
    last_verdict: str | None = None
    last_token: str | None = None
    last_room: str | None = None
    last_heard_text: str | None = None
    pending_missing: dict = field(default_factory=dict)
    recent_transcripts: deque = field(default_factory=deque)
    recent_alerts: deque = field(default_factory=deque)
    last_alert: dict | None = None

    def touch(self) -> None:
        self.last_seen = time.time()

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.last_seen > SESSION_TTL_SECONDS


_LOCK = None
_SESSIONS: dict[str, DeviceSession] = {}
_DIAGNOSTIC_HMAC_KEY = secrets.token_bytes(32)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionLookup:
    session: DeviceSession | None
    outcome: Literal["found", "missing", "expired", "owner_mismatch"]


def _fingerprint(value: str, purpose: bytes) -> str:
    digest = hmac.new(
        _DIAGNOSTIC_HMAC_KEY,
        purpose + b"\0" + str(value or "").encode("utf-8", errors="replace"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:16]


def _log_session_event(
    event: str,
    outcome: str,
    *,
    route: str,
    session_id: str = "",
    client_ip: str = "",
    owner_ip: str = "",
) -> None:
    session_fp = _fingerprint(session_id, b"session") if session_id else "-"
    client_fp = _fingerprint(client_ip, b"client-ip") if client_ip else "-"
    owner_fp = _fingerprint(owner_ip, b"client-ip") if owner_ip else "-"
    _LOGGER.info(
        "device_session event=%s route=%s outcome=%s "
        "session_fp=%s client_fp=%s owner_fp=%s",
        event,
        route,
        outcome,
        session_fp,
        client_fp,
        owner_fp,
    )


def _lock():
    """Create the lock lazily so importing this module stays side-effect free."""
    global _LOCK
    if _LOCK is None:
        import threading

        _LOCK = threading.Lock()
    return _LOCK


def _clean_text(value: object, limit: int = MAX_WATCH_VALUE_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def validate_watch(kind: str, value: str) -> None:
    """Raise DeviceAudioError when a watch cannot ever match."""
    if kind not in VALID_KINDS:
        raise DeviceAudioError("kind must be hospital, train or bus")
    if not value:
        raise DeviceAudioError("nothing to watch for")
    if len(value) > MAX_WATCH_VALUE_CHARS:
        raise DeviceAudioError("watch value is too long")
    if kind == "train":
        if not (value.isascii() and value.isdigit() and len(value) == 5):
            raise DeviceAudioError("train values must be exactly five digits")
        return
    if kind in ("hospital", "token"):
        if token_key(value) is None:
            raise DeviceAudioError(
                "token values must be digits, optionally with a block letter"
            )
        return
    if not re.fullmatch(r"[\w .'-]+", value):
        raise DeviceAudioError("destination contains unsupported characters")


def create_session(
    kind: str,
    value: str,
    language: str,
    *,
    client_ip: str,
) -> DeviceSession:
    kind = (kind or "hospital").strip().lower()
    value = _clean_text(value)
    selected_language = normalize_language(language)
    if selected_language is None or not language_supported(selected_language):
        raise DeviceAudioError(
            "No voice is configured for that language. "
            "Choose one of Seva's supported languages."
        )
    validate_watch(kind, value)

    now = time.time()
    with _lock():
        expired = [sid for sid, session in _SESSIONS.items() if session.expired(now)]
        for sid in expired:
            removed = _SESSIONS[sid]
            _log_session_event(
                "session_expired",
                "expired",
                route="session_create",
                session_id=removed.id,
                owner_ip=removed.client_ip,
            )
            del _SESSIONS[sid]
        while len(_SESSIONS) >= MAX_ACTIVE_SESSIONS:
            oldest = min(_SESSIONS.values(), key=lambda item: item.last_seen)
            _log_session_event(
                "session_evicted",
                "capacity",
                route="session_create",
                session_id=oldest.id,
                owner_ip=oldest.client_ip,
            )
            del _SESSIONS[oldest.id]

        session = DeviceSession(
            id=secrets.token_urlsafe(24),
            kind=kind,
            value=value,
            language=selected_language,
            client_ip=str(client_ip or ""),
            created_at=now,
            last_seen=now,
        )
        _SESSIONS[session.id] = session
    _log_session_event(
        "session_created",
        "created",
        route="session_create",
        session_id=session.id,
        client_ip=session.client_ip,
    )
    return session


def lookup_session(
    session_id: str,
    *,
    client_ip: str,
    route: Literal["state", "chunk", "lookup"] = "lookup",
) -> SessionLookup:
    """Find a live session and record a privacy-safe diagnostic outcome."""
    if not session_id:
        _log_session_event(
            "session_lookup",
            "missing",
            route=route,
            client_ip=client_ip,
        )
        return SessionLookup(None, "missing")

    now = time.time()
    with _lock():
        session = _SESSIONS.get(str(session_id))
        if session is None:
            result = SessionLookup(None, "missing")
            owner_ip = ""
        elif session.expired(now):
            del _SESSIONS[session.id]
            _log_session_event(
                "session_expired",
                "expired",
                route=route,
                session_id=session.id,
                client_ip=client_ip,
                owner_ip=session.client_ip,
            )
            result = SessionLookup(None, "expired")
            owner_ip = session.client_ip
        elif session.client_ip and str(client_ip or "") != session.client_ip:
            result = SessionLookup(None, "owner_mismatch")
            owner_ip = session.client_ip
        else:
            session.touch()
            result = SessionLookup(session, "found")
            owner_ip = session.client_ip

    _log_session_event(
        "session_lookup",
        result.outcome,
        route=route,
        session_id=session_id,
        client_ip=client_ip,
        owner_ip=owner_ip,
    )
    return result


def get_session(session_id: str, *, client_ip: str) -> DeviceSession | None:
    """Return a live session owned by this client, or None."""
    return lookup_session(session_id, client_ip=client_ip).session


def stop_session(session_id: str, *, client_ip: str) -> bool:
    if not session_id:
        _log_session_event(
            "session_stop",
            "missing",
            route="stop",
            client_ip=client_ip,
        )
        return False
    with _lock():
        session = _SESSIONS.get(str(session_id))
        if session is None:
            outcome = "missing"
            owner_ip = ""
        elif session.client_ip and str(client_ip or "") != session.client_ip:
            outcome = "owner_mismatch"
            owner_ip = session.client_ip
        else:
            del _SESSIONS[session.id]
            outcome = "stopped"
            owner_ip = session.client_ip

    _log_session_event(
        "session_stop",
        outcome,
        route="stop",
        session_id=session_id,
        client_ip=client_ip,
        owner_ip=owner_ip,
    )
    return outcome == "stopped"


def clear_sessions() -> None:
    """Test helper: drop every in-memory session."""
    with _lock():
        _SESSIONS.clear()


def _normalised_transcript(text: str) -> str:
    try:
        return " ".join(normalize_text(text).split())
    except Exception:
        return " ".join(str(text or "").lower().split())


def _claim_alert(session: DeviceSession, match: dict) -> bool:
    """Claim a semantic alert once per short repeat window in this session."""
    verdict = match.get("verdict")
    if session.kind in ("hospital", "token"):
        details = (match.get("token"), match.get("room"))
    elif session.kind == "train":
        details = (match.get("train_no"), match.get("platform"))
    else:
        details = (session.value, match.get("platform"))
    signature = (session.kind, verdict, *details)
    now = time.time()
    recent = session.recent_alerts
    while recent and now - recent[0][1] >= ALERT_REPEAT_WINDOW_SECONDS:
        recent.popleft()
    if any(existing == signature for existing, _timestamp in recent):
        return False
    recent.append((signature, now))
    return True


def _record(session: DeviceSession, match: dict, text: str, verdict: str) -> dict:
    token = match.get("token")
    room = match.get("room")
    slots = {
        "token": token,
        "room": room,
        "train_no": match.get("train_no"),
        "train_name": match.get("train_name"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "specialist": match.get("specialist"),
    }
    certain = (
        alert_is_certain(slots, session.kind)
        if verdict in ("mine", "missing")
        else verdict == "other"
    )
    return {
        "schemaVersion": 1,
        "at": time.strftime("%H:%M:%S"),
        "verdict": verdict,
        "certain": certain,
        "token": token,
        "room": room,
        "train_no": match.get("train_no"),
        "train_name": match.get("train_name"),
        "platform": match.get("platform"),
        "destination": match.get("destination"),
        "specialist": match.get("specialist"),
        "transcript": text,
        "language": session.language,
        "requested_language": session.language,
        "source": "device_microphone",
        "duplicate": False,
    }


def _with_spoken(session: DeviceSession, record: dict) -> dict:
    try:
        record["spoken"] = build_alert(
            {
                "token": record.get("token"),
                "room": record.get("room"),
                "train_no": record.get("train_no"),
                "platform": record.get("platform"),
                "destination": record.get("destination"),
            },
            kind=session.kind,
            language=session.language,
        )
    except ValueError:
        record["spoken"] = ""
    record["lang"] = session.language
    return record


def process_transcript(session: DeviceSession, text: str) -> dict:
    """Run one transcript through the shared matcher and update session state.

    The caller is responsible for synthesising audio when the returned record
    has a verdict of ``mine`` or ``missing`` and is not a duplicate.
    """
    if not isinstance(text, str):
        raise DeviceAudioError("transcript must be text")
    text = text.strip()
    if not text:
        raise DeviceAudioError("empty transcript")
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise DeviceAudioError("transcript is too long")

    now = time.time()
    normalised = _normalised_transcript(text)
    recent = session.recent_transcripts
    while recent and now - recent[0][1] >= DUPLICATE_WINDOW_SECONDS:
        recent.popleft()
    if normalised and any(value == normalised for value, _ts in recent):
        session.duplicate_chunks += 1
        record = _record(
            session,
            {"verdict": "mine" if session.pending_missing.get("active") else "none"},
            text,
            "duplicate",
        )
        record["duplicate"] = True
        return record
    if normalised:
        recent.append((normalised, now))

    session.heard += 1
    session.last_heard_text = text
    match = match_announcement(session.kind, text, session.value)
    verdict = match.get("verdict", "none")
    session.last_verdict = verdict
    session.last_token = match.get("token")
    session.last_room = match.get("room")

    if verdict == "none":
        return _record(session, match, text, "none")

    if verdict == "other":
        return _record(session, match, text, "other")

    if verdict == "missing":
        pending = session.pending_missing
        if not pending.get("active"):
            pending.update({
                "active": True,
                "started_at": now,
                "token": match.get("token") or session.value,
            })
            record = _record(session, match, text, "missing_pending")
            record["pending"] = True
            return record
        # A repeated missing announcement inside the pending window: claim it
        # now rather than waiting for a room that is not coming.
        if _claim_alert(session, match):
            session.pending_missing = {}
            record = _with_spoken(session, _record(session, match, text, "missing"))
            return record
        record = _record(session, match, text, "missing")
        record["duplicate"] = True
        return record

    # verdict == "mine"
    session.pending_missing = {}
    record = _record(session, match, text, "mine")
    if not _claim_alert(session, match):
        record["duplicate"] = True
        return record
    return _with_spoken(session, record)


def check_pending(session: DeviceSession) -> dict | None:
    """Return a missing-room alert once the pending window has elapsed."""
    pending = session.pending_missing
    if not pending.get("active"):
        return None
    if time.time() - pending.get("started_at", 0) < PENDING_MISSING_SECONDS:
        return None

    token = pending.get("token") or session.value
    match = {
        "verdict": "missing",
        "token": token if session.kind in ("hospital", "token") else None,
        "room": None,
        "train_no": token if session.kind == "train" else None,
        "train_name": None,
        "platform": None,
        "destination": token if session.kind == "bus" else None,
        "specialist": None,
    }
    session.pending_missing = {}
    if not _claim_alert(session, match):
        return None
    record = _with_spoken(session, _record(session, match, "", "missing"))
    return record


def begin_chunk(session: DeviceSession) -> bool:
    """Reserve one chunk slot for a session, or return False at the cap."""
    with _lock():
        if session.chunks >= MAX_CHUNKS_PER_SESSION:
            return False
        session.chunks += 1
        session.touch()
        return True


def set_last_alert(session: DeviceSession, record: dict | None) -> None:
    session.last_alert = record


def stats(session: DeviceSession) -> dict:
    return {
        "schemaVersion": 1,
        "active": True,
        "kind": session.kind,
        "value": session.value,
        "language": session.language,
        "chunks": session.chunks,
        "heard": session.heard,
        "duplicate_chunks": session.duplicate_chunks,
        "last_verdict": session.last_verdict,
        "last_token": session.last_token,
        "last_room": session.last_room,
        "pending_missing": bool(session.pending_missing.get("active")),
        "expires_in": max(0, int(SESSION_TTL_SECONDS - (time.time() - session.last_seen))),
    }


# --------------------------------------------------------------------------
# Audio validation and transcription
# --------------------------------------------------------------------------

def _wav_details(data: bytes) -> dict:
    """Validate a WAV byte string and return its format details."""
    if not data or len(data) < 44:
        raise DeviceAudioError("audio chunk is empty or too short")
    if len(data) > MAX_CHUNK_BYTES:
        raise DeviceAudioError("audio chunk is too large")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise DeviceAudioError("audio chunk must be a WAV file")

    # wave.open needs a file; a small in-memory wrapper keeps the API simple.
    import io

    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
    except (wave.Error, EOFError) as exc:
        raise DeviceAudioError("audio chunk is not a readable WAV file") from exc

    if channels != 1:
        raise DeviceAudioError("audio chunk must be mono")
    if width != 2:
        raise DeviceAudioError("audio chunk must be 16-bit PCM")
    if rate not in (8000, 16000):
        raise DeviceAudioError("audio chunk must be 8 kHz or 16 kHz")
    if frames <= 0:
        raise DeviceAudioError("audio chunk contains no samples")

    seconds = frames / float(rate)
    if seconds < MIN_CHUNK_SECONDS:
        raise DeviceAudioError("audio chunk is too short")
    if seconds > MAX_CHUNK_SECONDS:
        raise DeviceAudioError("audio chunk is too long")
    return {"channels": channels, "width": width, "rate": rate,
            "frames": frames, "seconds": seconds}


def transcribe_chunk(client, data: bytes, temp_dir: Path) -> dict:
    """Transcribe one validated chunk and always delete its temporary file.

    ``client`` is the existing Gnani client (or a fake in tests). It must expose
    ``transcribe_multilingual(path)`` returning a dict with ``transcript``.
    """
    details = _wav_details(data)
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="device-", suffix=".wav", dir=temp_dir, delete=False
        ) as handle:
            handle.write(data)
            temp_path = Path(handle.name)
        payload = client.transcribe_multilingual(temp_path)
        if not isinstance(payload, dict):
            raise DeviceAudioError("speech service returned an invalid response")
        return {
            "transcript": str(payload.get("transcript") or ""),
            "detected_language_code": payload.get("detected_language_code"),
            "seconds": details["seconds"],
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
