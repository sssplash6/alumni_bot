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
        await db.commit()


async def is_applications_open() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'applications_open'"
        ) as cur:
            row = await cur.fetchone()
    return row is not None and row[0] == "1"
