"""SQLite persistence for weekly reviews.

Stores each review produced by weekly_review.py in life_os.db (created next
to this file on first use) so past reviews can be looked back on later.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "life_os.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            top_needle_movers TEXT NOT NULL,
            drift_detected INTEGER NOT NULL,
            drift_summary TEXT,
            reflection_question TEXT,
            raw_transcript TEXT
        )
        """
    )
    return conn


def _row_to_dict(row):
    d = dict(row)
    d["top_needle_movers"] = json.loads(d["top_needle_movers"])
    d["drift_detected"] = bool(d["drift_detected"])
    return d


def save_review(summary_dict, transcript_text):
    """Insert one row for today's review.

    summary_dict is the structured output from weekly_review.py's final tool
    call: top_needle_movers, drift_detected, drift_summary,
    reflection_question. transcript_text is the full conversation as one
    string.
    """
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO weekly_reviews
                (date, top_needle_movers, drift_detected, drift_summary,
                 reflection_question, raw_transcript)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                date.today().isoformat(),
                json.dumps(summary_dict.get("top_needle_movers", [])),
                1 if summary_dict.get("drift_detected") else 0,
                summary_dict.get("drift_summary", ""),
                summary_dict.get("reflection_question", ""),
                transcript_text,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_reviews(n=4):
    """Return the n most recent reviews, most recent first, as dicts."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM weekly_reviews ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def get_all_reviews():
    """Return every stored review, most recent first, as dicts.

    Convenience wrapper for confirming/inspecting the full table.
    """
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM weekly_reviews ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]
