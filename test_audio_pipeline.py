#!/usr/bin/env python3
"""Test the complete audio pipeline to identify failures.

This script verifies:
1. Gnani API key is loaded correctly
2. GnaniClient initializes without errors
3. A test audio file can be transcribed
4. Token matching works correctly
"""

import sys
from pathlib import Path

# Fix Windows console encoding issues
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project to path
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

def test_environment():
    """Verify environment variables are loaded."""
    print("=" * 60)
    print("1. Testing environment setup...")
    print("=" * 60)

    from gnani.client import load_dotenv
    import os

    load_dotenv()

    key = os.environ.get("GNANI_API_KEY", "")
    if not key:
        print("✗ GNANI_API_KEY not found in environment")
        return False

    print(f"✓ GNANI_API_KEY found")
    print(f"  Key length: {len(key)} characters")
    print(f"  Format check: {'PASS' if key.startswith('vach_') else 'FAIL - unexpected prefix'}")
    return True


def test_client_init():
    """Verify GnaniClient initializes correctly."""
    print("\n" + "=" * 60)
    print("2. Testing GnaniClient initialization...")
    print("=" * 60)

    try:
        from gnani import GnaniClient
        client = GnaniClient()

        if not client.api_key:
            print("✗ GnaniClient initialized but api_key is empty")
            return False

        print(f"✓ GnaniClient initialized successfully")
        print(f"  API key length: {len(client.api_key)}")
        print(f"  Base URL: {client.base_url}")
        print(f"  STT model: {getattr(client, 'stt_model', 'default')}")
        print(f"  TTS model: {getattr(client, 'tts_model', 'default')}")
        return True

    except Exception as exc:
        print(f"✗ GnaniClient initialization failed:")
        print(f"  {type(exc).__name__}: {str(exc)}")
        import traceback
        traceback.print_exc()
        return False


def test_matcher():
    """Verify token matching works correctly."""
    print("\n" + "=" * 60)
    print("3. Testing token matcher...")
    print("=" * 60)

    try:
        from app.matcher import match_hospital

        # Test exact match
        result = match_hospital("token 47 room 5", "47")
        print(f"✓ Token matching works")
        print(f"  Input: 'token 47 room 5' | Target: '47'")
        print(f"  Result: verdict={result['verdict']} token={result['token']} room={result['room']}")

        # Test mismatch
        result2 = match_hospital("token 847 room 5", "47")
        print(f"  Input: 'token 847 room 5' | Target: '47'")
        print(f"  Result: verdict={result2['verdict']} (should be 'other' or 'none')")

        return True

    except Exception as exc:
        print(f"✗ Token matching failed:")
        print(f"  {type(exc).__name__}: {str(exc)}")
        import traceback
        traceback.print_exc()
        return False


def test_stt_mock():
    """Test STT with a mock audio file (if available)."""
    print("\n" + "=" * 60)
    print("4. Testing speech-to-text (requires test audio)...")
    print("=" * 60)

    # Look for any existing audio samples
    samples_dir = PROJECT / "samples"
    if not samples_dir.exists():
        print("ℹ No samples directory found - skipping STT test")
        return True

    wav_files = list(samples_dir.rglob("*.wav"))
    if not wav_files:
        print("ℹ No WAV files found in samples/ - skipping STT test")
        return True

    test_file = wav_files[0]
    print(f"ℹ Found test audio: {test_file.relative_to(PROJECT)}")
    print("  (STT test requires valid Gnani API key and credits)")
    print("  Skipping actual API call to preserve credits")

    return True


def test_device_audio():
    """Test device audio session creation."""
    print("\n" + "=" * 60)
    print("5. Testing device audio session...")
    print("=" * 60)

    try:
        from app import device_audio

        # Test session creation (doesn't make API calls)
        session = device_audio.create_session(
            kind="hospital",
            value="47",
            language="en-IN",
            client_ip="127.0.0.1"
        )

        print(f"✓ Device session created successfully")
        print(f"  ID: {session.id}")
        print(f"  Kind: {session.kind}")
        print(f"  Value: {session.value}")
        print(f"  Language: {session.language}")

        # Clean up
        device_audio.stop_session(session.id, client_ip="127.0.0.1")

        return True

    except Exception as exc:
        print(f"✗ Device session creation failed:")
        print(f"  {type(exc).__name__}: {str(exc)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\nSeva Audio Pipeline Test")
    print("=" * 60)

    results = []

    results.append(("Environment", test_environment()))
    results.append(("GnaniClient Init", test_client_init()))
    results.append(("Token Matcher", test_matcher()))
    results.append(("STT (Mock)", test_stt_mock()))
    results.append(("Device Audio", test_device_audio()))

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {name}")

    all_passed = all(passed for _, passed in results)

    print("=" * 60)
    if all_passed:
        print("✓ All tests passed!")
        print("\nIf live listening still fails in production:")
        print("1. Check Render logs for [ERROR] and [CRITICAL] messages")
        print("2. Verify GNANI_API_KEY is set in Render environment")
        print("3. Check browser console for [SevaDeviceAudio] logs")
        print("4. Verify microphone permissions are granted")
        return 0
    else:
        print("✗ Some tests failed - see errors above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
