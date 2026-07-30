"""Persistence for the Alumni Gate, inside the host bot's database.

``gate_users`` records everyone the gate has made a decision about, so that:

  * a person is tagged in a group at most once per announcement cycle;
  * we never re-check membership for someone already classified (it acts as a
    cache in front of get_chat_member);
  * admins can see counts and the roster.

``status`` is one of:
    'member'         -> already in the alumni group; nothing to do.
    'nudged'         -> not in alumni; tagged in the group.
    'awaiting_form'  -> started the bot; onboarding form not yet verified.
    'awaiting_intro' -> form verified; waiting for the student's intro.
    'registered'     -> fully onboarded and handed a one-time invite link.

``gate_announcements`` remembers the live "check if I'm in the alumni group"
message per monitored group — its id so the previous one can be cleared when a
fresh one goes up, and its timestamp so the re-post cadence survives restarts.
"""
from datetime import datetime, timezone

import aiosqlite

from config import DB_PATH

VALID_STATUSES = ("member", "nudged", "awaiting_form", "awaiting_intro", "registered")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_schema() -> None:
    """Create the gate's tables. Safe to call on every boot."""
    async with aiosqlite.connect(DB_PATH) as db:
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
        # Which monitored group we last saw this person in, so a follow-up nudge
        # goes to the group they're actually in rather than all of them. Added
        # after the first release, hence the tolerated "duplicate column" error.
        try:
            await db.execute(
                "ALTER TABLE gate_users ADD COLUMN last_seen_chat_id INTEGER"
            )
        except aiosqlite.OperationalError:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gate_announcements (
                chat_id    INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                posted_at  TEXT NOT NULL
            )
        """)
        # The groups being watched. In the database rather than the environment
        # because these change often — new community groups get added all the
        # time, and that shouldn't need a config edit and a restart.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gate_monitored_chats (
                chat_id  INTEGER PRIMARY KEY,
                title    TEXT,
                added_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def _upsert(
    user_id: int,
    status: str,
    username: str | None = None,
    first_name: str | None = None,
    full_name: str | None = None,
    nudged_at: str | None = None,
    registered_at: str | None = None,
    invite_link: str | None = None,
    last_seen_chat_id: int | None = None,
) -> None:
    """Insert or update a row, preserving existing non-null fields.

    COALESCE keeps prior full_name / nudged_at / registered_at / invite_link when
    this call doesn't supply them, so recording a later status never wipes
    earlier facts.
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gate_users (
                user_id, username, first_name, full_name, status,
                nudged_at, registered_at, invite_link, last_seen_chat_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = COALESCE(excluded.username, gate_users.username),
                first_name    = COALESCE(excluded.first_name, gate_users.first_name),
                full_name     = COALESCE(excluded.full_name, gate_users.full_name),
                status        = excluded.status,
                nudged_at     = COALESCE(excluded.nudged_at, gate_users.nudged_at),
                -- note: mark_nudged always passes a fresh nudged_at, so a
                -- re-tag advances it; other statuses pass None and preserve it.
                registered_at = COALESCE(excluded.registered_at, gate_users.registered_at),
                invite_link   = COALESCE(excluded.invite_link, gate_users.invite_link),
                last_seen_chat_id = COALESCE(
                    excluded.last_seen_chat_id, gate_users.last_seen_chat_id
                ),
                updated_at    = excluded.updated_at
            """,
            (
                user_id, username, first_name, full_name, status,
                nudged_at, registered_at, invite_link, last_seen_chat_id, now,
            ),
        )
        await db.commit()


async def mark_member(user_id: int, username: str | None, first_name: str | None) -> None:
    """Record that the user is (or has become) an alumni-group member."""
    await _upsert(user_id, "member", username=username, first_name=first_name)


async def mark_nudged(
    user_id: int,
    username: str | None,
    first_name: str | None,
    chat_id: int | None = None,
) -> None:
    """Record that we've tagged this user in a group.

    ``nudged_at`` is overwritten on every tag (not COALESCEd) so it always means
    "when we last chased this person", which is what the follow-up sweep reads.
    """
    await _upsert(
        user_id,
        "nudged",
        username=username,
        first_name=first_name,
        nudged_at=_now(),
        last_seen_chat_id=chat_id,
    )


async def mark_awaiting_form(
    user_id: int, username: str | None, first_name: str | None
) -> None:
    """Record that the user started the bot and is working on the form."""
    await _upsert(user_id, "awaiting_form", username=username, first_name=first_name)


async def mark_awaiting_intro(
    user_id: int,
    username: str | None,
    first_name: str | None,
    full_name: str | None = None,
) -> None:
    """Record that the form is verified and we're awaiting their intro.

    ``full_name`` (from the Airtable submission, if any) is stored now so it's
    available for the roster when we admit them.
    """
    await _upsert(
        user_id,
        "awaiting_intro",
        username=username,
        first_name=first_name,
        full_name=full_name,
    )


async def mark_registered(
    user_id: int,
    username: str | None,
    first_name: str | None,
    full_name: str | None,
    invite_link: str,
) -> None:
    """Record that the user cleared the gate and was handed their link."""
    await _upsert(
        user_id,
        "registered",
        username=username,
        first_name=first_name,
        full_name=full_name,
        registered_at=_now(),
        invite_link=invite_link,
    )


async def awaiting_form_users() -> list[dict]:
    """Everyone whose form we're still waiting on — used by the background poll."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_users WHERE status = 'awaiting_form'"
        )
        return [dict(row) for row in await cur.fetchall()]


