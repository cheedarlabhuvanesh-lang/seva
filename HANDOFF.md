# Seva Handoff

**Date:** 2026-11-04
**Branch:** `language-all`
**Working tree:** multilingual matching, TTS/replay, docs and tests applied; not committed
**Overall status:** implementation complete for the current Flask/PWA flow

## What was done

### Language and matching
- `web/i18n.js` supports all ten picker languages: Telugu inline; Hindi, Marathi,
  Bengali, Gujarati, Punjabi, Tamil, Kannada and Malayalam load from
  `i18n-<code>.js`; English restores the original text.
- `app/langdata.py` holds number words, digit conversion and zero-width helpers
  for all ten languages.
- `app/extract.py` and `app/matcher.py` extract and match tokens, rooms,
  specialists, train numbers/names, platforms and destinations in all ten
  languages. A token is digits with an optional block letter (`47` or `A-47`);
  trains must be exactly five digits.
- `app/watch.py` adds conservative fuzzy bus-place matching, including
  cross-script Latin/Indic comparison and ambiguity refusal.
- `app/alert.py` composes hospital, train and bus alerts in every supported
  language. Missing values are announced as missing, never invented.
- Gnani STT uses `prisma-v3.0` with `indic-auto`, so the announcement language is
  detected independently of the listener's reply language.

### Web and server
- `/tts` and `/api/replay` synthesise speech with a deterministic cache.
- `/alerts/<name>` serves generated alert audio. The watch result includes
  `audio_url` and `tts_error`.
- `confirmed.html`, `missing.html` and `history.html` play server audio and fall
  back to the browser voice only when the server has no audio.
- `ask.html` shows validation, rate-limit and network errors. `answer.html`
  renders Nova's light markdown safely and no longer keeps a duplicate copy of
  the hospital facts.
- `/` redirects to `/app/`; the manifest `start_url` and `scope` are `/app/`.
- The privacy page now lists every `seva.*` key and states the real network and
  microphone behaviour.

### Tests and docs
- `tests/test_multilingual.py` and `tests/test_bus_matching.py` cover the
  language and place-matching work.
- `tests/test_end_to_end.py` covers the root redirect, manifest, TTS/replay
  cache, watch start/state/simulate and ask flow with mocked services.
- Offline suite: `python -m unittest tests.test_gemini tests.test_multilingual tests.test_bus_matching tests.test_end_to_end -v`.
- `README.md` documents setup, layout, routes and the legacy backend.

## Still open

1. Run `tests/step4_complete.py` and `tests/step4_verification.py`
   against real Gnani/Gemini keys, a running server and real audio devices.
2. Decide whether to delete the legacy `backend/` directory. It is documented as
   legacy and is not imported by the current app, but it has not been removed.
3. Sign off the hospital facts in `data/hospital.json` against the real hospital
   before a public demo.
4. Commit the working tree when the above checks are accepted.

## Guardrails

- Do not copy secrets or `.env` contents into this document or logs.
- Do not treat the legacy `backend/` as part of the running system.
- Do not add hospital facts anywhere except `data/hospital.json`.
