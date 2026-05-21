# database.py
import json
from datetime import datetime, timezone
import aiosqlite
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('applications_open', '0')"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mentors (
                chat_id          INTEGER PRIMARY KEY,
                full_name        TEXT NOT NULL,
                spheres          TEXT NOT NULL,
                exp_level        TEXT NOT NULL,
                devote_time      TEXT NOT NULL,
                mentee_exp_prefs TEXT NOT NULL,
                extra            TEXT,
                registered_at    TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mentees (
                chat_id           INTEGER PRIMARY KEY,
                full_name         TEXT NOT NULL,
                spheres           TEXT NOT NULL,
                exp_level         TEXT NOT NULL,
                mentor_exp_prefs  TEXT NOT NULL,
                extra             TEXT,
                devote_time       TEXT NOT NULL,
                consent           INTEGER NOT NULL DEFAULT 0,
                registered_at     TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_chat_id  INTEGER NOT NULL,
                mentee_chat_id  INTEGER NOT NULL,
                score           REAL    NOT NULL,
                matched_at      TEXT    NOT NULL
            )
        """)
        # Migrations: add status column to existing tables if not present
        try:
            await db.execute(
                "ALTER TABLE mentors ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        except Exception:
            pass  # column already exists
        try:
            await db.execute(
                "ALTER TABLE mentees ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        except Exception:
            pass  # column already exists
        await db.commit()


async def is_applications_open() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'applications_open'"
        ) as cur:
            row = await cur.fetchone()
    return row is not None and row[0] == "1"


async def set_applications_open(open_: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('applications_open', ?)",
            ("1" if open_ else "0",),
        )
        await db.commit()


async def save_mentor(
    chat_id: int,
    full_name: str,
    spheres: list[str],
    exp_level: str,
    devote_time: str,
    mentee_exp_prefs: list[str],
    extra: str | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO mentors
               (chat_id, full_name, spheres, exp_level, devote_time, mentee_exp_prefs, extra, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id, full_name, json.dumps(spheres), exp_level,
                devote_time, json.dumps(mentee_exp_prefs), extra,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def save_mentee(
    chat_id: int,
    full_name: str,
    spheres: list[str],
    exp_level: str,
    mentor_exp_prefs: list[str],
    extra: str | None,
    devote_time: str,
    consent: bool,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO mentees
               (chat_id, full_name, spheres, exp_level, mentor_exp_prefs, extra, devote_time, consent, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id, full_name, json.dumps(spheres), exp_level,
                json.dumps(mentor_exp_prefs), extra, devote_time,
                int(consent), datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def is_registered_mentor(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM mentors WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None


async def is_registered_mentee(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM mentees WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None


def _parse_mentor_row(row: dict) -> dict:
    row["spheres"] = json.loads(row["spheres"])
    row["mentee_exp_prefs"] = json.loads(row["mentee_exp_prefs"])
    return row


def _parse_mentee_row(row: dict) -> dict:
    row["spheres"] = json.loads(row["spheres"])
    row["mentor_exp_prefs"] = json.loads(row["mentor_exp_prefs"])
    return row


async def get_mentor_by_chat_id(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return _parse_mentor_row(dict(row)) if row else None


async def get_mentee_by_chat_id(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return _parse_mentee_row(dict(row)) if row else None


async def get_all_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_all_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def save_matches(matches: list[tuple[int, int, float]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO matches (mentor_chat_id, mentee_chat_id, score, matched_at) VALUES (?, ?, ?, ?)",
            [(m_id, t_id, score, now) for m_id, t_id, score in matches],
        )
        await db.commit()


async def get_registration_counts() -> tuple[int, int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM mentors") as cur:
            (mentors,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM mentees") as cur:
            (mentees,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM matches") as cur:
            (matches,) = await cur.fetchone()
    return mentors, mentees, matches


async def get_pending_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_pending_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def set_mentor_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mentors SET status = ? WHERE chat_id = ?", (status, chat_id)
        )
        await db.commit()


async def set_mentee_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mentees SET status = ? WHERE chat_id = ?", (status, chat_id)
        )
        await db.commit()


async def get_approved_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE status = 'approved'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_approved_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE status = 'approved'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def get_pending_counts() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM mentors WHERE status = 'pending'"
        ) as cur:
            (mentor_pending,) = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM mentees WHERE status = 'pending'"
        ) as cur:
            (mentee_pending,) = await cur.fetchone()
    return mentor_pending, mentee_pending


async def get_review_summary() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM mentors GROUP BY status"
        ) as cur:
            mentor_counts = dict(await cur.fetchall())
        async with db.execute(
            "SELECT status, COUNT(*) FROM mentees GROUP BY status"
        ) as cur:
            mentee_counts = dict(await cur.fetchall())
    return {
        "mentor_approved": mentor_counts.get("approved", 0),
        "mentor_denied":   mentor_counts.get("denied", 0),
        "mentor_pending":  mentor_counts.get("pending", 0),
        "mentee_approved": mentee_counts.get("approved", 0),
        "mentee_denied":   mentee_counts.get("denied", 0),
        "mentee_pending":  mentee_counts.get("pending", 0),
    }
