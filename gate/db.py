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
    'awaiting_name'  -> not found by tg_id or username; asked for their full
                        name so the form can be looked up by that instead.
    'awaiting_intro' -> form verified; waiting for the student's intro.
    'registered'     -> fully onboarded and handed a one-time invite link — plus a
                        second one to the channel, where GATE_CHANNEL_ID is set.
                        ``channel_invite_link`` is null both for rows written
                        before the channel was configured and for anyone whose
                        channel link failed to mint, and is backfilled on demand;
                        only ``invite_link`` is guaranteed on this status.

``gate_announcements`` remembers the live "check if I'm in the alumni group"
message per monitored group — its id so the previous one can be cleared when a
fresh one goes up, and its timestamp so the re-post cadence survives restarts.
"""
from datetime import datetime, timezone

import aiosqlite

from config import DB_PATH

VALID_STATUSES = (
    "member", "nudged", "awaiting_form", "awaiting_name", "awaiting_intro",
    "registered",
)


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
        # Set when someone redeems an invite token, which excuses them from the
        # "must be in an approved group" check. Persistent rather than a one-shot,
        # because eligibility is re-asked every time they re-enter onboarding —
        # abandoning halfway and coming back must not need a second token.
        try:
            await db.execute(
                "ALTER TABLE gate_users ADD COLUMN exempt INTEGER NOT NULL DEFAULT 0"
            )
        except aiosqlite.OperationalError:
            pass
        # The intro-reminder clock. joined_group_at starts it, intro_posted_at
        # stops it, intro_reminded_at makes sure it only rings once. All three are
        # null for everyone who was already in the group when this shipped, which
        # is why they are never chased about an intro nobody asked them for.
        for column in (
            "joined_group_at TEXT",
            "intro_posted_at TEXT",
            "intro_reminded_at TEXT",
            # The channel link handed out alongside the group one. Null both for
            # everyone registered before the channel existed and for anyone
            # admitted while GATE_CHANNEL_ID is unset, so it is backfilled on
            # demand rather than assumed present — see _existing_invite_markup.
            "channel_invite_link TEXT",
        ):
            try:
                await db.execute(f"ALTER TABLE gate_users ADD COLUMN {column}")
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
        #
        # ``approved`` is the security boundary. Being watched only means the
        # group is KNOWN — anyone at all can put the bot in a group and promote
        # it, so a watched group proves nothing about anybody. Approval is set by
        # a bot admin acting inside the group, and it is what makes membership
        # there count as being a Freshman person. See gate/handlers.py.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gate_monitored_chats (
                chat_id  INTEGER PRIMARY KEY,
                title    TEXT,
                added_at TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Added after the first release. Rows predating the column were all put
        # there by a config edit or an admin command — auto-watch could not yet
        # grant anything — so grandfathering them as approved preserves exactly
        # the behaviour they already had.
        try:
            await db.execute(
                "ALTER TABLE gate_monitored_chats "
                "ADD COLUMN approved INTEGER NOT NULL DEFAULT 0"
            )
            await db.execute("UPDATE gate_monitored_chats SET approved = 1")
        except aiosqlite.OperationalError:
            pass
        # One-off invite codes, for admitting someone who isn't in any approved
        # group — a guest speaker, a graduate who left every chat, an early
        # alumnus who predates them.
        #
        # Only a HASH is stored. The plaintext is shown to the admin once, at
        # creation, and never again: a token in the database is a working key to
        # the alumni group, and this file gets backed up on a schedule. Losing one
        # means issuing another, which is the correct cost.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gate_invite_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash  TEXT NOT NULL UNIQUE,
                note        TEXT,
                created_by  INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT,
                redeemed_by INTEGER,
                redeemed_at TEXT,
                revoked_at  TEXT
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
    channel_invite_link: str | None = None,
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
                nudged_at, registered_at, invite_link, channel_invite_link,
                last_seen_chat_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                channel_invite_link = COALESCE(
                    excluded.channel_invite_link, gate_users.channel_invite_link
                ),
                last_seen_chat_id = COALESCE(
                    excluded.last_seen_chat_id, gate_users.last_seen_chat_id
                ),
                updated_at    = excluded.updated_at
            """,
            (
                user_id, username, first_name, full_name, status,
                nudged_at, registered_at, invite_link, channel_invite_link,
                last_seen_chat_id, now,
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


async def mark_awaiting_name(
    user_id: int, username: str | None, first_name: str | None
) -> None:
    """Record that we've asked for their full name to look the form up by."""
    await _upsert(
        user_id, "awaiting_name", username=username, first_name=first_name
    )


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
    channel_invite_link: str | None = None,
) -> None:
    """Record that the user cleared the gate and was handed their link(s).

    ``channel_invite_link`` is optional because the channel is: admission means
    the group, and a channel link that couldn't be minted must not cost someone
    their place. It's COALESCEd like the rest, so a later backfill sticks.
    """
    await _upsert(
        user_id,
        "registered",
        username=username,
        first_name=first_name,
        full_name=full_name,
        registered_at=_now(),
        invite_link=invite_link,
        channel_invite_link=channel_invite_link,
    )


