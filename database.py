import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


DB_PATH = os.getenv("DB_PATH", "votes.db")


def now_utc():
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db():
    """Open a short-lived SQLite connection and safely commit/rollback."""
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level="DEFERRED",
    )
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create only the new-poll tables; never alter the legacy votes table."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed')),
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                FOREIGN KEY (poll_id)
                    REFERENCES polls(id)
                    ON DELETE CASCADE,
                UNIQUE (poll_id, option_text)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS poll_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (poll_id)
                    REFERENCES polls(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (option_id)
                    REFERENCES options(id),
                UNIQUE (poll_id, user_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_poll_votes_poll
            ON poll_votes(poll_id)
        """)


def create_poll(title, question, options):
    title = str(title).strip()
    question = str(question).strip()
    options = [str(item).strip() for item in options if str(item).strip()]

    if not title or len(title) > 200:
        raise ValueError("Opros nomi 1–200 belgi bo'lishi kerak.")
    if not question or len(question) > 1000:
        raise ValueError("Opros savoli 1–1000 belgi bo'lishi kerak.")
    if len(options) < 2:
        raise ValueError("Kamida 2 ta javob varianti kerak.")
    if len(options) > 50:
        raise ValueError("Eng ko'pi bilan 50 ta variant mumkin.")
    if any(len(item) > 64 for item in options):
        raise ValueError("Har bir javob varianti 64 belgidan oshmasin.")
    if len(set(options)) != len(options):
        raise ValueError("Takroriy javob variantlari mavjud.")

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO polls (title, question, status, created_at)
            VALUES (?, ?, 'active', ?)
            """,
            (title, question, now_utc()),
        )
        poll_id = cursor.lastrowid

        conn.executemany(
            "INSERT INTO options (poll_id, option_text) VALUES (?, ?)",
            [(poll_id, option) for option in options],
        )

        return poll_id


def get_poll(poll_id):
    with get_db() as conn:
        poll = conn.execute(
            "SELECT * FROM polls WHERE id = ?",
            (poll_id,),
        ).fetchone()

        if poll is None:
            return None

        options = conn.execute(
            """
            SELECT id, option_text
            FROM options
            WHERE poll_id = ?
            ORDER BY id
            """,
            (poll_id,),
        ).fetchall()

        result = dict(poll)
        result["options"] = [dict(option) for option in options]
        return result


def get_all_polls():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.title,
                p.question,
                p.status,
                p.created_at,
                COUNT(v.id) AS total_votes
            FROM polls AS p
            LEFT JOIN poll_votes AS v ON v.poll_id = p.id
            GROUP BY p.id
            ORDER BY p.id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def cast_vote(poll_id, user_id, option_id):
    """Atomically validate and record one vote per user per poll."""
    with get_db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            poll = conn.execute(
                "SELECT status FROM polls WHERE id = ?",
                (poll_id,),
            ).fetchone()

            if poll is None or poll["status"] != "active":
                return "closed"

            option = conn.execute(
                """
                SELECT id
                FROM options
                WHERE id = ? AND poll_id = ?
                """,
                (option_id, poll_id),
            ).fetchone()

            if option is None:
                return "invalid"

            conn.execute(
                """
                INSERT INTO poll_votes
                    (poll_id, user_id, option_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (poll_id, user_id, option_id, now_utc()),
            )
            return "success"

        except sqlite3.IntegrityError:
            existing_vote = conn.execute(
                """
                SELECT 1 FROM poll_votes
                WHERE poll_id = ? AND user_id = ?
                """,
                (poll_id, user_id),
            ).fetchone()
            if existing_vote:
                return "already_voted"
            raise


def get_results(poll_id):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.option_text,
                COUNT(v.id) AS votes
            FROM options AS o
            LEFT JOIN poll_votes AS v
                ON v.option_id = o.id
                AND v.poll_id = o.poll_id
            WHERE o.poll_id = ?
            GROUP BY o.id, o.option_text
            ORDER BY o.id
            """,
            (poll_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def close_poll(poll_id):
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE polls
            SET status = 'closed'
            WHERE id = ? AND status = 'active'
            """,
            (poll_id,),
        )
        return cursor.rowcount > 0


def delete_poll(poll_id):
    """Deletion is intentionally disabled to preserve poll history."""
    return False
