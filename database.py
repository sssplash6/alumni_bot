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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS apf_submissions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id             INTEGER NOT NULL UNIQUE,
                username            TEXT,
                first_name          TEXT NOT NULL,
                full_name           TEXT NOT NULL,
                cohort              TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                reviewer_message_id INTEGER,
                created_at          TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS elysium_submissions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id             INTEGER NOT NULL UNIQUE,
                username            TEXT,
                first_name          TEXT NOT NULL,
                full_name           TEXT NOT NULL,
                cohort              TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                reviewer_message_id INTEGER,
                created_at          TEXT NOT NULL
            )
        """)
        # Alumni Gate: everyone the bot has classified against the alumni group.
        # status is 'member' (already in) / 'nudged' (tagged once) /
        # 'registered' (gave name + got a one-time link).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gate_users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                full_name     TEXT,
                status        TEXT NOT NULL,
                nudged_at     TEXT,
                registered_at TEXT,
                invite_link   TEXT,
                updated_at    TEXT NOT NULL
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


# ── Generic settings ───────────────────────────────────────────────────────────

async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


# ── Admissions Program Fair ─────────────────────────────────────────────────────

async def apf_save_submission(
    chat_id: int,
    username: str | None,
    first_name: str,
    full_name: str,
    cohort: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO apf_submissions
               (chat_id, username, first_name, full_name, cohort, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   username = excluded.username,
                   first_name = excluded.first_name,
                   full_name = excluded.full_name,
                   cohort = excluded.cohort,
                   status = 'pending',
                   reviewer_message_id = NULL,
                   created_at = excluded.created_at""",
            (chat_id, username, first_name, full_name, cohort, now),
        )
        await db.commit()


async def apf_get_submission(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM apf_submissions WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def apf_set_reviewer_message(chat_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE apf_submissions SET reviewer_message_id = ? WHERE chat_id = ?",
            (message_id, chat_id),
        )
        await db.commit()


async def apf_set_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE apf_submissions SET status = ? WHERE chat_id = ?",
            (status, chat_id),
        )
        await db.commit()


async def apf_get_by_status(statuses: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM apf_submissions WHERE status IN ({placeholders}) ORDER BY created_at",
            statuses,
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Elysium pre-2025 ────────────────────────────────────────────────────────────

async def elysium_save_submission(
    chat_id: int,
    username: str | None,
    first_name: str,
    full_name: str,
    cohort: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO elysium_submissions
               (chat_id, username, first_name, full_name, cohort, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   username = excluded.username,
                   first_name = excluded.first_name,
                   full_name = excluded.full_name,
                   cohort = excluded.cohort,
                   status = 'pending',
                   reviewer_message_id = NULL,
                   created_at = excluded.created_at""",
            (chat_id, username, first_name, full_name, cohort, now),
        )
        await db.commit()


async def elysium_get_submission(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM elysium_submissions WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def elysium_set_reviewer_message(chat_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE elysium_submissions SET reviewer_message_id = ? WHERE chat_id = ?",
            (message_id, chat_id),
        )
        await db.commit()


async def elysium_set_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE elysium_submissions SET status = ? WHERE chat_id = ?",
            (status, chat_id),
        )
        await db.commit()


async def elysium_get_by_status(statuses: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM elysium_submissions WHERE status IN ({placeholders}) ORDER BY created_at",
            statuses,
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def elysium_get_all() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM elysium_submissions ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Alumni Gate ──────────────────────────────────────────────────────────────────

GATE_STATUSES = ("member", "nudged", "registered")


async def gate_get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM gate_users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _gate_upsert(
    user_id: int,
    status: str,
    username: str | None = None,
    first_name: str | None = None,
    full_name: str | None = None,
    nudged_at: str | None = None,
    registered_at: str | None = None,
    invite_link: str | None = None,
) -> None:
    """Insert or update a gate_users row, preserving existing non-null fields.

    COALESCE keeps prior full_name / nudged_at / registered_at / invite_link so
    recording a later status never wipes earlier facts.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gate_users (
                user_id, username, first_name, full_name, status,
                nudged_at, registered_at, invite_link, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = COALESCE(excluded.username, gate_users.username),
                first_name    = COALESCE(excluded.first_name, gate_users.first_name),
                full_name     = COALESCE(excluded.full_name, gate_users.full_name),
                status        = excluded.status,
                nudged_at     = COALESCE(excluded.nudged_at, gate_users.nudged_at),
                registered_at = COALESCE(excluded.registered_at, gate_users.registered_at),
                invite_link   = COALESCE(excluded.invite_link, gate_users.invite_link),
                updated_at    = excluded.updated_at
            """,
            (
                user_id, username, first_name, full_name, status,
                nudged_at, registered_at, invite_link, now,
            ),
        )
        await db.commit()


async def gate_mark_member(user_id: int, username: str | None, first_name: str | None) -> None:
    await _gate_upsert(user_id, "member", username=username, first_name=first_name)


async def gate_mark_nudged(user_id: int, username: str | None, first_name: str | None) -> None:
    await _gate_upsert(
        user_id,
        "nudged",
        username=username,
        first_name=first_name,
        nudged_at=datetime.now(timezone.utc).isoformat(),
    )


async def gate_mark_registered(
    user_id: int,
    username: str | None,
    first_name: str | None,
    full_name: str,
    invite_link: str,
) -> None:
    await _gate_upsert(
        user_id,
        "registered",
        username=username,
        first_name=first_name,
        full_name=full_name,
        registered_at=datetime.now(timezone.utc).isoformat(),
        invite_link=invite_link,
    )


async def gate_has_been_nudged(user_id: int) -> bool:
    user = await gate_get_user(user_id)
    return bool(user and user["nudged_at"])


async def gate_stats() -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        result = {s: 0 for s in GATE_STATUSES}
        async with db.execute(
            "SELECT status, COUNT(*) AS n FROM gate_users GROUP BY status"
        ) as cur:
            for row in await cur.fetchall():
                result[row[0]] = row[1]
    result["total"] = sum(result[s] for s in GATE_STATUSES)
    return result


async def gate_get_registered() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM gate_users WHERE status = 'registered' ORDER BY registered_at"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
