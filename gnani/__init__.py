"""Gnani Speech API bridge for Hermes Agent."""

__version__ = "3.0.0"

from .client import GnaniClient, GnaniError, load_dotenv, wav_duration_seconds
from .voices import DEFAULT_VOICE, LANGUAGES, TELUGU_VOICE_NOTES, VOICES_BY_LANGUAGE

__all__ = [
    "__version__",
    "GnaniClient",
    "GnaniError",
    "load_dotenv",
    "wav_duration_seconds",
    "LANGUAGES",
    "VOICES_BY_LANGUAGE",
    "DEFAULT_VOICE",
    "TELUGU_VOICE_NOTES",
]
