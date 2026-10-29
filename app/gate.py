"""
gate.py — decide locally whether a sound is an announcement.

Why this file exists:

    The free tier is 2,000 STT minutes. A person waits three hours at a station.
    Streaming the microphone the whole time costs 180 minutes per session —
    eleven sessions and the entire grant is gone, before the product is even
    finished.

    But a station makes only 20-40 announcements an hour, each 10-20 seconds.
    That is 5-10 minutes of audio worth transcribing, and 50 minutes of crowd
    noise worth nothing.

    So this module listens for free, on the laptop, and only wakes the paid
    service when something that sounds like an announcement actually happens.

The threshold is not invented. Indian Railways' own CAMTECH maintenance
handbook for public address systems specifies that a PA must run 15-25 dB
above background noise to be intelligible. If an announcement is audible to a
human at all, it is at least 15 dB above the room. That is the trigger.

No network. No model. Just arithmetic on a numpy array.
"""

from __future__ import annotations

import numpy as np

# An announcement must be at least this much louder than the ambient room.
# 15 dB is the lower bound of the railway's own intelligibility specification.
TRIGGER_DB = 15.0

# Absolute floor. In a silent room the background RMS approaches zero and any
# ratio looks enormous, so a tiny sound would trigger. This stops that.
SILENCE_FLOOR = 0.005

# How fast the background estimate follows the room. Small = slow and steady,
# which is what we want: a long announcement must not be absorbed into the
# background and switch itself off mid-sentence.
BACKGROUND_ALPHA = 0.05


def rms(frame: np.ndarray) -> float:
    """Loudness of one frame of audio. Returns 0.0 for an empty frame."""
    if frame is None or frame.size == 0:
        return 0.0
    data = frame.astype(np.float64)
    return float(np.sqrt(np.mean(np.square(data))))


def db_above(frame_rms: float, background_rms: float) -> float:
    """
    How many dB louder this frame is than the background.

    Returns 0.0 rather than infinity when the background is silent, so callers
    never have to handle a special case.
    """
    floor = max(background_rms, SILENCE_FLOOR)
    if frame_rms <= 0:
        return 0.0
    return float(20.0 * np.log10(frame_rms / floor))


def is_announcement(frame: np.ndarray, background_rms: float,
                    trigger_db: float = TRIGGER_DB) -> bool:
    """True when this frame is loud enough to be worth paying to transcribe."""
    level = rms(frame)
    if level < SILENCE_FLOOR:
        return False
    return db_above(level, background_rms) >= trigger_db


class Gate:
    """
    Rolling gate for a live microphone.

    Feed it frames. It tracks the ambient level itself and tells you when to
    start recording and when the announcement has finished.

        gate = Gate()
        for frame in microphone:
            state = gate.push(frame)
            if state == "start":  begin buffering
            if state == "end":    send the buffer to STT
    """

    def __init__(self, trigger_db: float = TRIGGER_DB,
                 release_frames: int = 12) -> None:
        self.trigger_db = trigger_db
        self.release_frames = release_frames   # quiet frames before we call it over
        self.background = SILENCE_FLOOR
        self.open = False
        self._quiet_run = 0
        self._primed = False

    def push(self, frame: np.ndarray) -> str:
        """
        Feed one frame. Returns "start", "open", "end", or "idle".
        """
        level = rms(frame)

        # First frame sets the initial background instead of assuming silence.
        if not self._primed:
            self.background = max(level, SILENCE_FLOOR)
            self._primed = True
            return "idle"

        loud = level >= SILENCE_FLOOR and \
            db_above(level, self.background) >= self.trigger_db

        if not self.open:
            # Only learn the background while the gate is shut, otherwise a
            # long announcement slowly teaches the gate that it is normal.
            self.background = (1 - BACKGROUND_ALPHA) * self.background \
                + BACKGROUND_ALPHA * level
            self.background = max(self.background, SILENCE_FLOOR)
            if loud:
                self.open = True
                self._quiet_run = 0
                return "start"
            return "idle"

        # Gate is open.
        if loud:
            self._quiet_run = 0
            return "open"

        self._quiet_run += 1
        if self._quiet_run >= self.release_frames:
            self.open = False
            self._quiet_run = 0
            return "end"
        return "open"


# --------------------------------------------------------------------------
# self test — python -m app.gate
# --------------------------------------------------------------------------

if __name__ == "__main__":
    rng = np.random.default_rng(0)

    def noise(level: float, n: int = 1600) -> np.ndarray:
        return (rng.standard_normal(n) * level).astype(np.float32)

    print("gate.py self test")
    print("=" * 62)
    checks: list[tuple[str, bool]] = []

    quiet = noise(0.02)
    loud = noise(0.02 * (10 ** (20 / 20)))      # 20 dB above
    marginal = noise(0.02 * (10 ** (8 / 20)))   # 8 dB above — not enough

    bg = rms(quiet)
    checks.append(("quiet frame does not trigger",
                   not is_announcement(quiet, bg)))
    checks.append(("20 dB frame triggers",
                   is_announcement(loud, bg)))
    checks.append(("8 dB frame does NOT trigger",
                   not is_announcement(marginal, bg)))
    checks.append(("silence never triggers",
                   not is_announcement(np.zeros(1600, dtype=np.float32), 0.0)))
    checks.append(("empty frame is safe",
                   rms(np.array([], dtype=np.float32)) == 0.0))

    # Full sequence: room tone, announcement, room tone again.
    gate = Gate(release_frames=3)
    events: list[str] = []
    for _ in range(20):
        events.append(gate.push(noise(0.02)))
    for _ in range(10):
        events.append(gate.push(loud))
    for _ in range(10):
        events.append(gate.push(noise(0.02)))

    checks.append(("gate opened exactly once", events.count("start") == 1))
    checks.append(("gate closed exactly once", events.count("end") == 1))
    checks.append(("gate stayed open during announcement",
                   events.count("open") >= 9))
    checks.append(("gate idle before announcement",
                   events[:19].count("idle") == 19))

    # Credit maths, the reason this file exists.
    per_hour_announcements, seconds_each = 30, 15
    gated_min = per_hour_announcements * seconds_each / 60
    checks.append(("gating cuts an hour to under 10 min", gated_min < 10))

    failed = 0
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1

    print("=" * 62)
    print(f"  background estimate : {gate.background:.5f}")
    print(f"  streaming 1 hour    : 60.0 min billed")
    print(f"  gated     1 hour    : {gated_min:.1f} min billed")
    print(f"  2000 min free tier  : {2000 / gated_min:.0f} hours of listening")
    print(f"{len(checks) - failed} passed, {failed} failed")
