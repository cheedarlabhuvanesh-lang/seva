"""
batch.py — run a whole folder of recordings through the ear in one command.

Written for one job: you come home from the station with thirty wav files on a
phone, and you need to know, before you trust this thing on a real journey,
how many of them it actually heard correctly.

    python -m app.batch --dir samples\\station --train 12739
    python -m app.batch --dir samples\\station --token A-47 --quiet

--quiet transcribes nothing and speaks nothing; it replays the gate only, so
you can see which files contain a detectable announcement for free before
spending a single credit.

Nothing here re-implements extraction, gating or alerting. It drives the same
Listener that --live and --file drive, so a result seen here is a result the
live app would produce.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import gate as gate_mod
from app.listen import (
    FRAME_MS,
    MAX_CLIP_SECONDS,
    PRE_ROLL_FRAMES,
    Listener,
    load_stations,
    read_wav_frames,
    write_clip,
)
from app.watch import Watch

PROJECT = Path(__file__).resolve().parents[1]
REPORT = Path("samples") / "batch-report.txt"

COLUMNS = ("file", "clip", "train", "plat", "destination", "token", "room",
           "mine", "alert")
WIDTHS = (26, 5, 7, 5, 18, 7, 5, 5, 10)


# --------------------------------------------------------------------------
# one file
# --------------------------------------------------------------------------

def gate_file(path: Path, trigger_db: float) -> tuple[list[Path], float]:
    """
    Replay one recording through the energy gate and return the clips it
    isolated, plus the total seconds of audio inside them.

    This is the step that decides what gets paid for. Everything the gate
    rejects is silence or room noise and never reaches the network.
    """
    gate = gate_mod.Gate(trigger_db=trigger_db)
    tmp = Path("samples") / "clips" / path.stem
    clips: list[Path] = []
    buffer: list = []
    pre: list = []
    seconds = 0.0

    for frame in read_wav_frames(path):
        state = gate.push(frame)
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
            seconds += len(buffer) * FRAME_MS / 1000
            clips.append(write_clip(
                buffer, tmp / f"clip-{len(clips) + 1:03d}.wav"))
            buffer = []
            pre = []

    if buffer:                       # recording ended while still loud
        seconds += len(buffer) * FRAME_MS / 1000
        clips.append(write_clip(buffer, tmp / f"clip-{len(clips) + 1:03d}.wav"))

    return clips, seconds


# --------------------------------------------------------------------------
# table
# --------------------------------------------------------------------------

def _cell(value, width: int) -> str:
    # `value in (None, "", False)` would swallow a legitimate 0
    text = "-" if value is None or value is False or value == "" else str(value)
    if len(text) > width:
        text = text[:width - 1] + "…"
    return text.ljust(width)


def header() -> list[str]:
    line = "  ".join(_cell(c, w) for c, w in zip(COLUMNS, WIDTHS))
    return [line, "-" * len(line)]


def row(name: str, clip_no, slots: dict | None, mine, alert_state: str) -> str:
    slots = slots or {}
    values = (name, clip_no, slots.get("train_no"), slots.get("platform"),
              slots.get("destination"), slots.get("token"), slots.get("room"),
              "YES" if mine else "no", alert_state)
    return "  ".join(_cell(v, w) for v, w in zip(values, WIDTHS))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m app.batch",
        description="Run a folder of recordings through the ear.")
    p.add_argument("--dir", required=True,
                   help="folder of .wav recordings")

    what = p.add_argument_group("what are you waiting for (pick one)")
    what.add_argument("--train", help="train number, or a destination city")
    what.add_argument("--token", help="hospital token, e.g. A-47")
    what.add_argument("--bus", help="destination city")

    p.add_argument("--language", default="te-IN")
    p.add_argument("--trigger-db", type=float, default=gate_mod.TRIGGER_DB,
                   help="how far above background counts as an announcement")
    p.add_argument("--max-calls", type=int, default=60,
                   help="hard ceiling on network calls for the whole run")
    p.add_argument("--quiet", action="store_true",
                   help="gate only: no transcription, no speech, no credits")
    args = p.parse_args()

    if args.train:
        watch = Watch("train", args.train)
    elif args.token:
        watch = Watch("token", args.token)
    elif args.bus:
        watch = Watch("bus", args.bus)
    else:
        p.error("say what you are waiting for: --train, --token or --bus")

    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"no such folder: {folder}", file=sys.stderr)
        return 1

    files = sorted(folder.glob("*.wav"))
    if not files:
        print(f"no .wav files in {folder}", file=sys.stderr)
        return 1

    client = None
    if not args.quiet:
        try:
            from gnani import GnaniClient
            client = GnaniClient()
        except Exception as exc:                          # noqa: BLE001
            print(f"Gnani client unavailable: {exc}", file=sys.stderr)
            return 1

    listener = Listener(watch, client, language=args.language,
                        stations=load_stations(PROJECT), speak=False,
                        max_calls=args.max_calls, verbose=False)

    lines = [f"watching  : {watch.kind} = {watch.value}",
             f"folder    : {folder}   ({len(files)} files)",
             f"mode      : {'gate only, no credits' if args.quiet else 'full pipeline'}",
             ""]
    lines += header()

    unreadable = empty = clip_total = matched = 0
    audio_seconds = 0.0
    hits: list[tuple[str, dict]] = []

    for path in files:
        try:
            clips, seconds = gate_file(path, args.trigger_db)
        except Exception as exc:                          # noqa: BLE001
            unreadable += 1
            lines.append(row(path.name, "-", None, False, "unreadable"))
            lines.append(f"      {type(exc).__name__}: {exc}")
            continue

        if not clips:
            empty += 1
            lines.append(row(path.name, 0, None, False, "silence"))
            continue

        audio_seconds += seconds
        clip_total += len(clips)

        for number, clip in enumerate(clips, 1):
            name = path.name if number == 1 else ""
            if args.quiet:
                lines.append(row(name, number, None, False, "gated"))
                continue

            record = listener.handle_clip(clip)
            if record is None:
                lines.append(row(name, number, None, False, "no text"))
                continue

            mine = record.get("mine")
            state = "-"
            if mine:
                matched += 1
                state = "ready" if record.get("certain") else "uncertain"
                hits.append((path.name, record))
            lines.append(row(name, number, record.get("slots"), mine, state))

    # -- speak only the matches, and only after the whole folder is known --
    spoken = 0
    if hits and client is not None:
        lines += ["", "matches:"]
        for name, record in hits:
            lines.append(f"  {name}: {record['alert']}")
        out_dir = Path("samples") / "alerts"
        for index, (_, record) in enumerate(hits, 1):
            if listener.tts_calls >= args.max_calls:
                lines.append("  (call ceiling reached, not speaking the rest)")
                break
            try:
                out = client.speak(
                    record["alert"], language=args.language,
                    out_path=out_dir / f"batch-{index:03d}.wav")
                listener.tts_calls += 1
                spoken += 1
                lines.append(f"  spoken -> {out}")
            except Exception as exc:                      # noqa: BLE001
                lines.append(f"  speech failed: {exc}")
                break

    lines += [
        "",
        "=" * 60,
        f"files processed    : {len(files)}",
        f"unreadable         : {unreadable}",
        f"no announcement    : {empty}",
        f"clips gated        : {clip_total}",
        f"audio sent to STT  : {audio_seconds / 60:.1f} min"
        f"{'  (none - quiet mode)' if args.quiet else ''}",
        f"matched your watch : {matched}",
        f"alerts spoken      : {spoken}",
        f"stt calls {listener.stt_calls} | tts calls {listener.tts_calls}",
        "=" * 60,
    ]

    text = "\n".join(lines)
    print(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(f"\nreport written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
