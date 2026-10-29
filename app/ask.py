#!/usr/bin/env python3
"""One command, the whole helpline. Built for an agent to call.

    python -m app.ask "రైతులకు ఏ పథకం ఉంది?"
    python -m app.ask --listen 6
    python -m app.ask "..." --json --no-play

Why this exists: an agent that has to chain four separate tools (record,
transcribe, think, speak) gets it wrong sometimes - wrong order, wrong flag,
wrong file path. One deterministic command cannot be chained wrongly.

Always exits 0 with `{"ok": false, "error": ...}` on failure rather than
raising, so the caller never has to parse a stack trace.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.brain import BrainError, add_expression, ask as think, sounds_worried  # noqa: E402
from app.schemes import SchemeBook  # noqa: E402
from gnani import GnaniClient, GnaniError  # noqa: E402

OUT_DIR = ROOT / "samples" / "helpline"


def record(seconds: float, path: Path) -> None:
    """Capture mono 16 kHz - exactly the format Prisma wants, no conversion."""
    import wave

    import sounddevice as sd

    rate = 16000
    print(f"listening {seconds:g}s ...", file=sys.stderr)
    frames = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
    sd.wait()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="Telugu government-scheme helpline")
    parser.add_argument("question", nargs="?", help="question in Telugu or English")
    parser.add_argument("--listen", type=float, metavar="SECONDS",
                        help="record from the microphone instead of taking text")
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--voice", default="Chetana",
                        help="Timbre v3.0 voice. Chetana = healthcare persona.")
    parser.add_argument("--out", default=None, help="where to write the spoken answer")
    parser.add_argument("--no-play", action="store_true", help="do not play the audio")
    parser.add_argument("--no-speak", action="store_true", help="text answer only, no TTS")
    parser.add_argument("--plain", action="store_true",
                        help="disable Timbre v3.0 audio tags")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    result: dict = {"ok": False}
    timings: dict[str, int] = {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        client = GnaniClient()
        book = SchemeBook()

        # 1. ears
        if args.listen:
            wav = OUT_DIR / f"q-{uuid.uuid4().hex[:8]}.wav"
            record(args.listen, wav)
            started = time.time()
            question = client.transcribe(wav, language_code="te-IN")
            timings["hear_ms"] = int((time.time() - started) * 1000)
            if not question.strip():
                raise GnaniError("Nothing was recognised. Speak closer to the microphone.")
        elif args.question:
            question = args.question
        else:
            parser.error("give a question, or use --listen SECONDS")

        # 2. lookup + thinking
        matches = book.search(question)
        started = time.time()
        answer = think(args.model, question, book.as_context(matches))
        timings["think_ms"] = int((time.time() - started) * 1000)

        # Timbre v3.0 only: direct the delivery with inline audio tags.
        spoken = answer
        if not args.plain:
            spoken = add_expression(answer, worried=sounds_worried(question))

        result.update({
            "ok": True,
            "question": question,
            "answer": answer,
            "schemes": [{"id": s["id"], "name": s["name_te"], "url": s["url"]}
                        for s in matches],
            "grounded": bool(matches),
        })

        # 3. mouth
        if not args.no_speak:
            out = Path(args.out) if args.out else OUT_DIR / f"a-{uuid.uuid4().hex[:8]}.wav"
            started = time.time()
            client.speak(spoken, language="te-IN", voice=args.voice,
                         sample_rate=16000, out_path=out)
            timings["speak_ms"] = int((time.time() - started) * 1000)
            result["audio"] = str(out)
            result["spoken_text"] = spoken
            if not args.no_play:
                sys.path.insert(0, str(ROOT / "scripts"))
                from tts import play
                play(out)

        result["timings"] = timings
        result["usage"] = client.usage_summary()

    except (GnaniError, BrainError, OSError, ImportError) as exc:
        result["error"] = str(exc)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print(f"\nQ: {result['question']}")
        print(f"A: {result['answer']}")
        if result["schemes"]:
            print("   " + " · ".join(f"{s['name']} {s['url']}" for s in result["schemes"]))
        else:
            print("   (no scheme matched - gave the safe fallback answer)")
        if result.get("audio"):
            print(f"   audio: {result['audio']}")
        print(f"   {timings}")
    else:
        print(f"error: {result['error']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
