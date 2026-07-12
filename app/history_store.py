import json
import os
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "careconnect.db"


def _db_path():
    path = Path(os.environ.get("SEARCH_HISTORY_DB_PATH", DEFAULT_DB_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect():
    connection = sqlite3.connect(_db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_history_store():
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload TEXT NOT NULL
            )
            """
        )


def add_search(payload, max_records=200):
    # Store aggregate navigation metrics only. Never persist symptoms,
    # coordinates, names, emails, user IDs, or free-text input here.
    allowed_fields = {
        "specialty", "provider_count", "nearest_clinic_count",
        "fallback_hospital_count", "recommended_hospital_count",
        "recommended_long_term_count", "advocate_count", "ai_matched",
        "access_level", "care_gap_detected",
    }
    minimized = {key: payload.get(key) for key in allowed_fields if key in payload}
    serialized = json.dumps(minimized, allow_nan=False)
    with _connect() as connection:
        connection.execute(
            "INSERT INTO search_history (payload) VALUES (?)",
            (serialized,),
        )
        connection.execute(
            """
            DELETE FROM search_history
            WHERE id NOT IN (
                SELECT id FROM search_history ORDER BY id DESC LIMIT ?
            )
            """,
            (max_records,),
        )


def history_summary(recent_limit=5):
    with _connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM search_history"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT created_at, payload FROM search_history ORDER BY id DESC"
        ).fetchall()

    items = []
    specialty_counts = {}
    for row in rows:
        item = json.loads(row["payload"])
        item["created_at"] = row["created_at"]
        items.append(item)
        specialty = item.get("specialty", "Unknown")
        specialty_counts[specialty] = specialty_counts.get(specialty, 0) + 1

    return {
        "total_searches": total,
        "specialty_counts": specialty_counts,
        "recent_searches": items[:recent_limit],
    }
