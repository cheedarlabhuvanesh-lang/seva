"""
listen.py — the ear.

This is the product. Everything else is a part of it.

    You tell it one thing you are waiting for.
    It listens to the hall for you.
    When your announcement happens, it tells you in Telugu.

    python -m app.listen --train 12739 --live
    python -m app.listen --token A-47  --live
    python -m app.listen --bus Hyderabad --live

    python -m app.listen --train 12739 --file platform.wav     (a recording)
    python -m app.listen --train 12739 --text "..."            (no API at all)

The pipeline:

    microphone ──▶ gate ──▶ Prisma STT ──▶ extract ──▶ watch
                    │                                    │
              free, local                           not mine ──▶ keep listening
              only pays when                            │
              something is loud                      MINE ──▶ alert ──▶ Timbre
                                                                         │
                                                                 Telugu, out loud

Three safety properties, each of which exists because of a specific failure:

  * The gate means a three-hour wait costs ~8 minutes of credit, not 180.
  * extract() refuses rather than guesses, so a number is never invented.
  * --max-calls is a hard ceiling. A runaway loop cannot drain the grant
    while you are asleep.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import gate as gate_mod          # noqa: E402
from app.alert import build_alert, alert_is_certain, screen_text  # noqa: E402
from app.extract import extract           # noqa: E402
from app.watch import Watch               # noqa: E402

SAMPLE_RATE = 16000        # Prisma takes 8k or 16k mono
FRAME_MS = 100             # gate resolution
FRAME_LEN = SAMPLE_RATE * FRAME_MS // 1000
MAX_CLIP_SECONDS = 20      # Prisma rejects anything over 60; announcements are short
PRE_ROLL_FRAMES = 3        # keep 300 ms before the trigger, so no clipped first word


# --------------------------------------------------------------------------
# station names used for destination matching
# --------------------------------------------------------------------------

def load_stations(project: Path) -> list[str]:
    """Pull every place name out of the rail and bus packs, if they exist."""
    names: set[str] = set()
    for name, key in (("rail.json", "trains"), ("bus.json", "routes")):
        path = project / "data" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for row in data.get(key, []):
            for field in ("to_station", "from_station", "to_city", "from_city"):
                if row.get(field):
                    names.add(str(row[field]))
    return sorted(names)


# --------------------------------------------------------------------------
# audio helpers
# --------------------------------------------------------------------------

def _require_numpy():
    try:
        import numpy as np
        return np
    except ImportError:
        print("numpy is needed.  pip install numpy", file=sys.stderr)
        raise SystemExit(2)


def read_wav_frames(path: Path):
    """Yield fixed-length float frames from a wav file, resampled by decimation."""
    np = _require_numpy()
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())

    if width != 2:
        raise SystemExit(f"{path.name}: need 16-bit PCM, got {width * 8}-bit")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE and rate % SAMPLE_RATE == 0:
        samples = samples[:: rate // SAMPLE_RATE]

    for start in range(0, len(samples) - FRAME_LEN, FRAME_LEN):
        yield samples[start:start + FRAME_LEN]


def write_clip(frames: list, out_path: Path) -> Path:
    """Write buffered frames to a 16 kHz mono wav Prisma will accept."""
    np = _require_numpy()
    audio = np.concatenate(frames) if frames else np.zeros(1, dtype=np.float32)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767).astype(np.int16)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return out_path


# --------------------------------------------------------------------------
# the one place an announcement is handled
# --------------------------------------------------------------------------

class Listener:
    def __init__(self, watch: Watch, client=None, *, language="te-IN",
                 stations=None, speak=True, max_calls=40, verbose=True):
        self.watch = watch
        self.client = client
        self.language = language
        self.stations = stations or []
        self.speak = speak
        self.max_calls = max_calls
        self.verbose = verbose
        self.stt_calls = 0
        self.tts_calls = 0
        self.heard = 0
        self.hits = 0

    # -- the core step ----------------------------------------------------

    def handle_text(self, text: str) -> dict:
        """
        One transcript in. Decide whether it is ours and act.

        Returns a record describing what happened, so a UI or a test can
        inspect it without reading stdout.
        """
        self.heard += 1
        slots = extract(text, self.stations)
        mine = self.watch.matches(slots)

        record = {
            "transcript": text,
            "slots": slots,
            "mine": mine,
            "alert": None,
            "certain": False,
            "audio": None,
        }

        if self.verbose:
            print(f"\n  heard   : {text.strip()[:70]}")
            print(f"  slots   : train={slots.get('train_no')} "
                  f"platform={slots.get('platform')} "
                  f"dest={slots.get('destination')}")

        if not mine:
            if self.verbose:
                print("  -> not yours, still listening")
            return record

        self.hits += 1
        alert = build_alert(slots, kind=self.watch.kind, language=self.language)
        record["alert"] = alert
        record["certain"] = alert_is_certain(slots, self.watch.kind)
        record["screen"] = screen_text(slots, self.watch.kind)

        if self.verbose:
            print("  -> THIS IS YOURS")
            print(f"  say     : {alert}")

        if self.speak and self.client is not None:
            if self.tts_calls >= self.max_calls:
                print("  (call ceiling reached, not speaking)")
                return record
            out = Path("samples") / "alerts" / f"alert-{int(time.time())}.wav"
            try:
                path = self.client.speak(alert, language=self.language,
                                         out_path=out)
                self.tts_calls += 1
                record["audio"] = str(path)
                if self.verbose:
                    print(f"  audio   : {path}")
                _play(path)
            except Exception as exc:                      # noqa: BLE001
                print(f"  (speech failed: {exc})")

        return record

    def handle_clip(self, clip: Path) -> dict | None:
        """Send one gated clip to Prisma, then handle whatever came back."""
        if self.client is None:
            return None
        if self.stt_calls >= self.max_calls:
            print("  (call ceiling reached, stopping)")
            return None
        try:
            text = self.client.transcribe(clip, language_code=self.language)
            self.stt_calls += 1
        except Exception as exc:                          # noqa: BLE001
            print(f"  (transcribe failed: {exc})")
            return None
        if not text.strip():
            return None
        return self.handle_text(text)

    def summary(self) -> str:
        return (f"announcements heard {self.heard} | yours {self.hits} | "
                f"stt {self.stt_calls} | tts {self.tts_calls}")


def _play(path: Path) -> None:
    """Best-effort playback. Never crashes the listener."""
    import subprocess
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["powershell", "-c",
                 f"(New-Object Media.SoundPlayer '{path}').PlaySync()"],
                check=False, capture_output=True, timeout=60)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", str(path)], check=False, timeout=60)
        else:
            subprocess.run(["aplay", "-q", str(path)], check=False, timeout=60)
    except Exception:                                     # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def run_file(listener: Listener, path: Path, trigger_db: float) -> None:
    """Replay a recording through the gate exactly as if it were live."""
    print(f"\nlistening to {path.name} ...")
    g = gate_mod.Gate(trigger_db=trigger_db)
    buffer: list = []
    pre: list = []
    clips = 0
    tmp = Path("samples") / "clips"

    for frame in read_wav_frames(path):
        state = g.push(frame)
        if state == "idle":
            pre.append(frame)
            pre[:] = pre[-PRE_ROLL_FRAMES:]
        elif state == "start":
            buffer = list(pre) + [frame]
        elif state == "open":
            buffer.append(frame)
            if len(buffer) * FRAME_MS / 1000 > MAX_CLIP_SECONDS:
                state = "end"
        if state == "end" and buffer:
            clips += 1
            clip = write_clip(buffer, tmp / f"clip-{clips:03d}.wav")
            seconds = len(buffer) * FRAME_MS / 1000
            print(f"\n[{clips}] announcement detected, {seconds:.1f}s")
            listener.handle_clip(clip)
            buffer = []

    if buffer:
        clips += 1
        clip = write_clip(buffer, tmp / f"clip-{clips:03d}.wav")
        print(f"\n[{clips}] announcement detected (end of file)")
        listener.handle_clip(clip)

    print(f"\n{clips} announcement(s) gated out of the whole recording.")
    print(listener.summary())


def run_live(listener: Listener, trigger_db: float, seconds: int) -> None:
    """Listen to the real microphone until Ctrl-C or the time limit."""
    np = _require_numpy()
    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice is needed for --live.  pip install sounddevice",
              file=sys.stderr)
        raise SystemExit(2)

    print(f"\n{listener.watch.describe_te()}")
    print("listening ... press Ctrl-C to stop\n")

    g = gate_mod.Gate(trigger_db=trigger_db)
    buffer: list = []
    pre: list = []
    clips = 0
    started = time.time()
    tmp = Path("samples") / "clips"

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=FRAME_LEN) as stream:
            while True:
                if seconds and time.time() - started > seconds:
                    break
                block, _ = stream.read(FRAME_LEN)
                frame = block[:, 0]
                state = g.push(frame)

                if state == "idle":
                    pre.append(frame)
                    pre[:] = pre[-PRE_ROLL_FRAMES:]
                elif state == "start":
                    print("  . announcement starting")
                    buffer = list(pre) + [frame]
                elif state == "open":
                    buffer.append(frame)
                    if len(buffer) * FRAME_MS / 1000 > MAX_CLIP_SECONDS:
                        state = "end"

                if state == "end" and buffer:
                    clips += 1
                    clip = write_clip(buffer, tmp / f"live-{clips:03d}.wav")
                    listener.handle_clip(clip)
                    buffer = []
                    if listener.stt_calls >= listener.max_calls:
                        print("\ncall ceiling reached — stopping to protect credits")
                        break
    except KeyboardInterrupt:
        print("\nstopped.")

    print(listener.summary())


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main() -> int:
    project = Path(__file__).resolve().parent.parent

    p = argparse.ArgumentParser(
        description="Listen to announcements and tell me when mine happens.")
    what = p.add_argument_group("what are you waiting for (pick one)")
    what.add_argument("--train", help="train number, or a destination city")
    what.add_argument("--token", help="hospital token, e.g. A-47")
    what.add_argument("--bus", help="destination city")

    src = p.add_argument_group("where to listen (pick one)")
    src.add_argument("--live", action="store_true", help="the microphone")
    src.add_argument("--file", help="a wav recording")
    src.add_argument("--text",
                     help="a transcript typed by hand (skips STT; still speaks)")

    p.add_argument("--language", default="te-IN")
    p.add_argument("--trigger-db", type=float, default=gate_mod.TRIGGER_DB,
                   help="how much louder than the room counts as an announcement")
    p.add_argument("--seconds", type=int, default=0,
                   help="stop live listening after N seconds (0 = until Ctrl-C)")
    p.add_argument("--max-calls", type=int, default=40,
                   help="hard ceiling on API calls, protects the free tier")
    p.add_argument("--quiet", action="store_true",
                   help="do not call Timbre and do not play audio (free dry run)")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    args = p.parse_args()

    # --- what -------------------------------------------------------
    if args.train:
        watch = Watch("train", args.train)
    elif args.token:
        watch = Watch("token", args.token)
    elif args.bus:
        watch = Watch("bus", args.bus)
    else:
        p.error("say what you are waiting for: --train, --token or --bus")

    stations = load_stations(project)

    # --- client ------------------------------------------------------
    # Always built, because even --text mode should SPEAK the alert out loud.
    # --text only skips the listening half (there is no audio to transcribe);
    # it does not mean silence. Use --quiet for a completely free dry run.
    client = None
    if not args.quiet:
        try:
            from gnani import GnaniClient
            client = GnaniClient()
        except Exception as exc:                          # noqa: BLE001
            print(f"Gnani client unavailable: {exc}", file=sys.stderr)
            if args.live or args.file:
                return 1

    listener = Listener(watch, client, language=args.language,
                        stations=stations, speak=not args.quiet,
                        max_calls=args.max_calls, verbose=not args.json)

    print(f"watching  : {watch.kind} = {watch.value}")
    if stations:
        print(f"stations  : {len(stations)} known place names loaded")

    # --- where ------------------------------------------------------
    if args.text:
        record = listener.handle_text(args.text)
        if args.json:
            print(json.dumps(record, ensure_ascii=False, indent=2))
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 1
        run_file(listener, path, args.trigger_db)
    elif args.live:
        run_live(listener, args.trigger_db, args.seconds)
    else:
        p.error("say where to listen: --live, --file or --text")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
