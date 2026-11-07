# Seva

Seva is a voice help desk for people waiting in a hospital, railway station or bus
stand. It listens to live announcements through the laptop microphone, matches the
token, train or destination the user is waiting for, and answers questions through
Nova, a Gemini-backed assistant.

The browser app is a vanilla HTML/CSS/JS PWA in `web/`. The server is Flask.

## Run it

1. Put the API keys in `.env` at the project root:

   ```
   GNANI_API_KEY=...
   GEMINI_API_KEY=...
   GEMINI_MODEL=gemini-3.5-flash-lite
   ```

2. On Windows, double-click `START-SEVA.bat`. It installs `requirements.txt` on the
   first run and opens `http://localhost:8000/app/`.

3. Or run it directly:

   ```
   python -m pip install -r requirements.txt
   python -m app.server --port 8000
   ```

The microphone belongs to the laptop running Flask, not to the phone. Keep the
browser on the same machine as the server.

## Production deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the production WSGI start command,
environment variables, persistent storage, HTTPS requirements, Caddy/systemd
examples, backup/restore scripts, and the cloud microphone limitation. Start
from [.env.example](./.env.example); never commit real values. Public hosting
requires a persistent volume and additional edge rate limiting for the billable
API routes.

## What is where

- `app/server.py` - Flask routes and the announcement watch loop.
- `app/gemini.py` - Nova's Gemini client.
- `app/extract.py`, `app/matcher.py`, `app/watch.py` - announcement parsing and
  matching, including all ten supported languages.
- `app/alert.py` - spoken alert phrasing per language.
- `app/langdata.py` - number words and script helpers for the ten languages.
- `gnani/` - Gnani STT/TTS client, tags, cache and usage log.
- `data/hospital.json` - the single source of hospital facts used as grounding.
- `web/` - PWA screens. `web/index.html` is the splash screen and `/app/` is the
  canonical entry point.

## API

- `POST /api/ask` - Nova answer with hospital grounding and local fallback.
- `POST /api/listen` - one recorded clip through STT and matching.
- `POST /api/watch/start`, `GET /api/watch/state`, `POST /api/watch/stop` -
  server-side microphone watch flow.
- `POST /api/watch/simulate` - feed typed text through the same pipeline.
- `POST /tts` and `GET|POST /api/replay` - synthesise speech, cached by text and
  language.
- `GET /alerts/<name>` - generated alert audio.
- `GET /api/mic/status`, `GET /api/languages`, `GET /api/usage`.

`/` redirects to `/app/`, and the PWA manifest starts at `/app/`.

## Tests

The offline suite needs no microphone or API keys:

```
python -m unittest tests.test_gemini tests.test_multilingual tests.test_bus_matching tests.test_end_to_end -v
```

`tests/step4_complete.py` and `tests/step4_verification.py` are manual
integration scripts, intentionally excluded from pytest discovery. They call
the real Gnani and Gemini APIs and need a running server, keys, audio devices
and a network connection.

## Legacy code

`backend/` is the old FastAPI + SQLite implementation. The current Flask app does
not import it, and the PWA does not call it. It is kept only as a reference until
the migration is signed off. Do not add new behaviour there.

Generated audio, caches, usage logs and `.env` are intentionally ignored by Git.
