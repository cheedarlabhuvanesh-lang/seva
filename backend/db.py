"""Seva database (SQLite, built into Python: nothing to install).
faq.csv is the source of truth (edit it in Excel). seva.db is rebuilt from it, so a fresh server always starts correctly.
No audio, no transcripts and no personal data are stored here."""
import csv
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("SEVA_DB", os.path.join(HERE, "seva.db"))
CSV_PATH = os.path.join(HERE, "faq.csv")
VENUES = ("hospital", "train", "bus", "all")


def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def import_csv(c, path=CSV_PATH):
    """Replace all FAQ rows with the contents of the CSV file. Returns how many rows were loaded."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            venue = (r.get("venue") or "").strip().lower()
            if venue not in VENUES:
                raise ValueError(f"faq.csv line {n}: venue must be one of {', '.join(VENUES)}")
            keys = "|".join(k.strip().lower() for k in (r.get("keywords") or "").split("|") if k.strip())
            te, en = (r.get("answer_te") or "").strip(), (r.get("answer_en") or "").strip()
            if not keys or not te or not en:
                raise ValueError(f"faq.csv line {n}: keywords, answer_te and answer_en are all required")
            rows.append((venue, keys, te, en, 0 if str(r.get("is_sample", "1")).strip() == "0" else 1))
    c.execute("DELETE FROM faq")
    c.executemany("INSERT INTO faq(venue,keywords,answer_te,answer_en,is_sample) VALUES (?,?,?,?,?)", rows)
    return len(rows)


def init():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS faq(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT NOT NULL CHECK (venue IN ('hospital','train','bus','all')),
            keywords TEXT NOT NULL,
            answer_te TEXT NOT NULL,
            answer_en TEXT NOT NULL,
            is_sample INTEGER NOT NULL DEFAULT 1)""")
        if c.execute("SELECT COUNT(*) FROM faq").fetchone()[0] == 0 and os.path.exists(CSV_PATH):
            import_csv(c)


def _hit(k, q):
    """English keywords must begin a word ('lab' matches 'labs' but not 'available'); Telugu keywords match anywhere."""
    if k.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(k), q) is not None
    return k in q


def search(question, venue=None):
    """Best FAQ row for the question, or None. If venue is given, only that venue plus 'all' is searched."""
    q = question.lower()
    with connect() as c:
        if venue in ("hospital", "train", "bus"):
            rows = c.execute("SELECT * FROM faq WHERE venue IN (?, 'all')", (venue,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM faq").fetchall()
    best, score = None, 0
    for r in rows:
        s = sum(1 for k in r["keywords"].split("|") if k and _hit(k, q))
        if s > score:
            best, score = r, s
    return best


def facts(venue=None):
    """English facts for the venue (plus 'all'), used as context for the AI so it does not invent details."""
    with connect() as c:
        if venue in ("hospital", "train", "bus"):
            rows = c.execute("SELECT answer_en FROM faq WHERE venue IN (?, 'all')", (venue,)).fetchall()
        else:
            rows = c.execute("SELECT answer_en FROM faq").fetchall()
    return [r["answer_en"] for r in rows]
