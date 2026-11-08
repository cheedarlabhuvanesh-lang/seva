# Hospital integration contract

**Current status:** Seva does not connect to any hospital system. The bundled
`data/hospital.json` facts are informational content, not a live feed. The
`/api/hospital/*` routes expose a normalized contract and remain gated.

Real hospital announcements require **written authorization** from the hospital
before any configuration is changed. Do not enable real-data mode based on a
verbal request alone.

## What is implemented

- `app/hospital.py` defines a normalized `Announcement` and adapter interface.
- `SyntheticAdapter` (`source_id=synthetic-demo`) is always available, makes no
  provider calls, and is clearly labelled `real_data: false`.
- `AuthorizedTextAdapter` validates text supplied by an authorized source but
  makes no network connection and stores nothing.
- `GET /api/hospital/sources` lists the adapters and their availability.
- `POST /api/hospital/announcement` normalizes one announcement payload.

## Activation prerequisites

All of the following are required before real announcements are accepted:

1. Written hospital authorization covering the announcement source, purpose,
   retention, and any patient-facing use.
2. `SEVA_HOSPITAL_DATA_APPROVED=1` in the server environment.
3. A non-empty `SEVA_HOSPITAL_SOURCE_ID` (and preferably
   `SEVA_HOSPITAL_SOURCE_NAME`).
4. HTTPS and the existing preview/authentication controls enabled.
5. An agreed delivery mechanism with the hospital. The current adapter only
   accepts a POST from a caller; it does not poll or subscribe.

If approval is absent, a configured source returns `403`. If approval is
present but no source is configured, the adapter reports itself unavailable and
direct parsing returns `503`. Unknown source IDs return `404`.

## Normalized announcement

```json
{
  "source_id": "ward-announcements",
  "source_type": "authorized_text",
  "source_name": "Ward announcements",
  "received_at": "2026-11-08T10:15:00+00:00",
  "text": "Token 47, please come to room 12.",
  "kind": "hospital",
  "language": "en-IN",
  "status": "now_serving",
  "real_data": true,
  "metadata": {}
}
```

| Field | Rules |
| --- | --- |
| `text` | Required for authorized sources; at most 2,000 characters. |
| `kind` | Lowercase token such as `hospital`; defaults to `hospital`. |
| `language` | Optional BCP-47-like value such as `en-IN` or `te`. |
| `status` | `announced`, `now_serving`, `called`, `missing`, `delayed`, `cancelled`, or `info`. Defaults to `announced`. |
| `metadata` | Optional JSON object, at most 8 KB. Do not put secrets in it. |
| `received_at` | Assigned by the server in UTC; client values are ignored. |
| `real_data` | Assigned by the adapter; client values are ignored. |

The server trims text, removes control characters, normalizes `status`, and
rejects malformed language/kind/metadata values with `400`.

## Endpoints

### `GET /api/hospital/sources`

```json
{
  "sources": [
    {
      "source_id": "synthetic-demo",
      "source_type": "synthetic",
      "source_name": "Synthetic demonstration announcement",
      "available": true,
      "real_data": false,
      "requires_approval": false,
      "reason": null
    },
    {
      "source_id": "",
      "source_type": "authorized_text",
      "source_name": "Authorized hospital source",
      "available": false,
      "real_data": true,
      "requires_approval": true,
      "reason": "Hospital data mode is not approved. Set SEVA_HOSPITAL_DATA_APPROVED=1 only after written approval."
    }
  ],
  "real_data_approved": false,
  "persistence": "none",
  "network_connections": "none"
}
```

### `POST /api/hospital/announcement`

Request body:

```json
{
  "source_id": "synthetic-demo",
  "text": "Synthetic demo announcement for token 47 at Counter 3.",
  "language": "en-IN",
  "status": "now_serving",
  "metadata": {"counter": "3"}
}
```

Response:

```json
{
  "announcement": {"...": "normalized fields as above"},
  "stored": false,
  "provider_calls": 0
}
```

`source_id` is required. Omitting `text` on the synthetic source uses a fixed
demo sentence. Both routes send `Cache-Control: no-store`.

## Security and data handling

- No raw announcement text is logged by these routes.
- No announcement is written to disk or a database.
- No Gnani, Gemini, or hospital system is called.
- Keys and tokens are never included in responses.
- The preview/authentication layer and HTTPS requirements still apply.
- Send only the minimum data needed for the announcement. Avoid patient names,
  diagnoses, phone numbers, and other identifiers in `text` or `metadata`.
- The caller is responsible for authenticating to the hospital source and for
  any retention or audit obligations on its side.

## Implementing a real adapter later

An adapter that connects to a hospital system must:

1. Authenticate with credentials supplied by the hospital, stored in a secret
   manager, never in the repository.
2. Validate message authenticity and reject replays or stale messages.
3. Normalize into the schema above without changing the public route contract.
4. Bound message size, rate, and retry behavior; never retry a non-idempotent
   delivery blindly.
5. Avoid logging announcement text and patient identifiers.
6. Require `SEVA_HOSPITAL_DATA_APPROVED=1` and fail closed otherwise.
7. Be covered by offline tests with recorded or synthetic payloads before any
   live connection is enabled.

This contract is not a claim of legal or regulatory compliance. The deploying
organization remains responsible for authorization, consent, retention, and
applicable law.
