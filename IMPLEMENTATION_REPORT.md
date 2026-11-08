# Seva — Implementation Report

**Date:** 2026-11-08  
**Branch:** `language-all`  
**Status:** All 8 Phases Complete — Ready for Review / Verification  

---

## Executive Summary

Seva has been updated from a local prototype relying on the host's server microphone to a production-ready, mobile-first Progressive Web App (PWA) capable of real phone-microphone capture via modern browser standards (`getUserMedia`), with strict security, rate limiting, and cost controls.

All architectural decisions preserve the existing Flask + PWA codebase without introducing heavy frameworks, paid databases, or complex build steps. All 186 automated unit and integration tests run offline without making external paid calls to speech (Gnani) or AI (Gemini) providers.

---

## Completed Phases

### Phase 1 — Codebase Inspection & Architecture Preservation
- Retained the clean, single-column vanilla HTML/CSS/JS frontend in `web/` with 10 Indic languages (`en`, `te`, `hi`, `ta`, `kn`, `ml`, `mr`, `bn`, `gu`, `pa`).
- Preserved the core Flask backend in `app/server.py` with Gunicorn WSGI configuration (`gunicorn.conf.py`).
- Kept the announcement matching engine (`app/extract.py`, `app/matcher.py`, `app/watch.py`) intact with exact token validation (e.g. `47` does not match `847`, leading zeros are preserved).
- Confirmed legacy `backend/` remains inert and unimported.

### Phase 2 — Android / Client-Side Device Audio Capture
- **`app/device_audio.py`**: Added an in-memory session manager for device audio uploads.
  - Generates session tokens with 10-minute TTL.
  - Enforces per-session limits: maximum 60 chunks per session, maximum 1 MB and 20 seconds per chunk.
  - Validates 16 kHz mono 16-bit PCM WAV headers.
  - Deletes temporary audio files immediately after transcription inside `try...finally` blocks.
  - Suppresses duplicate consecutive transcripts to save compute and eliminate repeated alerts.
  - Emits missing-detail alerts when tokens match but room/doctor information is absent.
- **Device API Endpoints**:
  - `GET /api/device/capabilities`: Reports availability of device capture, format requirements (16 kHz mono PCM WAV), and constraints.
  - `POST /api/device/session`: Creates a bounded listener session.
  - `POST /api/device/chunk`: Receives audio clips, transcribes with Gnani STT, runs matching, and returns alert audio URLs.
  - `POST /api/device/stop`: Closes an active listening session.
- **Frontend Capture (`web/device-listener.js`)**:
  - Implements `navigator.mediaDevices.getUserMedia` capture.
  - Performs real-time linear resampling to 16 kHz mono PCM.
  - Uses an RMS-based local loudness gate to discard silence and only upload audible speech.
  - Handles UI states: idle, requesting permission, listening, processing, detected, not detected, permission denied, mic unavailable.
  - Uses `navigator.sendBeacon` to cleanly stop sessions on page unload.
- **Permissions-Policy**:
  - Updated server headers to allow `microphone=(self)` while blocking camera and geolocation.

### Phase 3 — Gnani Speech Integration (STT & TTS)
- **Retry Policy (`gnani/client.py`)**:
  - Added bounded retry logic for transient HTTP status codes (`429`, `500`, `502`, `503`, `504`).
  - Configurable via `GNANI_MAX_RETRIES` (default `1`, max capped at `2`).
  - Connection errors and timeouts are deliberately **not** retried to prevent double-billing.
- **Provider Status**:
  - Exposed `/api/providers` returning booleans (`gnani.configured: true/false`). API keys are never disclosed.
- **Cost Controls**:
  - Retained deterministic disk caching for TTS audio.
  - Retained `usage.jsonl` tracking for calls and cache hits.
  - All tests use fake mock clients, preventing any live credit consumption during development and CI.

### Phase 4 — Gemini / Nova Multilingual Assistant
- **Safe API Client (`app/gemini.py`)**:
  - Passes API key in HTTP header `x-goog-api-key`, never in URL query strings.
  - Strict system instructions enforce reply in the requested language.
  - Script validation checks ensure correct Unicode blocks; automatically applies a second translation pass if needed.
  - Strict guardrails against diagnosing or recommending medication doses.
- **Fallback Resilience**:
  - Pre-translated fallback responses in `app/server.py` ensure graceful failure in all 10 languages if the network or API is unavailable.
  - Rate limiting on `POST /api/ask` protects against denial-of-service and quota drain.

### Phase 5 — Hospital Integration Contract
- **Normalized Schema & Adapter (`app/hospital.py`)**:
  - Implemented `Announcement` dataclass with fields: `source_id`, `source_type`, `source_name`, `received_at`, `text`, `kind`, `language`, `status`, `real_data`, `metadata`.
  - Created `SyntheticAdapter` (`source_id=synthetic-demo`, `real_data=false`) for safe, uncredentialed testing.
  - Created `AuthorizedTextAdapter` requiring written hospital sign-off (`SEVA_HOSPITAL_DATA_APPROVED=1`) and source configuration. Returns `403` or `503` if unapproved.
