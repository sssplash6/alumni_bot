"""Persistence for the Event feature, inside the host bot's database.

``event_registrations`` records everyone who has started registering, so that:

  * someone handed a link to the chat they were missing can be recognised and
    picked up again the moment they join it;
  * a name is asked for once, not on every tap;
  * admins can see the attendee list and the counts.

``status`` is one of:
    'awaiting_join' -> in one of the two chats; given a link to the other.
    'awaiting_name' -> in both; waiting for their full name.
    'registered'    -> done, on the attendee list.

``missing`` is 'group' or 'channel' while awaiting_join, so the follow-up knows
which chat they were sent to. It is left in place afterwards as a record of how
they arrived.

``event_settings`` holds the pointer to the admin-set event post — its chat and
message id, since the post carries an image and is delivered with copy_message
rather than being stored as text.
"""
from datetime import datetime, timezone

import aiosqlite

from config import DB_PATH

VALID_STATUSES = ("awaiting_join", "awaiting_name", "registered")

POST_CHAT_KEY = "post_chat_id"
POST_MESSAGE_KEY = "post_message_id"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_schema() -> None:
    """Create the event's tables. Safe to call on every boot."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_registrations (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                full_name     TEXT,
                status        TEXT NOT NULL,
                missing       TEXT,
                created_at    TEXT NOT NULL,
                registered_at TEXT,
                updated_at    TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.commit()


# ── Registrations ───────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM event_registrations WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _upsert(
    user_id: int,
    status: str,
    username: str | None = None,
    first_name: str | None = None,
    full_name: str | None = None,
    missing: str | None = None,
    registered_at: str | None = None,
) -> None:
    """Write a status, preserving fields a later step doesn't know about.

    COALESCE on the excluded value keeps an earlier full_name or missing marker
    when a subsequent write doesn't carry one — otherwise re-entering the flow
    would blank the attendee list entry.
    """
    assert status in VALID_STATUSES, status
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO event_registrations
                   (user_id, username, first_name, full_name, status, missing,
                    created_at, registered_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username      = COALESCE(excluded.username, username),
                   first_name    = COALESCE(excluded.first_name, first_name),
                   full_name     = COALESCE(excluded.full_name, full_name),
                   status        = excluded.status,
                   missing       = COALESCE(excluded.missing, missing),
                   registered_at = COALESCE(excluded.registered_at, registered_at),
                   updated_at    = excluded.updated_at""",
            (
                user_id, username, first_name, full_name, status, missing,
                now, registered_at, now,
            ),
        )
        await db.commit()


async def mark_awaiting_join(
    user_id: int, username: str | None, first_name: str | None, missing: str
) -> None:
    await _upsert(
        user_id, "awaiting_join",
        username=username, first_name=first_name, missing=missing,
    )


async def mark_awaiting_name(
    user_id: int, username: str | None, first_name: str | None
) -> None:
    await _upsert(
        user_id, "awaiting_name", username=username, first_name=first_name
    )


async def mark_registered(
    user_id: int, username: str | None, first_name: str | None, full_name: str
) -> None:
    await _upsert(
        user_id, "registered",
        username=username, first_name=first_name, full_name=full_name,
        registered_at=_now(),
    )


async def registered() -> list[dict]:
    """The attendee list, in the order people signed up."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM event_registrations WHERE status = 'registered' "
            "ORDER BY registered_at"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def counts() -> dict[str, int]:
    """One row per status, zero-filled so callers can index every status."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM event_registrations GROUP BY status"
        ) as cur:
            rows = await cur.fetchall()
    result = {status: 0 for status in VALID_STATUSES}
    result.update({status: count for status, count in rows})
    return result


async def awaiting_join_ids() -> set[int]:
    """Everyone still short of one chat, for the join follow-up."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM event_registrations WHERE status = 'awaiting_join'"
        ) as cur:
            rows = await cur.fetchall()
    return {row[0] for row in rows}


# ── The admin-set post ──────────────────────────────────────────────────────────

async def set_post(chat_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR REPLACE INTO event_settings (key, value) VALUES (?, ?)",
            [(POST_CHAT_KEY, str(chat_id)), (POST_MESSAGE_KEY, str(message_id))],
        )
        await db.commit()


async def get_post() -> tuple[int, int] | None:
    """The (chat_id, message_id) of the event post, or None if unset."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT key, value FROM event_settings WHERE key IN (?, ?)",
            (POST_CHAT_KEY, POST_MESSAGE_KEY),
        ) as cur:
            rows = dict(await cur.fetchall())
    chat_id, message_id = rows.get(POST_CHAT_KEY), rows.get(POST_MESSAGE_KEY)
    if not chat_id or not message_id:
        return None
    return int(chat_id), int(message_id)
