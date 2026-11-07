# Seva Flask/Gemini Migration Status

**Updated:** 2026-11-04
**Branch:** `language-all`

This replaces the earlier 2026-10-30 report. The earlier report described the
state before the multilingual and announcement-work merge and is now historical.

## Current state

The Flask app in `app/` is the running backend. The PWA in `web/` is served from
`/app/`. The legacy FastAPI + SQLite app in `backend/` is not imported and is not
called by the PWA.

### Complete

- Flask routes: `/api/ask`, `/api/listen`, `/api/watch/start|state|stop|simulate`,
  `/api/mic/status`, `/api/languages`, `/api/usage`, `/tts`, `/api/replay`,
  `/alerts/<name>` and `/audio/<name>`.
- `/` redirects to `/app/`; the manifest starts at `/app/`.
- Nova: Gemini grounding, sanitised errors, local fallback, rate limiting, safe
  markdown rendering.
- Gnani: STT (`prisma-v2.5` and multilingual `prisma-v3.0`), TTS, cache, usage
  log and audio-tag validation.
- Announcements: gate, extraction, multilingual matching and hospital/train/bus
  alert phrasing. Token and train validation match the current engine.
- Web flow: listen -> listening -> confirmed/another/missing; ask -> answer;
  settings, history, privacy, help, about and PWA screens.
- Privacy page matches the real storage keys and network behaviour.
- Offline tests: Gemini, multilingual extraction/matching, bus matching and
  end-to-end Flask flows.

### Not verified here

- Real Gnani/Gemini calls in `tests/step4_complete.py` and
  `tests/step4_verification.py`. These require keys, a running server,
  audio devices and network access.
- A live microphone/speaker run on the demo laptop.
- The hospital facts in `data/hospital.json` against the real hospital.

## Migration decisions

- `data/` is the only source of hospital facts. `web/answer.html` no longer
  carries a copy.
- The legacy `backend/` directory is retained as reference until sign-off. It is
  not part of the running system.
- Deployment entry point is `START-SEVA.bat` or
  `python -m app.server --port 8000`, opened at `http://localhost:8000/app/`.
