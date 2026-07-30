# tests/test_broadcast.py
"""/broadcast: the audience union, the confirm gate, and delivery accounting."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import Forbidden

import bot
import database as db
from event import db as edb
from event import settings as event_settings
from gate import db as gdb

ADMIN = 1


@pytest.fixture()
def live(tmp_path):
    """A temp DB with all three features' schemas and no event announcement."""
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("database.DB_PATH", path), \
            patch("gate.db.DB_PATH", path), patch("event.db.DB_PATH", path), \
            patch("bot.ADMIN_IDS", [ADMIN]), \
            patch("bot._BROADCAST_PAUSE", 0), \
            patch.multiple(event_settings, LIVE=False, GROUP_ID=-1, CHANNEL_ID=-2):
        asyncio.run(db.init_db())
        asyncio.run(gdb.init_schema())
        asyncio.run(edb.init_schema())
        yield path


def _dm(args=None, uid=ADMIN):
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="admin", first_name="A"),
        effective_message=SimpleNamespace(reply_text=reply),
        message=SimpleNamespace(reply_text=reply),
    )
    ctx = SimpleNamespace(args=args or [], bot=MagicMock())
    ctx.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    return update, reply, ctx


def _seed_mentor(chat_id):
    asyncio.run(db.save_mentor(
        chat_id, "M", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["New to field"], None
    ))


# ── The audience ────────────────────────────────────────────────────────────────

def test_audience_unions_all_three_features(live):
    _seed_mentor(100)
    asyncio.run(gdb.mark_nudged(200, "b", "B", -100555))
    asyncio.run(edb.mark_registered(300, "c", "C", "C C"))

    assert asyncio.run(bot._broadcast_audience()) == {100, 200, 300}


def test_audience_deduplicates_someone_in_several_features(live):
    """The same person registering twice must not be messaged twice."""
    _seed_mentor(100)
    asyncio.run(gdb.mark_nudged(100, "a", "A", -100555))
    asyncio.run(edb.mark_registered(100, "a", "A", "A A"))

    assert asyncio.run(bot._broadcast_audience()) == {100}


def test_audience_is_empty_on_a_fresh_database(live):
    assert asyncio.run(bot._broadcast_audience()) == set()


# ── The confirm gate ────────────────────────────────────────────────────────────

def test_bare_broadcast_previews_and_sends_nothing(live):
    _seed_mentor(100)
    _seed_mentor(101)
    update, reply, ctx = _dm()

    asyncio.run(bot.broadcast_command(update, ctx))

    assert "2" in reply.await_args_list[0].args[0]
    # The preview goes to the admin only; nobody in the audience is touched.
    ctx.bot.send_message.assert_not_awaited()


def test_preview_shows_the_real_message_and_keyboard(live):
    _seed_mentor(100)
    update, reply, ctx = _dm()

    asyncio.run(bot.broadcast_command(update, ctx))

    # Second reply is the message itself, carrying the keyboard people will get.
    markup = reply.await_args_list[1].kwargs["reply_markup"]
    labels = [b.text for row in markup.keyboard for b in row]
    assert any("Instagram" in label for label in labels)


def test_confirm_actually_sends(live):
    _seed_mentor(100)
    _seed_mentor(101)
    update, reply, ctx = _dm(args=["confirm"])

    asyncio.run(bot.broadcast_command(update, ctx))

    assert ctx.bot.send_message.await_count == 2
    assert {c.kwargs["chat_id"] for c in ctx.bot.send_message.await_args_list} == {100, 101}


def test_confirm_is_case_insensitive(live):
    _seed_mentor(100)
    update, _, ctx = _dm(args=["CONFIRM"])

    asyncio.run(bot.broadcast_command(update, ctx))

    assert ctx.bot.send_message.await_count == 1


def test_a_different_argument_is_not_a_confirmation(live):
    _seed_mentor(100)
    update, _, ctx = _dm(args=["yes"])

    asyncio.run(bot.broadcast_command(update, ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_non_admins_get_nothing_at_all(live):
    _seed_mentor(100)
    update, reply, ctx = _dm(args=["confirm"], uid=999999)

    asyncio.run(bot.broadcast_command(update, ctx))

    reply.assert_not_awaited()
    ctx.bot.send_message.assert_not_awaited()


# ── Delivery accounting ─────────────────────────────────────────────────────────

def test_a_blocked_user_does_not_stop_the_run(live):
    """One person blocking the bot must not cost everyone else their message."""
    for chat_id in (100, 101, 102):
        _seed_mentor(chat_id)
    update, reply, ctx = _dm(args=["confirm"])
    ctx.bot.send_message = AsyncMock(side_effect=[
        SimpleNamespace(message_id=1),
        Forbidden("bot was blocked by the user"),
        SimpleNamespace(message_id=1),
    ])

    asyncio.run(bot.broadcast_command(update, ctx))

    summary = reply.await_args_list[-1].args[0]
    assert "Delivered: 2" in summary
    assert "Failed: 1" in summary


def test_every_send_failing_still_reports(live):
    _seed_mentor(100)
    update, reply, ctx = _dm(args=["confirm"])
    ctx.bot.send_message = AsyncMock(side_effect=Exception("boom"))

    asyncio.run(bot.broadcast_command(update, ctx))

    assert "Delivered: 0" in reply.await_args_list[-1].args[0]


def test_broadcast_carries_the_event_notice_when_live(live):
    _seed_mentor(100)
    update, _, ctx = _dm(args=["confirm"])

    with patch.multiple(event_settings, LIVE=True, GROUP_ID=-1, CHANNEL_ID=-2):
        asyncio.run(bot.broadcast_command(update, ctx))

    assert "Now open" in ctx.bot.send_message.await_args.kwargs["text"]


def test_broadcast_omits_the_notice_when_the_event_is_dormant(live):
    _seed_mentor(100)
    update, _, ctx = _dm(args=["confirm"])

    asyncio.run(bot.broadcast_command(update, ctx))

    assert "Now open" not in ctx.bot.send_message.await_args.kwargs["text"]
