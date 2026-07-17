# tests/test_gate.py
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import database as db


@pytest.fixture()
def temp_db(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("database.DB_PATH", path):
        asyncio.run(db.init_db())
        yield path


def _user(uid=555, username="alice", first="Alice", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first, is_bot=is_bot)


def _ctx(member_status=None, invite="https://t.me/+minted"):
    bot_obj = MagicMock()
    bot_obj.username = "AlumniBot"
    if member_status is None:
        bot_obj.get_chat_member = AsyncMock(side_effect=Exception("user not found"))
    else:
        bot_obj.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=member_status))
    bot_obj.create_chat_invite_link = AsyncMock(
        return_value=SimpleNamespace(invite_link=invite)
    )
    bot_obj.send_message = AsyncMock()
    return SimpleNamespace(bot=bot_obj, user_data={})


# ── DB layer ────────────────────────────────────────────────────────────────────

def test_gate_table_created(temp_db):
    import aiosqlite

    async def tables():
        async with aiosqlite.connect(temp_db) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                return {r[0] for r in await cur.fetchall()}

    assert "gate_users" in asyncio.run(tables())


def test_gate_register_preserves_nudge(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.gate_mark_nudged(9, "carol", "Carol"))
        original = asyncio.run(db.gate_get_user(9))["nudged_at"]
        asyncio.run(db.gate_mark_registered(9, "carol", "Carol", "Carol D.", "https://t.me/+x"))
        row = asyncio.run(db.gate_get_user(9))
    assert row["status"] == "registered"
    assert row["nudged_at"] == original
    assert row["full_name"] == "Carol D."
    assert row["invite_link"] == "https://t.me/+x"


def test_gate_stats(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.gate_mark_member(1, None, "A"))
        asyncio.run(db.gate_mark_nudged(2, None, "B"))
        asyncio.run(db.gate_mark_registered(3, None, "C", "C Full", "l"))
        counts = asyncio.run(db.gate_stats())
    assert counts == {"member": 1, "nudged": 1, "registered": 1, "total": 3}


# ── Coming-soon gating ────────────────────────────────────────────────────────────

def test_button_says_coming_soon_when_not_live(temp_db):
    ctx = _ctx()
    reply = AsyncMock()
    update = SimpleNamespace(
        callback_query=None,
        effective_user=_user(),
        effective_message=SimpleNamespace(reply_text=reply),
    )
    with patch("database.DB_PATH", temp_db), patch.object(bot, "ALUMNI_GATE_LIVE", False):
        state = asyncio.run(bot.gate_start(update, ctx))

    assert state == bot.ConversationHandler.END
    ctx.bot.create_chat_invite_link.assert_not_awaited()
    assert "coming soon" in reply.await_args.args[0].lower()


def test_no_nudge_when_not_live(temp_db):
    ctx = _ctx(member_status=None)
    with patch("database.DB_PATH", temp_db), \
         patch.object(bot, "ALUMNI_GATE_LIVE", False), \
         patch.object(bot, "ALUMNI_GATE_GROUP_ID", -100999), \
         patch.object(bot, "ALUMNI_GATE_MONITORED_GROUP_IDS", [-100111]):
        asyncio.run(bot._gate_process_user(ctx, -100111, _user()))
    ctx.bot.send_message.assert_not_awaited()


# ── Live behaviour ────────────────────────────────────────────────────────────────

def test_non_member_nudged_once_when_live(temp_db):
    ctx = _ctx(member_status=None)
    user = _user()
    with patch("database.DB_PATH", temp_db), \
         patch.object(bot, "ALUMNI_GATE_LIVE", True), \
         patch.object(bot, "ALUMNI_GATE_GROUP_ID", -100999), \
         patch.object(bot, "ALUMNI_GATE_MONITORED_GROUP_IDS", [-100111]):
        asyncio.run(bot._gate_process_user(ctx, -100111, user))
        # second sighting -> no repeat
        ctx.bot.send_message.reset_mock()
        asyncio.run(bot._gate_process_user(ctx, -100111, user))
        row = asyncio.run(db.gate_get_user(555))

    ctx.bot.send_message.assert_not_awaited()  # (post-reset) no second nudge
    assert row["status"] == "nudged"


def test_existing_member_not_nudged(temp_db):
    ctx = _ctx(member_status="member")
    with patch("database.DB_PATH", temp_db), \
         patch.object(bot, "ALUMNI_GATE_LIVE", True), \
         patch.object(bot, "ALUMNI_GATE_GROUP_ID", -100999), \
         patch.object(bot, "ALUMNI_GATE_MONITORED_GROUP_IDS", [-100111]):
        asyncio.run(bot._gate_process_user(ctx, -100111, _user()))
        row = asyncio.run(db.gate_get_user(555))
    ctx.bot.send_message.assert_not_awaited()
    assert row["status"] == "member"


def test_register_flow_issues_link_when_live(temp_db):
    ctx = _ctx(member_status=None, invite="https://t.me/+minted")
    with patch("database.DB_PATH", temp_db), \
         patch.object(bot, "ALUMNI_GATE_LIVE", True), \
         patch.object(bot, "ALUMNI_GATE_GROUP_ID", -100999):
        # /start -> asks for name
        reply1 = AsyncMock()
        u1 = SimpleNamespace(
            callback_query=None, effective_user=_user(),
            effective_message=SimpleNamespace(reply_text=reply1),
        )
        assert asyncio.run(bot.gate_start(u1, ctx)) == bot.GATE_NAME
        ctx.bot.create_chat_invite_link.assert_not_awaited()

        # name -> mints link, stores name
        reply2 = AsyncMock()
        u2 = SimpleNamespace(
            effective_user=_user(),
            message=SimpleNamespace(reply_text=reply2, text="  Ada Lovelace  "),
        )
        assert asyncio.run(bot.gate_got_name(u2, ctx)) == bot.ConversationHandler.END
        row = asyncio.run(db.gate_get_user(555))

    ctx.bot.create_chat_invite_link.assert_awaited_once()
    assert row["status"] == "registered"
    assert row["full_name"] == "Ada Lovelace"
    assert row["invite_link"] == "https://t.me/+minted"
    assert "https://t.me/+minted" in reply2.await_args.args[0]
