# Seva — Implementation Plan

Status: Phase 1 inspection complete.
Repository was inspected in place; no architecture replacement is planned.

## 1. Existing architecture

- **Web/PWA**: vanilla HTML/CSS/JavaScript in `web/`, served by Flask at `/app/`.
  Ten UI languages (`en`, `te`, `hi`, `ta`, `kn`, `ml`, `mr`, `bn`, `gu`, `pa`)
  with client-side dictionaries in `web/i18n*.js`. Mobile-first column,
  `max-width: 400px`, bottom navigation, dark mode, no build step.
- **Server**: Flask application in `app/server.py`, WSGI entry point `wsgi.py`,
  Gunicorn configuration in `gunicorn.conf.py`, Render blueprint in
  `render.yaml`, Procfile for process managers.
- **Announcement matching**: `app/extract.py` (slot extraction),
  `app/matcher.py` (hospital/transport verdicts), `app/watch.py`
  (exact number matching, conservative place-name matching),
  `app/gate.py` (local loudness gate), `app/listen.py` (clip capture helpers).
- **Alerts**: `app/alert.py` builds short per-language alert phrases.
  Gnani Timbre TTS renders them. Alerts are cached by text + language.
- **STT/TTS provider**: `gnani/client.py`. Prisma STT (`/stt/v3`) and Timbre TTS
  (`/api/v1/tts/inference`). `X-API-Key-ID` header. Disk cache + `usage.jsonl`.
- **Nova**: `app/gemini.py`, Gemini `generateContent` with a key in the
  `x-goog-api-key` header (never a URL), strict language instruction, a second
  translation pass, and localized fallbacks in `app/server.py`.
- **Hospital facts**: `app/schemes.py` reads `data/hospital.json`; disabled in
  production unless `SEVA_HOSPITAL_DATA_APPROVED=1`.
- **Storage**: no application database. Browser `localStorage` for UI state and
  history. Disk for TTS cache, usage log and temporary clips under
  `SEVA_DATA_DIR`. Render currently uses ephemeral `/tmp/seva-data`.
- **Auth/ops**: HTTP Basic preview auth in production, HTTPS redirect, trusted
  hosts, CSP, HSTS (behind a TLS proxy), rate limits on ask/TTS/replay,
  `/healthz`, provider feature flags.

## 2. Working features (verified by existing offline tests)

- 109 offline tests pass before changes (`tests.test_gemini`,
  `tests.test_multilingual`, `tests.test_bus_matching`,
  `tests.test_end_to_end`).
- Exact token matching: `47` does not match `847`; leading zeros are significant.
- Hospital, train and bus matching, multilingual number words and scripts.
- Synthetic announcement demo (`/api/watch/demo`) without microphone or provider
  calls.
