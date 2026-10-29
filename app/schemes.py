"""Scheme knowledge base + keyword retrieval.

Deliberately not embeddings. Keyword scoring over a curated Telugu/English
keyword list is instant, costs nothing, needs no extra model, and is easy to
debug when a judge asks "why did it pick that scheme?".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "hospital.json"

# Telugu has no capital letters and few word boundaries we can rely on, so we
# match on substrings rather than tokens.
_PUNCT = re.compile(r"[^\w\u0C00-\u0C7F]+")


def _normalise(text: str) -> str:
    return _PUNCT.sub(" ", text.lower()).strip()


class SchemeBook:
    def __init__(self, path: Path | str = DATA_FILE) -> None:
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.schemes: list[dict] = payload["schemes"]
        self.last_checked: str = payload.get("last_checked", "unknown")
        self._by_id = {scheme["id"]: scheme for scheme in self.schemes}

    def __len__(self) -> int:
        return len(self.schemes)

    def get(self, scheme_id: str) -> dict | None:
        return self._by_id.get(scheme_id)

    def score(self, question: str, scheme: dict) -> int:
        """How well does this scheme answer the question?"""
        asked = _normalise(question)
        points = 0
        for keyword in scheme["keywords"]:
            if _normalise(keyword) in asked:
                # Longer keywords are more specific, so weight them higher.
                points += 2 if len(keyword) > 6 else 1
        if _normalise(scheme["name_te"]) in asked:
            points += 5
        if _normalise(scheme["name_en"]) in asked:
            points += 5
        return points

    def search(self, question: str, limit: int = 2) -> list[dict]:
        """Best-matching schemes, highest score first. Empty if nothing matches."""
        ranked = sorted(
            ((self.score(question, s), s) for s in self.schemes),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [scheme for points, scheme in ranked[:limit] if points > 0]

    def as_context(self, schemes: list[dict]) -> str:
        """Format schemes as grounding text for the model."""
        blocks = []
        for scheme in schemes:
            blocks.append(
                f"పథకం పేరు: {scheme['name_te']} ({scheme['name_en']})\n"
                f"ఎవరు అర్హులు: {scheme['who_te']}\n"
                f"ఏమి వస్తుంది: {scheme['benefit_te']}\n"
                f"ఎలా దరఖాస్తు చేయాలి: {scheme['how_te']}"
            )
        return "\n\n".join(blocks)
