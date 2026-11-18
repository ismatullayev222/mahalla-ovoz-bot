
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "votes.db")


def get_db():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                FOREIGN KEY (poll_id) REFERENCES polls(id)
                    ON DELETE CASCADE,
                UNIQUE(poll_id, option_text)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS poll_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (poll_id) REFERENCES polls(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (option_id) REFERENCES options(id),
                UNIQUE(poll_id, user_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_votes_poll
            ON poll_votes(poll_id)
        """)


def create_poll(title, question, options):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO polls (title, question, status, created_at)
               VALUES (?, ?, 'active', ?)""",
            (
                title,
                question,
                datetime.now(timezone.utc).isoformat()
            )
        )

        poll_id = cursor.lastrowid

        for option in options:
            conn.execute(
                """INSERT INTO options (poll_id, option_text)
                   VALUES (?, ?)""",
                (poll_id, option)
            )

        return poll_id


def get_poll(poll_id):
    with get_db() as conn:
        poll = conn.execute(
            "SELECT * FROM polls WHERE id = ?",
            (poll_id,)
        ).fetchone()

        if not poll:
            return None

        options = conn.execute(
            """SELECT id, option_text FROM options
               WHERE poll_id = ? ORDER BY id""",
            (poll_id,)
        ).fetchall()

        return {
            "id": poll["id"],
            "title": poll["title"],
            "question": poll["question"],
            "status": poll["status"],
            "options": [dict(item) for item in options]
        }


def get_all_polls():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM polls ORDER BY id DESC"
        ).fetchall()


def cast_vote(poll_id, user_id, option_id):
    with get_db() as conn:
        poll = conn.execute(
            "SELECT status FROM polls WHERE id = ?",
            (poll_id,)
        ).fetchone()

        if not poll or poll["status"] != "active":
            return "closed"

        option = conn.execute(
            """SELECT id FROM options
               WHERE id = ? AND poll_id = ?""",
            (option_id, poll_id)
        ).fetchone()

        if not option:
            return "invalid"

        try:
            conn.execute(
                """INSERT INTO poll_votes
                   (poll_id, user_id, option_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    poll_id,
                    user_id,
                    option_id,
                    datetime.now(timezone.utc).isoformat()
                )
            )
            return "success"
        except sqlite3.IntegrityError:
            return "already_voted"


def get_results(poll_id):
    with get_db() as conn:
        return conn.execute(
            """
            SELECT
                o.id,
                o.option_text,
                COUNT(v.id) AS votes
            FROM options o
            LEFT JOIN poll_votes v
                ON v.option_id = o.id
                AND v.poll_id = o.poll_id
            WHERE o.poll_id = ?
            GROUP BY o.id, o.option_text
            ORDER BY o.id
            """,
            (poll_id,)
        ).fetchall()


def close_poll(poll_id):
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE polls SET status = 'closed' WHERE id = ?",
            (poll_id,)
        )
        return cursor.rowcount > 0


def delete_poll(poll_id):
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM polls WHERE id = ?",
            (poll_id,)
        )
        return cursor.rowcount > 0
