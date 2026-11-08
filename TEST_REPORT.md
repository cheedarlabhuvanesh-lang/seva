# Test report

**Date:** 2026-11-08
**Platform:** Windows, Python 3.12.10, Node 22.23.2
**Provider calls:** none. No Gnani or Gemini request was made.

## Commands and results

```powershell
python -m unittest tests.test_bus_matching tests.test_device_audio tests.test_end_to_end tests.test_gemini tests.test_gnani_client tests.test_hospital_adapter tests.test_multilingual tests.test_nova_language tests.test_security tests.test_ui_contract
```

Result: **186 tests passed, 0 failed** (about 0.9 s).

```powershell
python -m compileall -q app tests gnani
node --check web/device-listener.js
node --check web/i18n-device.js
node --check web/i18n.js
```

Result: all commands exited successfully with no output.

## Coverage by area

| Area | Tests |
| --- | --- |
| Exact token/train/bus matching and multilingual normalization | `test_bus_matching`, `test_multilingual`, `test_nova_language` |
| Device sessions, WAV validation, duplicate suppression, pending-missing, routes, rate limits, temp-file deletion | `test_device_audio` |
| Gnani request shape, retry policy, timeout behavior, provider status | `test_gnani_client` |
| Gemini fallback and language handling | `test_gemini` |
| Synthetic watch/ask/TTS/replay flows, production gates, auth | `test_end_to_end` |
| Hospital normalization and approval gating | `test_hospital_adapter` |
| Security headers, no-store routes, secret non-disclosure, preview auth | `test_security` |
| Device UI script order, capture/resample/WAV contract, translations, privacy copy | `test_ui_contract` |

## Warnings

Flask's test client emits `ResourceWarning: unclosed file` while serving static
files and cached audio. These warnings come from response teardown in the test
client, not from application logic, and do not affect results.

## Not tested here

- A real phone microphone and Android Chrome permission flow.
- Screen lock, app switching, and browser background behavior.
- Any live Gnani or Gemini request.
- Any hospital system, because no connection is implemented.
- Render deployment, HTTPS certificates, and platform sleep behavior.

Those require an explicit live-test decision, a physical device, or deployment
access. None was used in this run.