# ── Monitored groups ────────────────────────────────────────────────────────────

async def add_monitored_chat(chat_id: int, title: str | None) -> bool:
    """Start watching a group. False if it was already being watched."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM gate_monitored_chats WHERE chat_id = ?", (chat_id,)
        )
        existed = await cur.fetchone() is not None
        await db.execute(
            """
            INSERT INTO gate_monitored_chats (chat_id, title, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = COALESCE(excluded.title, gate_monitored_chats.title)
            """,
            (chat_id, title, _now()),
        )
        await db.commit()
        return not existed


async def remove_monitored_chat(chat_id: int) -> bool:
    """Stop watching a group. False if it wasn't being watched."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM gate_monitored_chats WHERE chat_id = ?", (chat_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def monitored_chats() -> list[dict]:
    """Every group currently being watched."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_monitored_chats ORDER BY added_at"
        )
        return [dict(row) for row in await cur.fetchall()]


async def stale_nudged_users(cutoff_iso: str, chat_id: int) -> list[dict]:
    """People in one group who were tagged before ``cutoff_iso`` and still haven't
    engaged — the follow-up sweep's targets.

    Only status 'nudged' qualifies. Anyone who tapped a button has moved on to
    'member' / 'awaiting_form' / 'awaiting_intro' / 'registered' and is left alone.

    IMPORTANT: this can only return people the bot has an ID for — someone seen
    joining or posting, or who tapped something. Telegram will not enumerate a
    group's members, so a person who has never done any of those is invisible here
    and cannot be tagged at all.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM gate_users
             WHERE status = 'nudged'
               AND last_seen_chat_id = ?
               AND nudged_at IS NOT NULL
               AND nudged_at < ?
             ORDER BY nudged_at
            """,
            (chat_id, cutoff_iso),
        )
        return [dict(row) for row in await cur.fetchall()]


async def registered_users() -> list[dict]:
    """The roster of everyone admitted through the gate."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_users WHERE status = 'registered' ORDER BY registered_at"
        )
        return [dict(row) for row in await cur.fetchall()]


async def has_been_nudged(user_id: int) -> bool:
    user = await get_user(user_id)
    return bool(user and user["nudged_at"])


# ── Announcements ───────────────────────────────────────────────────────────────

async def get_announcement(chat_id: int) -> dict | None:
    """The announcement currently live in this chat, if any."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_announcements WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_announcement(chat_id: int, message_id: int) -> None:
    """Record the announcement now live in this chat, replacing any previous."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gate_announcements (chat_id, message_id, posted_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                message_id = excluded.message_id,
                posted_at  = excluded.posted_at
            """,
            (chat_id, message_id, _now()),
        )
        await db.commit()


async def stats() -> dict[str, int]:
    """Counts per status plus a total, for the admin command."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = {s: 0 for s in VALID_STATUSES}
        cur = await db.execute(
            "SELECT status, COUNT(*) AS n FROM gate_users GROUP BY status"
        )
        for row in await cur.fetchall():
            result[row[0]] = row[1]
        result["total"] = sum(result[s] for s in VALID_STATUSES)
        return result


async def all_user_ids() -> set[int]:
    """Everyone the gate has a row for, for broadcasts.

    Includes people it only ever nudged: they have messaged the bot if they
    tapped an announcement button, and a send that fails is counted and skipped.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM gate_users") as cur:
            return {row[0] for row in await cur.fetchall()}
