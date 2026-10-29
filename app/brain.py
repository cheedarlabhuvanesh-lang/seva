"""The thinking half: local Ollama, grounded on the scheme book.

Grounding matters more than model size here. A 4B model that is handed the
exact scheme text will out-answer a 70B model guessing from memory, and it
cannot invent a benefit amount that does not exist.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

SYSTEM = """నువ్వు ఆసుపత్రి సహాయ కేంద్రంలో పనిచేసే సహాయకుడివి. రోగులకు, వారి కుటుంబ సభ్యులకు సహాయం చేస్తావు.

నియమాలు:
1. కింద ఇచ్చిన ఆసుపత్రి సమాచారం ఆధారంగా మాత్రమే జవాబు చెప్పు. డాక్టర్ పేరు, గది నంబర్, సమయం, ఫోన్ నంబర్ కల్పించి చెప్పకు.
2. సరళమైన తెలుగులో, మూడు చిన్న వాక్యాల్లో చెప్పు.
3. ఎక్కడికి వెళ్ళాలి, ఏ అంతస్తు, ఏమి తీసుకురావాలి అనేవి స్పష్టంగా చెప్పు.
4. సమాచారం లేకపోతే, ఆ విషయం నాకు తెలియదు, రిసెప్షన్‌లో అడగండి అని చెప్పు.
5. ఇది మాట్లాడే జవాబు. బుల్లెట్ పాయింట్లు, గుర్తులు వాడకు.
6. వైద్య సలహా ఇవ్వకు. మందులు, చికిత్స గురించి అడిగితే డాక్టర్‌ను కలవమని చెప్పు."""

NO_MATCH = (
    "ఆ విషయం గురించి నా దగ్గర సమాచారం లేదు. "
    "దయచేసి రిసెప్షన్ కౌంటర్‌లో అడిగి తెలుసుకోండి."
)


class BrainError(RuntimeError):
    pass


def ask(model: str, question: str, context: str, timeout: int = 180) -> str:
    """Answer `question` using only `context`. Returns spoken-style Telugu."""
    if not context.strip():
        return NO_MATCH

    user = (
        f"పథకాల సమాచారం:\n{context}\n\n"
        f"ప్రజల ప్రశ్న: {question}\n\n"
        "పైన ఉన్న సమాచారం ఆధారంగా తెలుగులో జవాబు చెప్పు."
    )
    body = json.dumps({
        "model": model,
        "stream": False,
        "options": {"temperature": 0.3},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")

    request = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise BrainError(
            f"Could not reach Ollama at {OLLAMA_URL} ({exc}). Is the Ollama app running?"
        ) from exc

    reply = (payload.get("message") or {}).get("content", "").strip()
    return clean_for_speech(reply) or NO_MATCH


def add_expression(text: str, worried: bool = False) -> str:
    """Add Timbre v3.0 audio tags so the delivery matches the moment.

    Deliberately conservative. A hospital line that giggles is worse than a
    flat one, so only the calming tags are ever used, and only two per answer:
      <slow>   for numbers, times and room locations people write down
      <pauses> between a reassurance and the instruction that follows

    A tag runs to the end of the input unless another follows it, so <slow>
    is placed on the sentence carrying the detail - not at the start.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[।.?!])\s+", text) if s.strip()]
    if not sentences:
        return text

    if worried and len(sentences) > 1:
        # Reassure, let it land, then instruct.
        sentences[0] = sentences[0] + " <pauses>"

    # Slow down whichever sentence carries a time, number or room.
    for i, sentence in enumerate(sentences):
        if re.search(r"\d|గంట|బ్లాక్|అంతస్తు|बजे|मंजिल|floor|block|a\.m|p\.m", sentence):
            sentences[i] = "<slow> " + sentence
            break

    return " ".join(sentences)


WORRY_WORDS = (
    "emergency", "urgent", "pain", "serious", "afraid", "scared", "worried",
    "అత్యవసర", "నొప్పి", "భయం", "సీరియస్", "ప్రమాద",
    "आपातकाल", "दर्द", "डर", "गंभीर",
)


def sounds_worried(question: str) -> bool:
    """Cheap intent check - no extra model call, no extra latency."""
    lowered = question.lower()
    return any(word in lowered for word in WORRY_WORDS)


def clean_for_speech(text: str) -> str:
    """Strip anything that sounds wrong when read aloud."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = line.lstrip("*-•#").strip()
        # Drop a leading "1." / "2)" list marker.
        while line[:1].isdigit() and line[1:2] in {".", ")"}:
            line = line[2:].strip()
        if line:
            lines.append(line)
    joined = " ".join(lines)
    for junk in ("**", "__", "`", "#"):
        joined = joined.replace(junk, "")
    return " ".join(joined.split())
