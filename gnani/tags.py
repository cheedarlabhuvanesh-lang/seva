"""Timbre v3.0 audio tags — inline expression control.

Source: https://docs.gnani.ai/api/TTS/tts-audio-tags

The rules that bite, in order of how often they bite:

1. Tags are `<angle brackets>`, not `[square]`. Case-sensitive, exact match.
2. A tag applies from its position to the END of the input, or until the next
   tag. There is no closing tag. This surprises everyone once.
3. Unknown tags are stripped silently — no error, just no effect. So a typo
   like `<whisper>` (the real tag is `<whispers>`) fails quietly. We validate
   up front instead.
4. Tags only work on `timbre-v3.0`. On `timbre-v2.5` they are stripped before
   synthesis, and text containing ONLY tags is rejected with 400.
"""

from __future__ import annotations

import re

TAG_PATTERN = re.compile(r"<([^<>]{1,24})>")

# tag -> (category, what it does)
TAGS: dict[str, tuple[str, str]] = {
    "laugh":         ("Laughter", "Full laugh"),
    "giggle":        ("Laughter", "Light, high-pitched giggle"),
    "sigh":          ("Breath",   "Audible sigh"),
    "exhales":       ("Breath",   "Audible exhale"),
    "sniffles":      ("Breath",   "Sniffling"),
    "yawns":         ("Breath",   "Yawning"),
    "coughs":        ("Breath",   "Coughing"),
    "clears throat": ("Breath",   "Throat-clearing"),
    "whispers":      ("Vocal",    "Hushed delivery"),
    "shouting":      ("Vocal",    "Loud, raised voice"),
    "excited":       ("Vocal",    "Energetic, enthusiastic delivery"),
    "slow":          ("Pacing",   "Drawn-out delivery"),
    "pauses":        ("Silence",  "Short silence - never spoken aloud"),
}

# Tags worth using when a person is worried, confused, or elderly. Shouting and
# giggling have no place in a hospital.
EMPATHY_TAGS = ("slow", "pauses", "whispers", "sigh", "exhales")


def find(text: str) -> list[str]:
    """Every tag-shaped token in the text, in order, including invalid ones."""
    return TAG_PATTERN.findall(text)


def unknown(text: str) -> list[str]:
    """Tag-shaped tokens that Timbre will silently throw away. Usually typos."""
    return [name for name in find(text) if name not in TAGS]


def strip(text: str) -> str:
    """Remove every tag. Use before sending to timbre-v2.5, or before display."""
    return " ".join(TAG_PATTERN.sub(" ", text).split())


def is_only_tags(text: str) -> bool:
    """True when nothing but tags - timbre-v2.5 rejects this with 400."""
    return bool(text.strip()) and not strip(text)


def validate(text: str, strict: bool = True) -> list[str]:
    """Check tags before spending a credit on a call that will disappoint.

    Returns the list of unknown tags. With strict=True, raises instead.
    """
    bad = unknown(text)
    if bad and strict:
        near = {t.lower().rstrip("s") for t in TAGS}
        hints = []
        for name in bad:
            stem = name.lower().rstrip("s")
            if stem in near:
                match = next(t for t in TAGS if t.lower().rstrip("s") == stem)
                hints.append(f"<{name}> -> did you mean <{match}>?")
            else:
                hints.append(f"<{name}>")
        raise ValueError(
            "Unknown audio tag(s): " + "; ".join(hints) +
            ". Timbre strips these silently. Valid tags: " +
            ", ".join(f"<{t}>" for t in TAGS)
        )
    return bad


def describe() -> str:
    """Human-readable catalogue, grouped by category."""
    lines, seen = [], []
    for name, (category, effect) in TAGS.items():
        if category not in seen:
            seen.append(category)
            lines.append(f"\n{category}")
        lines.append(f"  <{name}>".ljust(20) + effect)
    return "\n".join(lines).strip()