async def set_channel_invite_link(user_id: int, channel_invite_link: str) -> None:
    """Attach a channel link to someone who already has their group one.

    For rows written before the channel was configured. Status is deliberately
    left alone — this is a backfill, not a state change.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE gate_users SET channel_invite_link = ?, updated_at = ? "
            "WHERE user_id = ? AND channel_invite_link IS NULL",
            (channel_invite_link, _now(), user_id),
        )
        await db.commit()


async def awaiting_form_users() -> list[dict]:
    """Everyone whose form we're still waiting on — used by the background poll."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_users WHERE status = 'awaiting_form'"
        )
        return [dict(row) for row in await cur.fetchall()]


# ── Monitored groups ────────────────────────────────────────────────────────────

async def add_monitored_chat(
    chat_id: int, title: str | None, approved: bool = False
) -> bool:
    """Start watching a group. False if it was already being watched.

    ``approved`` only ever moves up. A group blessed by an admin must not be
    silently demoted by a later auto-watch touching the same row.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM gate_monitored_chats WHERE chat_id = ?", (chat_id,)
        )
        existed = await cur.fetchone() is not None
        await db.execute(
            """
            INSERT INTO gate_monitored_chats (chat_id, title, added_at, approved)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title    = COALESCE(excluded.title, gate_monitored_chats.title),
                approved = MAX(excluded.approved, gate_monitored_chats.approved)
            """,
            (chat_id, title, _now(), int(approved)),
        )
        await db.commit()
        return not existed


async def approve_monitored_chat(chat_id: int, title: str | None = None) -> bool:
    """Bless a group so membership in it counts towards eligibility.

    Watches it first if it wasn't already, so an admin never has to run two
    commands. Returns True if this actually changed anything.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT approved FROM gate_monitored_chats WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        already = row is not None and row[0] == 1
        await db.execute(
            """
            INSERT INTO gate_monitored_chats (chat_id, title, added_at, approved)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                title    = COALESCE(excluded.title, gate_monitored_chats.title),
                approved = 1
            """,
            (chat_id, title, _now()),
        )
        await db.commit()
        return not already


async def remove_monitored_chat(chat_id: int) -> bool:
    """Stop watching a group. False if it wasn't being watched."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM gate_monitored_chats WHERE chat_id = ?", (chat_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def monitored_chats() -> list[dict]:
    """Every group currently being watched, approved or not."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_monitored_chats ORDER BY added_at"
        )
        return [dict(row) for row in await cur.fetchall()]


# ── The intro reminder ──────────────────────────────────────────────────────────

async def mark_joined_group(
    user_id: int, username: str | None, first_name: str | None
) -> None:
    """They have just walked into the alumni group. Starts the intro clock.

    ``joined_group_at`` is written once and then preserved: someone who leaves and
    rejoins doesn't get a second grace period they already had. Distinct from
    mark_member, which means "we discovered they're in there" and can fire long
    after the fact — only a join event knows *when*.
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gate_users
                (user_id, username, first_name, status, joined_group_at, updated_at)
            VALUES (?, ?, ?, 'member', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username        = COALESCE(excluded.username, gate_users.username),
                first_name      = COALESCE(excluded.first_name, gate_users.first_name),
                status          = 'member',
                joined_group_at = COALESCE(
                    gate_users.joined_group_at, excluded.joined_group_at
                ),
                updated_at      = excluded.updated_at
            """,
            (user_id, username, first_name, now, now),
        )
        await db.commit()


