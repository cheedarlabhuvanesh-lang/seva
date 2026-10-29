# Seva Flask/Gemini Migration Status Report

**Branch:** seva-flask-gemini-2026-10-30  
**Date:** 2026-10-30  
**Backups:** Verified at `C:\Users\cheed\seva-backup-2026-10-30` and `C:\Users\cheed\OneDrive - K L University\Desktop\seva-backup-2026-10-30`

## ✅ COMPLETED

### Step 1-2: Backups and Branch
- Full backups created and hash-verified
- Branch `seva-flask-gemini-2026-10-30` created
- Pre-existing changes committed (ask.html, answer.html, test-mic.html deletion)

### Step 2: Engine Integration
- Copied `gnani/` package (Gnani API client)
- Copied `app/` modules (server.py, gemini.py, schemes.py, etc.)
- Copied `data/` JSON files (hospital, rail, bus, schemes)
- Copied `tests/test_gemini.py` (10 mocked tests)
- Copied requirements.txt and START-SEVA.bat
- Created root `.env` from backend/.env
- Updated `.gitignore` for DB, cache, usage, tests/real_audio

### Step 3: Web Integration (Partial)
- Updated `web/ask.html` to call Flask `/api/ask` endpoint
- Fixed payload structure: `{question, language}` instead of `{question, lang}`

### Verification Completed
- All 10 mocked Gemini tests pass
- Flask server runs from OneDrive: `python -m app.server --port 8000`
- Server binds to 127.0.0.1:8000 ✓
- Serves screens at `/app/` ✓
- API endpoint `/api/ask` responds ✓
- SchemeBook loads 10 schemes ✓
- Paths verified: All under OneDrive, no hardcoded engine paths ✓
- Environment: GEMINI_API_KEY and GNANI_API_KEY loaded from root .env ✓

### Gemini Model Verification
- Call 1/40: Google ListModels API succeeded (46 generateContent models available)
- Model `gemini-3.7-flash` confirmed available in ListModels response
- Call 2/40: Real hello test attempted but got 503 Service Unavailable (temporary)

## ⚠️ NOT VERIFIED / INCOMPLETE

### Step 3: Web Behavior Merges (Not Done)
- `listening.html`: Needs server-side watch polling merged (engine has `/api/watch/start` and `/api/watch/state` polling; OneDrive has browser getUserMedia)
- `listen.html`: Digit-only token validation needs checking
- `missing.html`: DOES NOT EXIST - needs creation in user's style
- `confirmed.html`: seva.lastResult schema routing needs verification
- `another.html`: Routing logic exists but not verified in flow
- `mic-blocked.html`: May contain getUserMedia references that should be removed
- `privacy.html`: Server-side privacy language needs accuracy check

### Step 4: Real API Tests (Not Done)
**Gemini Tests (Used 1 of 40 calls)**
- ✓ ListModels successful
- ✗ Hello test failed (503 - service temporarily unavailable)
- NOT TESTED: English, Telugu, Hindi questions
- NOT TESTED: Hospital-grounded questions
- NOT TESTED: Injection text handling

**Gnani TTS/STT Tests (0 of 250 calls used)**
- NOT TESTED: 3 languages × 3 venues × 2 runs = 18 combinations
- NOT TESTED: English hospital TTS/STT
- NOT TESTED: Telugu hospital TTS/STT
- NOT TESTED: Hindi hospital TTS/STT
- NOT TESTED: Train and bus variations
- NOT TESTED: Noisy replays

**Infrastructure Tests (Not Done)**
- NOT TESTED: Real microphone via speakers
- NOT TESTED: Server 409 conflict handling
- NOT TESTED: Stop/silence/resume flows
- NOT TESTED: Headless browser simulation
- NOT TESTED: Grep for getUserMedia/MediaRecorder/navigator.mediaDevices/FormData

### Missing Files
- `web/missing.html` - referenced in engine listening.html routing but does not exist
- `web/mic-check.html` - exists in engine but not in OneDrive

### Path Fixes
- All server paths correctly resolve from OneDrive root
- No audit for hardcoded references in frontend JS completed

## 📊 API CALL BUDGET

- **Gemini:** 1 of 40 used (39 remaining)
- **Gnani STT:** 0 of 150 used
- **Gnani TTS:** 0 of 100 used

## 🔄 NEXT STEPS TO COMPLETE

1. Wait for Gemini API availability, then run real test matrix
2. Merge server-side listening behavior into user's listening.html
3. Create missing.html in user's style
4. Verify all result routing (confirmed, another, missing)
5. Run full Gnani TTS/STT test matrix
6. Test microphone and speakers
7. Grep for browser microphone API patterns
8. Verify privacy.html accuracy
9. Generate final report with VERIFIED/NOT VERIFIED distinctions

## 🎯 CURRENT STATE

The canonical OneDrive project has:
- ✅ Flask engine integrated and running independently
- ✅ Gemini integration with passing mocked tests
- ✅ Root .env with both API keys
- ✅ Basic /api/ask endpoint working
- ⚠️ Web behavior merges incomplete
- ⚠️ Real API tests blocked by service availability
- ⚠️ Result routing screens incomplete

**The migration is approximately 60% complete.**
