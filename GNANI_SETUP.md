# Gnani setup

Seva uses Gnani for speech-to-text (Prisma) and text-to-speech (Timbre). No key
is included in the repository.

## Configuration

Set these only in the host's secret manager or a root-protected environment file:

```text
GNANI_API_KEY=<provider key>
# Optional:
GNANI_BASE_URL=https://api.vachana.ai
GNANI_AUTH_HEADER=X-API-Key-ID
GNANI_MAX_RETRIES=1
```

- `GNANI_MAX_RETRIES` accepts `0`, `1`, or `2`; higher values are capped at `2`.
  Retries apply only to HTTP `429`, `500`, `502`, `503`, and `504`. Timeouts and
  connection errors are **not** retried, because the provider may already have
  processed and billed the request.
- The application sends `X-API-Key-ID` by default. Change the header only if the
  provider instructs you to.
- The key is never returned by `/api/providers`; that route exposes only a
  boolean.

## What is sent

- Short browser-captured clips that pass the loudness gate, encoded as 16 kHz
  mono 16-bit WAV.
- Alert sentences and spoken answers for text-to-speech.
- The server-side legacy microphone path, if enabled, sends recorded clips too.

Live transcripts are not sent to Gemini. Generated alert audio is cached on the
server when a persistent `SEVA_DATA_DIR` is configured.

## Cost controls

- The browser uploads only after detecting likely speech, not continuously.
- Sessions cap chunk count, clip duration, and total session lifetime.
- Repeated transcripts and repeated alerts are suppressed.
- TTS results are cached by text, language, and voice parameters.
- `usage.jsonl` under `SEVA_DATA_DIR` records successful calls and cache hits;
  retry entries do not count as calls.
- Tests use fake clients and never spend credits.

## Checking configuration without a paid call

```powershell
curl http://localhost:8000/api/providers
```

The `gnani.configured` field is a boolean. It does not test the key against the
provider and does not spend credits.

## Live testing

A live Gnani call requires explicit approval. When approved, use one short
clip and one short sentence, record the endpoint/model used, and check the
resulting `usage.jsonl` entry. Do not run the automated suite against the live
provider.

## Failure behavior

- Missing key: device-session creation is refused; ask/answer features use
  their local fallback where applicable.
- `429`/`5xx`: bounded retry, then a clean error to the client.
- Timeout/connection failure: no retry; a safe error is returned.
- TTS failure after a match: the alert result is kept and the client shows the
  `tts_error` state instead of losing the verdict.