- Typed-announcement simulation (`/api/watch/simulate`, development only).
- Server-microphone watch flow (development/host only; deliberately disabled on
  Render because the server cannot hear a visitor's phone).
- Nova `/api/ask`, localized fallback, TTS, replay, audio cache, rate limits.
- Production preview auth, HTTPS redirect, security headers, health check.
- Ten-language UI dictionaries and alert phrase templates.

## 3. Incomplete / broken / unsafe functionality

1. **The phone's microphone is not used.** The browser only polls a server-side
   `sounddevice` stream, which on Render is the server's own (absent)
   microphone. The core product cannot work for a real phone user.
2. **`Permissions-Policy` denies microphone** (`microphone=()`), so a browser
   device-microphone implementation would be blocked even though the UI already
   has permission/error screens.
3. **No device audio upload route.** There is no session-scoped way to receive a
   small WAV chunk, transcribe it, match it, delete it, and return the verdict.
4. **No real hospital adapter.** Only synthetic and typed-simulation paths exist.
   There is no normalized announcement schema or authorized-source boundary.
5. **Provider retry behavior is undefined.** Gnani calls have timeouts but no
   explicit, documented retry policy. Tests cover request shape but not retry.
6. **Operational gaps**: in-memory watch state is single-process only; Render
   `/tmp` is ephemeral; no readiness signal for provider configuration; privacy
   page still describes only the server microphone.
7. **Visibility gaps**: clients cannot tell whether Gnani/Gemini are configured
   without attempting a paid call.

## 4. Required changes

### Phase 2 — Android/device microphone
- Add `app/device_audio.py`: short-lived session registry, validation,
  duplicate suppression, pending "room not heard" handling, temp-WAV lifecycle.
- Add `/api/device/capabilities`, `/api/device/session`, `/api/device/chunk`,
  `/api/device/stop`. Chunks are 16 kHz mono PCM WAV, bounded by size and
  duration, transcribed by the existing Gnani client, then deleted.
- Add `web/device-listener.js`: `getUserMedia`, Web Audio PCM capture,
  resampling to 16 kHz, local loudness gate, sequential chunk upload, states
  (requesting permission, listening, processing, detected, not detected,
  offline, permission denied, mic error).
- Update `web/listening.html` to use device capture when available, retain the
  synthetic demo, and keep the legacy server-watch path for development.
- Update `web/listen.html` consent copy and `Permissions-Policy` to
  `microphone=(self)`.
- Explain the browser-background limitation and the Android foreground-service
  option in `ANDROID_SETUP.md`.
- Offline tests use a pre-recorded/generated WAV and a fake STT client. No live
  Gnani calls.

### Phase 3 — Gnani STT/TTS
- Add bounded retry only for HTTP 429/5xx (never for timeouts/connection
  failures) with configurable attempts; default safe value.
- Add provider status (configured / not configured) without exposing keys.
- Keep the existing cache and usage log; add mock tests for retry, error
  sanitization, STT request shape, TTS cache and timeout.

### Phase 4 — Gemini/Nova
- Keep the existing provider separate from Gnani. Add `gemini.is_configured()`
  and expose only booleans in a provider status route.
- Keep the localized fallback and existing contract. Add mock tests only.

### Phase 5 — Hospital integration
- Add `app/hospital.py` with a normalized `Announcement` schema
  (`source_id`, `source_type`, `source_name`, `received_at`, `status`, `text`,
  `kind`, `language`, `metadata`) and a `HospitalAdapter` interface.
- Provide `SyntheticAdapter` (always available) and `AuthorizedTextAdapter`
  (returns 503/403 unless `SEVA_HOSPITAL_DATA_APPROVED=1` and an authorized
  source is configured). No connection to hospital systems is implemented.
- Add `/api/hospital/sources` and `/api/hospital/announcement`.
- Document the authorized integration contract in `HOSPITAL_INTEGRATION.md`.

### Phase 6 — Security/privacy/reliability
- Device endpoint: per-IP session and chunk limits, per-session chunk ceiling,
  content-length and duration bounds, WAV magic check, no raw audio retention,
  no transcript logging, `no-store` responses.
- Update privacy page and help copy for device audio and external STT.
- Raise `MAX_CONTENT_LENGTH` only to what a bounded WAV chunk needs.
- Document single-worker in-memory sessions, ephemeral Render disk, backup of
  `SEVA_DATA_DIR`, and the no-paid-database decision.
- Keep preview auth, HTTPS, CSP, trusted hosts, hospital mode disabled.

### Phase 7 — Tests
- New offline suites: token edge cases, normalization, synthetic flow, device
  permission/error contract, mocked STT/TTS, mocked Gemini, hospital adapter,
  auth/security headers, mobile/UI contract, translations present.
- Run the full suite and record actual output in `TEST_REPORT.md`.

### Phase 8 — Documentation/deployment
- Create `IMPLEMENTATION_REPORT.md`, `ANDROID_SETUP.md`, `GNANI_SETUP.md`,
  `GEMINI_SETUP.md`, `HOSPITAL_INTEGRATION.md`, `DEPLOYMENT_GUIDE.md`,
  `TEST_REPORT.md`.
- Review `git status`, confirm `.env` is not tracked, confirm Render env vars.
- Do **not** push or deploy without explicit approval.

## 5. Risks and dependencies

| Risk | Mitigation |
|---|---|
| Gnani credits are limited | No live calls in tests; local gate reduces audio; per-session chunk ceiling; TTS only on a real match; ask before any live test. |
| Device audio format varies | Capture PCM in the browser and encode 16 kHz mono WAV ourselves; reject anything else server-side. |
| Browser pauses microphone in background | Documented honestly; device capture requires the tab to remain open. Native Android foreground service is the reliable background option, not implemented here. |
| Multi-worker in-memory sessions | Keep one Gunicorn worker; documented. A shared store is a future change. |
| Render free tier sleeps / `/tmp` is ephemeral | Documented; no durable user data is stored server-side. Cache loss only affects cost, not correctness. |
| Hospital data authorization | Real mode stays disabled; adapter refuses until written approval and configuration. |
| Exact token matching must not regress | Existing tests retained and expanded (47 vs 847, leading zeros, block letters). |

## 6. Testing plan

- Baseline: existing 109 offline tests.
- New unit tests for `app/device_audio.py` (session bounds, WAV validation,
  duplicate suppression, fake STT, temp-file deletion).
- New route tests for `/api/device/*` with fake clients and preview auth.
- New unit tests for `app/hospital.py` (normalization, gating, exact matching).
- New tests for Gnani retry (mocked responses only) and provider status.
- Static/UI contract tests: microphone permission copy, device-listener script,
  key element IDs, `Permissions-Policy`, no secrets in responses.
- No automated test may call Gnani or Gemini. Any live check requires explicit
  approval and will be documented with the endpoint and expected credit use.