async def mark_intro_posted(user_id: int) -> None:
    """They've said something in the alumni group, so stop the clock.

    Only writes if it's still null, so the timestamp means "first posted", and
    only touches rows that exist — people who predate the gate have none, and
    inventing one would put them in a reminder they were never owed.
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE gate_users SET intro_posted_at = ?, updated_at = ? "
            "WHERE user_id = ? AND intro_posted_at IS NULL",
            (now, now, user_id),
        )
        await db.commit()


async def mark_intro_reminded(user_id: int) -> None:
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE gate_users SET intro_reminded_at = ?, updated_at = ? "
            "WHERE user_id = ?",
            (now, now, user_id),
        )
        await db.commit()


async def users_missing_intro(cutoff_iso: str) -> list[dict]:
    """Joined before the cutoff, still hasn't posted, hasn't been reminded."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM gate_users
             WHERE joined_group_at IS NOT NULL
               AND joined_group_at <= ?
               AND intro_posted_at IS NULL
               AND intro_reminded_at IS NULL
             ORDER BY joined_group_at
            """,
            (cutoff_iso,),
        )
        return [dict(row) for row in await cur.fetchall()]


# ── Invite tokens ───────────────────────────────────────────────────────────────

async def mark_exempt(user_id: int) -> None:
    """Excuse this person from the approved-group check, permanently.

    Written straight rather than through _upsert: redeeming a token must not
    disturb whatever status they already have, and the commonest case is somebody
    with no row at all.
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO gate_users (user_id, status, exempt, updated_at)
            VALUES (?, 'awaiting_form', 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                exempt = 1, updated_at = excluded.updated_at
            """,
            (user_id, now),
        )
        await db.commit()


async def is_exempt(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT exempt FROM gate_users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0])


async def create_invite_token(
    token_hash: str, note: str | None, created_by: int, expires_at: str | None
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO gate_invite_tokens
                (token_hash, note, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_hash, note, created_by, _now(), expires_at),
        )
        await db.commit()
        return cur.lastrowid


async def find_invite_token(token_hash: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_invite_tokens WHERE token_hash = ?", (token_hash,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def redeem_invite_token(token_id: int, user_id: int) -> bool:
    """Spend a token. False if someone else got there first, or it was revoked.

    The guard lives in the WHERE clause rather than in a read-then-write, so two
    people racing the same code can't both be admitted by it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE gate_invite_tokens
               SET redeemed_by = ?, redeemed_at = ?
             WHERE id = ? AND redeemed_by IS NULL AND revoked_at IS NULL
            """,
            (user_id, _now(), token_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def revoke_invite_token(token_id: int) -> bool:
    """Kill an unredeemed token. False if it doesn't exist or was already spent."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE gate_invite_tokens
               SET revoked_at = ?
             WHERE id = ? AND redeemed_by IS NULL AND revoked_at IS NULL
            """,
            (_now(), token_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def invite_tokens() -> list[dict]:
    """Every token ever issued, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_invite_tokens ORDER BY id DESC"
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
    """The roster of everyone ever admitted through the gate.

    Keyed off ``registered_at`` rather than ``status = 'registered'``: that
    status is lost as soon as they join the alumni group and become 'member',
    which would drop from the roster exactly the people who completed the
    journey. See stats() for the same distinction.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM gate_users "
            "WHERE registered_at IS NOT NULL ORDER BY registered_at"
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
    """Counts per status plus totals, for the admin command.

    ``status`` holds one value at a time, so the per-status counts say where
    people are *right now*, not what they have done. In particular 'registered'
    is not "registered through the bot": someone handed their invite link is
    flipped to 'member' the moment they walk into the alumni group, and their
    registration is absorbed into that bucket. The 'registered' count is
    therefore only the people mid-flight — given a link, not yet through the
    door — and reads zero whenever everyone has used theirs.

    ``registered_ever`` is the lifetime figure. ``registered_at`` is COALESCEd on
    every write and left alone by mark_joined_group, so it outlives the status
    flip and is the honest answer to "how many people did the bot register?".

    ``total`` counts rows directly rather than summing the buckets, so a status
    that isn't displayed can never quietly shrink it.

    An invite code is not a way round the flow — it only excuses someone from the
    approved-group check, and they still do the form and the intro — so everyone
    admitted by one is already inside ``registered_ever``. ``token_registered``
    breaks that subset out, and ``tokens_redeemed`` counts the people who spent a
    code at all: the gap between the two is codes handed to someone who started
    and never finished, which nothing else reports. Both count people rather than
    rows, since one person redeeming twice is still one person.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        result = {s: 0 for s in VALID_STATUSES}
        cur = await db.execute(
            "SELECT status, COUNT(*) AS n FROM gate_users GROUP BY status"
        )
        for row in await cur.fetchall():
            result[row[0]] = row[1]
        cur = await db.execute(
            "SELECT COUNT(*), COUNT(registered_at) FROM gate_users"
        )
        total, registered_ever = await cur.fetchone()
        result["total"] = total
        result["registered_ever"] = registered_ever
        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT t.redeemed_by),
                   COUNT(DISTINCT CASE WHEN u.registered_at IS NOT NULL
                                       THEN t.redeemed_by END)
              FROM gate_invite_tokens t
              LEFT JOIN gate_users u ON u.user_id = t.redeemed_by
             WHERE t.redeemed_by IS NOT NULL
            """
        )
        tokens_redeemed, token_registered = await cur.fetchone()
        result["tokens_redeemed"] = tokens_redeemed
        result["token_registered"] = token_registered
        return result


async def all_user_ids() -> set[int]:
    """Everyone the gate has a row for, for broadcasts.

    Includes people it only ever nudged: they have messaged the bot if they
    tapped an announcement button, and a send that fails is counted and skipped.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM gate_users") as cur:
            return {row[0] for row in await cur.fetchall()}