- **Endpoints**:
  - `GET /api/hospital/sources`: Lists configured announcement sources.
  - `POST /api/hospital/announcement`: Ingests and normalizes external announcement payloads.
- **Documentation (`HOSPITAL_INTEGRATION.md`)**:
  - Clear guidance on hospital authorization, privacy boundaries, and delivery protocols.

### Phase 6 — Security, Privacy, and Reliability
- **Hardened HTTP Headers**:
  - `Content-Security-Policy`: `default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`.
  - `Permissions-Policy`: `microphone=(self), camera=(), geolocation=()`.
  - `X-Content-Type-Options: nosniff`.
  - `X-Frame-Options: DENY`.
  - `Referrer-Policy: strict-origin-when-cross-origin`.
  - `Strict-Transport-Security` automatically attached on production HTTPS requests.
  - `Cache-Control: no-store` on all API endpoints.
- **Upload Protection**:
  - `MAX_CONTENT_LENGTH` set to 2 MB.
  - Server validates WAV magic header (`RIFF....WAVEfmt `).
  - Temporary audio files are isolated in `SEVA_DATA_DIR/samples/device-tmp/` and destroyed immediately after processing.
- **No Secret Leakage**:
  - Comprehensive unit tests ensure secrets (`GNANI_API_KEY`, `GEMINI_API_KEY`, `SEVA_PREVIEW_PASSWORD`) are never surfaced in logs or JSON endpoints.
- **Production Preview Protection**:
  - HTTP Basic Auth gate for preview deployments.
  - Unapproved hospital data disabled by default in production.

### Phase 7 — Comprehensive Offline Test Suite
- 186 unit and integration tests passing with 0 failures:
  - `tests.test_bus_matching`: Bus place fuzzy matching and token boundary logic.
  - `tests.test_device_audio`: Session management, WAV validation, chunk boundaries, duplicate suppression, and route limits.
  - `tests.test_end_to_end`: Core Flask routes, TTS cache, replay, preview auth, manifest, and synthetic demo.
  - `tests.test_gemini`: Language prompts, script validation, and localized fallback dictionaries.
  - `tests.test_gnani_client`: Request headers, retry behavior on 429/5xx, timeouts, and provider status.
  - `tests.test_hospital_adapter`: Normalization, synthetic source, and approval gating.
  - `tests.test_multilingual`: Number words, token formats, and matching across 10 languages.
  - `tests.test_nova_language`: Multilingual query handling.
  - `tests.test_security`: Security headers, HSTS, preview auth, secret non-disclosure, and cache control.
  - `tests.test_ui_contract`: Script loading order, client WAV encoder contract, translations, and privacy copy.
- Python compilation (`compileall`) and JavaScript syntax (`node --check`) validated cleanly.

### Phase 8 — Documentation & Operational Guides
Created and refreshed all operational documentation:
- `IMPLEMENTATION_PLAN.md`: Strategic roadmap and architecture invariants.
- `IMPLEMENTATION_REPORT.md`: This comprehensive verification report.
- `ANDROID_SETUP.md`: Guide for phone microphone setup, permissions, and browser limitations.
- `GNANI_SETUP.md`: Setup, cost controls, headers, and retry mechanics for Gnani STT/TTS.
- `GEMINI_SETUP.md`: Setup, prompt engineering, script verification, and safety rules for Gemini/Nova.
- `HOSPITAL_INTEGRATION.md`: Schema, authorization prerequisites, and adapter contract.
- `DEPLOYMENT.md` & `DEPLOYMENT_GUIDE.md`: VPS, Caddy, Gunicorn, systemd, and Render preview deployment guides.
- `TEST_REPORT.md`: Detailed test run records and verification commands.

---

## Current Git Status & Verification

1. **Working Tree**:
   - Modified tracked files: `.env.example`, `.gitignore`, `DEPLOYMENT.md`, `README.md`, `app/gemini.py`, `app/server.py`, `gnani/client.py`, `tests/test_end_to_end.py`, `tests/test_gnani_client.py`, `web/help.html`, `web/i18n.js`, `web/listen.html`, `web/listening.html`, `web/privacy.html`.
   - Untracked files: new test suites, adapters, audio capture modules, and documentation.
2. **Secrets Check**:
   - `.env` is ignored by `.gitignore` and is not tracked.
   - No credentials or API keys exist in git status or any committed code.
3. **Offline Invariant**:
   - Zero live provider calls were made during test runs.

---

## Recommended Next Steps

1. **User Review**: Inspect the working tree and the test report.
2. **Staging / Preview Testing**: Deploy to Render or VPS with preview auth enabled (`SEVA_PREVIEW_AUTH_REQUIRED=1`) to test client device microphone in Android Chrome.
3. **Commit & Branch Management**: When ready and approved by the user, commit the working tree with descriptive attribution to the `language-all` branch or create a PR to `main`.
