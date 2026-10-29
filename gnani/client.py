"""Minimal, dependency-light client for the Gnani Speech APIs.

Covers the two endpoints you need for the Great Indian AI Internship Challenge:

  * Prisma v2.5 STT  ->  POST https://api.vachana.ai/stt/v3           (multipart)
  * Timbre v2.5 TTS  ->  POST https://api.vachana.ai/api/v1/tts/inference  (JSON)

Both authenticate with the  X-API-Key-ID  header.
Docs: https://docs.gnani.ai/api/introduction/introduction

Design notes
------------
* **TTS responses are cached on disk.** You have 5,000 programme credits; the
  fastest way to waste them is re-synthesising the same prompt during a demo.
  Identical requests are served from ./cache and cost nothing.
* **Every call is logged** to usage.jsonl so you can see where credits went.
* **The TTS response shape is handled defensively** - raw audio bytes, or JSON
  carrying base64, both work - because the docs show a bare string for 200.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

from . import tags as audio_tags
from . import voices as voice_catalogue

DEFAULT_BASE_URL = "https://api.vachana.ai"
STT_PATH = "/stt/v3"
TTS_PATH = "/api/v1/tts/inference"
VC_EMBED_PATH = "/api/v1/tts/voice-clone/embeddings"
TTS_MODEL = "timbre-v3.0"   # launched 14 Oct 2026: audio tags, 13 voices, all 10 languages
VC_MODEL = "vachana-vc-v1"

# Prisma REST hard limit. The docs call 30s the ideal.
STT_MAX_SECONDS = 60
STT_IDEAL_SECONDS = 30
STT_ALLOWED_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}


class GnaniError(RuntimeError):
    """Raised when the Gnani API returns an error payload or an unexpected body."""


def load_dotenv(path: str | Path | None = None) -> None:
    """Load a .env file without needing python-dotenv.

    Searches, in order:
      1. an explicit `path`, if given
      2. ./.env               (the current working directory)
      3. <project root>/.env  (the folder containing this package)

    Step 3 matters because Hermes runs these scripts from whatever directory
    the session started in, which is often not the project folder. Real
    environment variables always win over the file, so a `setx GNANI_API_KEY`
    on Windows overrides everything here.
    """
    if path is not None:
        candidates = [Path(path)]
    else:
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parent.parent / ".env",
        ]

    for candidate in candidates:
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return  # first file found wins


def repair_wav_header(raw: bytes) -> bytes:
    """Rewrite the chunk sizes of a streamed WAV so players accept it.

    Timbre streams its audio, which means it has to write the RIFF and data
    chunk sizes *before* it knows how long the clip will be. They come back
    as the placeholder 0xFFFFFFFF. Python's wave module shrugs that off, but
    Windows Media.SoundPlayer, ffmpeg and most browsers reject the file as
    corrupt. Patch both sizes to the real byte counts.

    Anything that is not a RIFF/WAVE file is returned untouched, so mp3 and
    ogg responses pass straight through.
    """
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return raw

    out = bytearray(raw)
    position = 12
    while position + 8 <= len(out):
        chunk_id = bytes(out[position:position + 4])
        chunk_size = int.from_bytes(out[position + 4:position + 8], "little")
        body = position + 8
        if chunk_id == b"data":
            actual = len(out) - body
            if chunk_size != actual:
                out[position + 4:position + 8] = actual.to_bytes(4, "little")
            break
        if chunk_size == 0 or body + chunk_size > len(out):
            break  # malformed beyond repair - at least fix the RIFF size
        position = body + chunk_size + (chunk_size & 1)

    out[4:8] = (len(out) - 8).to_bytes(4, "little")
    return bytes(out)


def wav_duration_seconds(path: str | Path) -> float | None:
    """Duration of a WAV file in seconds, or None if it isn't a readable WAV.

    Does NOT trust the header's frame count. Streaming encoders - Gnani's TTS
    among them - write a placeholder data-chunk size (0x7FFFFFFF / 0xFFFFFFFF)
    because the length isn't known when the header goes out. Believing it
    yields absurd durations like 44739s for a 3-second clip, which would then
    trip the 60-second guard on a perfectly valid file.

    So: measure from the actual bytes on disk, and only fall back to the
    header when the two roughly agree.
    """
    try:
        file_size = Path(path).stat().st_size
        with contextlib.closing(wave.open(str(path), "rb")) as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels() or 1
            width = handle.getsampwidth() or 2
            header_frames = handle.getnframes()
    except (wave.Error, EOFError, OSError):
        return None

    byte_rate = rate * channels * width
    if byte_rate <= 0:
        return None

    # 44 bytes is the canonical RIFF header; a few extra chunks change this
    # only marginally, which is fine for a duration sanity check.
    size_based = max(0.0, file_size - 44) / byte_rate
    header_based = header_frames / float(rate) if header_frames else 0.0

    if header_based and abs(header_based - size_based) <= max(1.0, 0.25 * size_based):
        return header_based  # header agrees - use the precise value
    return size_based  # header is a placeholder - trust the bytes


@dataclass
class GnaniClient:
    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    cache_dir: Path = field(default_factory=lambda: Path("cache"))
    usage_log: Path = field(default_factory=lambda: Path("usage.jsonl"))
    timeout: int = 60
    # Default TTS model for every speak() call. Override per call with
    # speak(..., model="timbre-v2.5") when you need Hinglish or a v2.5 voice.
    tts_model: str = TTS_MODEL
    auth_header: str = "X-API-Key-ID"

    def __post_init__(self) -> None:
        load_dotenv()
        self.api_key = self.api_key or os.getenv("GNANI_API_KEY")
        self.base_url = (os.getenv("GNANI_BASE_URL") or self.base_url).rstrip("/")
        self.auth_header = os.getenv("GNANI_AUTH_HEADER") or self.auth_header
        if not self.api_key:
            raise GnaniError(
                "No API key. Put GNANI_API_KEY=... in a .env file next to your script, "
                "or set it as an environment variable. Get a key at https://app.gnani.ai/voice"
            )
        self.cache_dir = Path(self.cache_dir)
        self.usage_log = Path(self.usage_log)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ utils

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers = {self.auth_header: self.api_key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _log(self, **entry: Any) -> None:
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with self.usage_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # never let logging break a demo

    @staticmethod
    def _raise_for_api_error(payload: Any, status: int) -> None:
        if isinstance(payload, dict) and payload.get("success") is False:
            err = payload.get("error") or {}
            raise GnaniError(
                f"[{status}] {err.get('type', 'ERROR')}: "
                f"{err.get('message', json.dumps(payload, ensure_ascii=False))}"
            )

    # -------------------------------------------------------------------- STT

    def transcribe(
        self,
        audio_path: str | Path,
        language_code: str = "te-IN",
        itn: bool = True,
        native_numerals: bool = False,
        substitutions: list[dict[str, Any]] | None = None,
        bias_list: Iterable[str] | None = None,
    ) -> str:
        """Transcribe a short audio clip with Prisma v2.5 and return the transcript.

        itn=True sets format='transcribe', which writes numbers, currency, dates
        and phone numbers in conventional form instead of spelled-out words.
        """
        path = Path(audio_path)
        if not path.exists():
            raise GnaniError(f"Audio file not found: {path}")
        if path.suffix.lower() not in STT_ALLOWED_SUFFIXES:
            raise GnaniError(
                f"Prisma does not accept {path.suffix!r}. "
                f"Use one of: {', '.join(sorted(STT_ALLOWED_SUFFIXES))}. "
                "Browser-default webm/opus must be converted first."
            )

        duration = wav_duration_seconds(path)

        # Absolute safety net: the slowest format Prisma accepts is 8 kHz
        # 8-bit mono = 8,000 bytes/sec, so a file under 60 * 8000 bytes
        # physically cannot exceed the limit, whatever a corrupt or
        # placeholder header claims. Never block on those.
        min_bytes_for_limit = STT_MAX_SECONDS * 8000
        could_possibly_be_too_long = path.stat().st_size > min_bytes_for_limit

        if duration and duration > STT_MAX_SECONDS and could_possibly_be_too_long:
            raise GnaniError(
                f"Clip is {duration:.1f}s. The REST endpoint caps at {STT_MAX_SECONDS}s "
                "- split it, or use the Batch/WebSocket API for long audio."
            )
        if duration and duration > STT_IDEAL_SECONDS:
            print(f"  [warn] {duration:.1f}s clip - accuracy is best at <= {STT_IDEAL_SECONDS}s")

        data: dict[str, Any] = {
            "language_code": language_code,
            "format": "transcribe" if itn else "verbatim",
        }
        if itn and native_numerals:
            data["itn_native_numerals"] = "true"
        if substitutions:
            if len(substitutions) > 10:
                raise GnaniError("substitution_map accepts a maximum of 10 rules.")
            data["enable_substitution"] = "true"
            data["substitution_map"] = json.dumps(substitutions, ensure_ascii=False)
        if bias_list:
            words = list(bias_list)
            if len(words) > 100:
                raise GnaniError("bias_list accepts a maximum of 100 words.")
            data["bias_list"] = json.dumps(words, ensure_ascii=False)

        with path.open("rb") as fh:
            response = requests.post(
                f"{self.base_url}{STT_PATH}",
                headers=self._headers(),
                data=data,
                files={"audio_file": (path.name, fh)},
                timeout=self.timeout,
            )

        try:
            payload = response.json()
        except ValueError:
            raise GnaniError(
                f"[{response.status_code}] Non-JSON reply from STT: {response.text[:300]}"
            ) from None

        self._raise_for_api_error(payload, response.status_code)
        if response.status_code != 200:
            raise GnaniError(f"[{response.status_code}] {json.dumps(payload)[:300]}")

        transcript = payload.get("transcript", "")
        self._log(
            op="stt",
            language=language_code,
            seconds=round(duration, 2) if duration else None,
            chars=len(transcript),
            request_id=payload.get("request_id"),
        )
        return transcript

    # -------------------------------------------------------------------- TTS

    def speak(
        self,
        text: str,
        language: str = "te-IN",
        voice: str | None = None,
        speed: float = 1.0,
        out_path: str | Path | None = None,
        sample_rate: int = 48000,
        container: str = "wav",
        encoding: str = "linear_pcm",
        use_cache: bool = True,
        model: str | None = None,
    ) -> Path:
        """Synthesise `text` and write an audio file. Returns its path.

        model: "timbre-v3.0" for audio tags and any-voice-any-language,
               "timbre-v2.5" for the 42-voice catalogue and Hinglish.
               Defaults to self.tts_model.
        """
        if not text.strip():
            raise GnaniError("Nothing to speak - text is empty.")

        model = model or self.tts_model
        voice = voice_catalogue.validate(language, voice, model)

        # Audio tags: validate on v3.0 so a typo fails here rather than being
        # silently swallowed by the server; strip on v2.5 where they are not
        # supported and text made only of tags is a 400.
        if model == voice_catalogue.V3_MODEL:
            try:
                audio_tags.validate(text)
            except ValueError as exc:
                raise GnaniError(str(exc)) from None
        elif audio_tags.find(text):
            if audio_tags.is_only_tags(text):
                raise GnaniError(
                    f"Text is nothing but audio tags, which {model} rejects with 400. "
                    "Add words, or switch to model='timbre-v3.0'."
                )
            text = audio_tags.strip(text)

        audio_config = {
            "sample_rate": sample_rate,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": encoding,
            "container": container,
        }
        # Known server-side quirk: mp3 + extra audio params can 500. Keep mp3 bare.
        if container == "mp3":
            audio_config = {"container": "mp3"}

        body = {
            "text": text,
            "model": model,
            "voice": voice,
            "language": language,
            "speed": speed,
            "audio_config": audio_config,
        }

        key = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        cached = self.cache_dir / f"{key}.{container}"
        out = Path(out_path) if out_path else cached

        if use_cache and cached.exists() and cached.stat().st_size > 0:
            if out != cached:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(cached.read_bytes())
            self._log(op="tts", cached=True, chars=len(text), voice=voice, language=language)
            return out

        response = requests.post(
            f"{self.base_url}{TTS_PATH}",
            headers=self._headers(json_body=True),
            json=body,
            timeout=self.timeout,
        )
        audio = self._extract_audio(response)

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        if use_cache and out != cached:
            cached.write_bytes(audio)

        self._log(
            op="tts", cached=False, chars=len(text), voice=voice,
            language=language, bytes=len(audio), model=model,
            tags=audio_tags.find(text) or None,
        )
        return out

    # ------------------------------------------------------------ voice clone

    def create_voice_embedding(
        self,
        reference_audio: str | Path,
        save_to: str | Path | None = None,
    ) -> dict[str, Any]:
        """Turn 5-30s of clear reference speech into a reusable speaker embedding.

        Returns the embedding dict ({embedding, shape, dtype}) and optionally
        saves it as JSON so you never have to pay for the same clone twice.
        """
        path = Path(reference_audio)
        if not path.exists():
            raise GnaniError(f"Reference audio not found: {path}")

        duration = wav_duration_seconds(path)
        if duration and not (3 <= duration <= 60):
            print(f"  [warn] reference clip is {duration:.1f}s - 5 to 30s of clean speech works best")

        with path.open("rb") as fh:
            response = requests.post(
                f"{self.base_url}{VC_EMBED_PATH}",
                headers=self._headers(),
                files={"audio_file": (path.name, fh)},
                timeout=self.timeout,
            )

        try:
            payload = response.json()
        except ValueError:
            raise GnaniError(
                f"[{response.status_code}] Non-JSON reply from voice clone: {response.text[:300]}"
            ) from None

        self._raise_for_api_error(payload, response.status_code)
        if response.status_code != 200 or "embedding" not in payload:
            raise GnaniError(f"[{response.status_code}] {json.dumps(payload)[:300]}")

        embedding = {
            "embedding": payload["embedding"],
            "shape": payload.get("shape", [1, 768]),
            "dtype": payload.get("dtype", "torch.bfloat16"),
        }
        if save_to:
            target = Path(save_to)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(embedding), encoding="utf-8")

        self._log(op="voice_clone_embed", seconds=round(duration, 2) if duration else None)
        return embedding

    @staticmethod
    def load_voice_embedding(path: str | Path) -> dict[str, Any]:
        """Load an embedding saved by create_voice_embedding()."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "embedding" not in data:
            raise GnaniError(f"{path} is not a speaker embedding file.")
        return data

    def speak_cloned(
        self,
        text: str,
        embedding: dict[str, Any] | str | Path,
        out_path: str | Path | None = None,
        sample_rate: int = 44100,
        container: str = "wav",
        encoding: str = "linear_pcm",
        use_cache: bool = True,
    ) -> Path:
        """Synthesise `text` in a cloned voice using a speaker embedding."""
        if not text.strip():
            raise GnaniError("Nothing to speak - text is empty.")
        if isinstance(embedding, (str, Path)):
            embedding = self.load_voice_embedding(embedding)

        audio_config = {
            "sample_rate": sample_rate,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": encoding,
            "container": container,
        }
        if container == "mp3":
            audio_config = {"container": "mp3"}

        body = {
            "text": text,
            "model": VC_MODEL,
            "audio_config": audio_config,
            "speaker_embedding": embedding,
        }

        # Cache on the text + a short fingerprint of the embedding, not the
        # whole 768-dim blob.
        fingerprint = hashlib.sha256(
            str(embedding.get("embedding", ""))[:4096].encode("utf-8")
        ).hexdigest()[:12]
        key = hashlib.sha256(
            f"{fingerprint}|{text}|{json.dumps(audio_config, sort_keys=True)}".encode("utf-8")
        ).hexdigest()[:20]
        cached = self.cache_dir / f"vc_{key}.{container}"
        out = Path(out_path) if out_path else cached

        if use_cache and cached.exists() and cached.stat().st_size > 0:
            if out != cached:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(cached.read_bytes())
            self._log(op="voice_clone_tts", cached=True, chars=len(text))
            return out

        response = requests.post(
            f"{self.base_url}{TTS_PATH}",
            headers=self._headers(json_body=True),
            json=body,
            timeout=self.timeout,
        )
        audio = self._extract_audio(response)

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        if use_cache and out != cached:
            cached.write_bytes(audio)

        self._log(op="voice_clone_tts", cached=False, chars=len(text), bytes=len(audio))
        return out

    def _extract_audio(self, response: requests.Response) -> bytes:
        """Pull audio bytes out of a TTS reply and hand back a *valid* file."""
        return repair_wav_header(self._extract_audio_raw(response))

    def _extract_audio_raw(self, response: requests.Response) -> bytes:
        """Pull audio bytes out of a TTS reply, whatever shape it arrives in."""
        content_type = response.headers.get("Content-Type", "")

        if response.status_code != 200:
            try:
                payload = response.json()
            except ValueError:
                raise GnaniError(
                    f"[{response.status_code}] TTS failed: {response.text[:300]}"
                ) from None
            self._raise_for_api_error(payload, response.status_code)
            raise GnaniError(f"[{response.status_code}] {json.dumps(payload)[:300]}")

        # 1. Straight binary audio.
        if content_type.startswith("audio/") or content_type == "application/octet-stream":
            return response.content

        # 2. JSON wrapper - either an error, or base64 under a few possible keys.
        if "json" in content_type:
            payload = response.json()
            self._raise_for_api_error(payload, response.status_code)
            if isinstance(payload, str):
                return self._b64(payload)
            if isinstance(payload, dict):
                for field_name in ("audio", "audio_content", "audio_base64", "data", "result"):
                    value = payload.get(field_name)
                    if isinstance(value, str) and value:
                        return self._b64(value)
            raise GnaniError(f"No audio field in TTS reply: {json.dumps(payload)[:300]}")

        # 3. Bare string body (what the docs' 200 example shows) - try base64,
        #    and fall back to raw bytes if it already looks like a RIFF/ID3 header.
        raw = response.content
        if raw[:4] in (b"RIFF", b"OggS", b"fLaC") or raw[:3] == b"ID3":
            return raw
        return self._b64(response.text)

    @staticmethod
    def _b64(value: str) -> bytes:
        cleaned = value.strip().strip('"')
        if cleaned.startswith("data:") and "," in cleaned:
            cleaned = cleaned.split(",", 1)[1]
        try:
            return base64.b64decode(cleaned, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise GnaniError(f"Could not decode TTS audio payload: {exc}") from exc

    # ------------------------------------------------------------------ usage

    def usage_summary(self) -> dict[str, Any]:
        """Roll up usage.jsonl so you can watch your 5,000 credits."""
        summary = {
            "stt_calls": 0, "stt_seconds": 0.0,
            "tts_calls": 0, "tts_chars": 0, "tts_cache_hits": 0,
            "clone_embeddings": 0, "clone_tts_calls": 0, "clone_cache_hits": 0,
        }
        if not self.usage_log.exists():
            return summary
        for line in self.usage_log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("op") == "stt":
                summary["stt_calls"] += 1
                summary["stt_seconds"] += entry.get("seconds") or 0
            elif entry.get("op") == "tts":
                if entry.get("cached"):
                    summary["tts_cache_hits"] += 1
                else:
                    summary["tts_calls"] += 1
                    summary["tts_chars"] += entry.get("chars") or 0
            elif entry.get("op") == "voice_clone_embed":
                summary["clone_embeddings"] += 1
            elif entry.get("op") == "voice_clone_tts":
                if entry.get("cached"):
                    summary["clone_cache_hits"] += 1
                else:
                    summary["clone_tts_calls"] += 1
                    summary["tts_chars"] += entry.get("chars") or 0
        summary["stt_seconds"] = round(summary["stt_seconds"], 1)
        return summary
