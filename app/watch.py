"""
watch.py — what the user is waiting for.

The user declares one thing at the start:

    "my token is 47"            -> Watch("token", "47")
    "I'm going to Hyderabad"    -> Watch("bus", "Hyderabad")
    "train 12739"               -> Watch("train", "12739")

Every announcement is then checked against that one thing. Everything else in
the hall is somebody else's business.

Matching rule, in priority order:

    1. Exact identifier match  — train number, token. Never fuzzy. A digit is
       either right or it is not.
    2. Fuzzy place-name match  — only for destinations, because STT mangles
       place names constantly ("hydrabad", "hyderbad", "haidarabad") while it
       rarely mangles a digit into another valid digit string.

That split is deliberate. Being loose about place names costs the user a
glance at the board. Being loose about numbers sends them to the wrong
platform.
"""

from __future__ import annotations

from difflib import SequenceMatcher

# Standard library only — no pip install, so this runs anywhere Python runs.

#: Two place names this similar are treated as the same place.
FUZZY_THRESHOLD = 0.80

KINDS = ("train", "token", "bus")


def similarity(a: str, b: str) -> float:
    """0.0 to 1.0. Case and surrounding spaces are ignored."""
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def fuzzy_equal(a: str, b: str, threshold: float = FUZZY_THRESHOLD) -> bool:
    """True when two place names are close enough to be the same place."""
    if not a or not b:
        return False
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l == b_l:
        return True
    # A short string inside a longer one, e.g. "hyderabad" in "hyderabad decan".
    if len(a_l) >= 5 and len(b_l) >= 5 and (a_l in b_l or b_l in a_l):
        return True
    return similarity(a_l, b_l) >= threshold


class Watch:
    """One thing the user is waiting for."""

    def __init__(self, kind: str, value: str, label: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        if not value or not str(value).strip():
            raise ValueError("a watch needs something to watch for")
        self.kind = kind
        self.value = str(value).strip()
        if kind in ("train", "token") and (not self.value.isascii() or not self.value.isdigit()):
            raise ValueError(f"{kind} watch values must contain digits only")
        self.label = label or self.value

    # -- matching ---------------------------------------------------------

    def matches(self, slots: dict) -> bool:
        """True when this announcement is the one the user is waiting for."""
        if not slots:
            return False

        if self.kind == "train":
            return self._match_train(slots)
        if self.kind == "token":
            return self._match_token(slots)
        if self.kind == "bus":
            return self._match_place(slots)
        return False

    def _match_train(self, slots: dict) -> bool:
        train_no = slots.get("train_no")

        # A number was heard. It decides the answer either way — if the
        # announcement names a different train, this is not our announcement,
        # even if the destination happens to match.
        if train_no:
            return str(train_no) == self.value

        return False

    def _match_token(self, slots: dict) -> bool:
        token = slots.get("token")
        if not token or not str(token).isdigit() or not self.value.isdigit():
            return False
        return str(token) == self.value

    def _match_place(self, slots: dict) -> bool:
        destination = slots.get("destination")
        if not destination:
            return False
        return fuzzy_equal(self.value, str(destination))

    # -- plumbing ---------------------------------------------------------

    def __repr__(self) -> str:
        return f"Watch({self.kind!r}, {self.value!r})"

    def describe_te(self) -> str:
        """One short Telugu line for the UI: what we are listening for."""
        if self.kind == "train":
            return f"రైలు {self.value} కోసం వింటున్నాను"
        if self.kind == "token":
            return f"టోకెన్ {self.value} కోసం వింటున్నాను"
        return f"{self.value} బస్సు కోసం వింటున్నాను"


# --------------------------------------------------------------------------
# self test — python -m app.watch
# --------------------------------------------------------------------------

if __name__ == "__main__":
    print("watch.py self test")
    print("=" * 70)

    CASES = [
        # (watch, slots, expected, why)
        (Watch("train", "12739"),
         {"train_no": "12739", "platform": "4", "destination": "Hyderabad"},
         True, "exact train number"),

        (Watch("train", "12739"),
         {"train_no": "17201", "platform": "2", "destination": "Hyderabad"},
         False, "different train, same destination -> NOT ours"),

        (Watch("train", "12739"),
         {"train_no": None, "platform": None, "destination": None},
         False, "nothing heard at all"),

        (Watch("token", "47"),
         {"token": "47"}, True, "exact numeric token"),

        (Watch("token", "47"),
         {"token": "847"}, False, "longer token is not ours"),

        (Watch("token", "47"),
         {"token": "41"}, False, "one digit different -> NOT ours"),

        (Watch("token", "047"),
         {"token": "47"}, False, "leading zero is significant"),

        (Watch("bus", "Vijayawada"),
         {"destination": "vijaywada"}, True, "bus destination fuzzy"),

        (Watch("bus", "Vijayawada"),
         {"destination": "Visakhapatnam"}, False, "different city"),
    ]

    failed = 0
    for watch, slots, want, why in CASES:
        got = watch.matches(slots)
        ok = got == want
        failed += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {str(watch):<28} -> "
              f"{str(got):<5} ({why})")

    # The number rule must never be loosened.
    strict = Watch("train", "12739")
    for near in ("12738", "1273", "127390", "12749"):
        ok = not strict.matches({"train_no": near})
        failed += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  near-miss {near} rejected")

    print("=" * 70)
    print(f"{len(CASES) + 4 - failed} passed, {failed} failed")
