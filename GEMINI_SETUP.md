# Gemini (Nova) setup

Seva uses Google Gemini to power Nova, the multilingual AI assistant for general,
educational, and hospital-navigation questions. No key is included in the
repository.

## Configuration

Set these only in the host's secret manager or a root-protected environment file:

```text
GEMINI_API_KEY=<provider key>
# Optional (defaults to gemini-3.5-flash-lite):
GEMINI_MODEL=gemini-3.5-flash-lite
```

- `GEMINI_API_KEY` is sent via the `x-goog-api-key` HTTP header. It is **never**
  passed as a query parameter in request URLs.
- `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. Supported models include:
  - `gemini-3.5-flash-lite` (default, recommended for fast, cost-effective inference)
  - `gemini-3.8-flash` (higher capacity flash model)
  - `gemini-2.5-flash` (for accounts with grandfathered/active access to 2.5)
  If specified with a `models/` prefix (e.g. `models/gemini-3.5-flash-lite`), the prefix
  is automatically normalized.
- The key is never returned by `/api/providers`; that route exposes only a
  boolean (`gemini.configured`).

## How Nova works

1. **User asks a question** via `POST /api/ask` with JSON `{"question": "...", "language": "te-IN"}`.
2. **Language enforcement:** Nova runs with a strict system instruction requiring
   the reply to be written entirely in the requested language (English, Telugu,
   Hindi, Tamil, Kannada, Malayalam, Marathi, Bengali, Gujarati, or Punjabi).
3. **Unicode script validation:** If the generated answer does not contain the
   expected Unicode script for the chosen language, a second translation pass is
   triggered automatically.
4. **Hospital facts grounding:** If `SEVA_HOSPITAL_DATA_APPROVED=1`, verified facts
   from `data/hospital.json` are supplied as local context. If hospital data is not
   approved, Nova operates in general assistance mode and will decline to invent
   unverified hospital details.
5. **Medical guardrails:** System prompts forbid diagnosing conditions or prescribing
   medication dosages, especially for children. For emergencies, the user is directed
   to hospital staff or emergency services (108).
6. **Localized fallbacks:** When Gemini is unconfigured, unreachable, or returns an
   error, `app/server.py` returns pre-translated local messages for each of the
   ten supported languages. The application never fails with an unhandled exception.

## Cost and safety controls

- **Rate limiting:** `/api/ask` is rate-limited per client IP.
- **Payload limits:** Question length is bounded (max 500 characters) to prevent token
  exhaustion.
- **Separation from audio:** Speech recognition transcripts from the announcement
  listener are never sent to Gemini.
- **Provider status check:** Check provider availability without consuming quota:

```powershell
curl http://localhost:8000/api/providers
```

The `gemini.configured` field reports `true` or `false` without making an API call.

## Live testing

A live Gemini request requires explicit approval. When testing:
1. Ensure `GEMINI_API_KEY` is set in your environment.
2. Send a single short test prompt via `POST /api/ask`.
3. Automated unit tests (`tests/test_gemini.py`) mock all network calls and never
   spend credits or quota.
