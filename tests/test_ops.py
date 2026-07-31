# tests/test_ops.py
"""Operational safety nets: database snapshots and admin error alerts."""
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import database as db


@pytest.fixture()
def temp_db(tmp_path):
    """A temp database with snapshots directed into the same temp dir."""
    path = str(tmp_path / "test.db")
    backups = str(tmp_path / "backups")
    with patch("database.DB_PATH", path), patch("database.BACKUP_DIR", backups):
        asyncio.run(db.init_db())
        yield SimpleNamespace(path=path, backups=backups)


def _ctx():
    bot_obj = MagicMock()
    bot_obj.send_message = AsyncMock()
    return SimpleNamespace(bot=bot_obj, error=None)


# ── Backups ─────────────────────────────────────────────────────────────────────

def test_backup_produces_a_readable_copy(temp_db):
    async def run():
        await db.set_applications_open(True)
        return await db.backup()

    path = asyncio.run(run())

    assert path is not None
    copy = Path(path)
    assert copy.exists() and copy.stat().st_size > 0
    # A real database carrying the row, not an empty file.
    with sqlite3.connect(copy) as conn:
        value = conn.execute(
            "SELECT value FROM settings WHERE key = 'applications_open'"
        ).fetchone()
    assert value == ("1",)


def test_backup_prunes_to_the_keep_limit(temp_db):
    """Rapid snapshots must be distinct files (same-second names would collide)
    and the oldest beyond `keep` must be pruned."""
    async def run():
        return [await db.backup(keep=2) for _ in range(4)]

    paths = asyncio.run(run())

    assert len(set(paths)) == 4, "snapshots overwrote each other"
    remaining = sorted(Path(temp_db.backups).glob("test-*.db"))
    assert [str(p) for p in remaining] == paths[-2:]


def test_backup_without_a_database_returns_none(tmp_path):
    missing = str(tmp_path / "nope.db")
    with patch("database.DB_PATH", missing), patch(
        "database.BACKUP_DIR", str(tmp_path / "backups")
    ):
        assert asyncio.run(db.backup()) is None


# ── Error alerts ────────────────────────────────────────────────────────────────

def _reset_throttle():
    bot._last_error_alert = None


def test_error_handler_alerts_admins():
    _reset_throttle()
    ctx = _ctx()
    ctx.error = ValueError("something broke")
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100111),
        effective_user=SimpleNamespace(id=555),
    )

    asyncio.run(bot.on_error(update, ctx))

    ctx.bot.send_message.assert_awaited()
    text = ctx.bot.send_message.await_args.kwargs["text"]
    assert "something broke" in text
    assert "-100111" in text


def test_error_alerts_are_throttled():
    _reset_throttle()
    ctx = _ctx()
    ctx.error = ValueError("boom")
    update = SimpleNamespace(effective_chat=None, effective_user=None)

    async def run():
        for _ in range(3):
            await bot.on_error(update, ctx)

    asyncio.run(run())

    # One alert per admin, not three rounds of them.
    assert ctx.bot.send_message.await_count == len(bot.ADMIN_IDS)


def test_error_handler_survives_undeliverable_admin_dm():
    """An admin who never started the bot can't be DMed; that must not raise."""
    _reset_throttle()
    ctx = _ctx()
    ctx.error = ValueError("boom")
    ctx.bot.send_message = AsyncMock(side_effect=Exception("chat not found"))
    update = SimpleNamespace(effective_chat=None, effective_user=None)

    asyncio.run(bot.on_error(update, ctx))  # must not raise


def test_error_handler_without_exception_attached():
    _reset_throttle()
    ctx = _ctx()
    ctx.error = None
    update = SimpleNamespace(effective_chat=None, effective_user=None)

    asyncio.run(bot.on_error(update, ctx))

    ctx.bot.send_message.assert_awaited()


# ── Setup helpers ───────────────────────────────────────────────────────────────

def test_id_command_in_a_group_answers_in_the_admins_dm():
    """The bot must not leave configuration chatter in a community group."""
    ctx = _ctx()
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(
            id=-100222, type="supergroup", title="Cohort 2024", full_name=None
        ),
        effective_user=SimpleNamespace(id=bot.ADMIN_IDS[0]),
        effective_message=SimpleNamespace(reply_text=reply),
    )

    asyncio.run(bot.id_command(update, ctx))

    reply.assert_not_awaited()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == bot.ADMIN_IDS[0]
    assert "-100222" in kwargs["text"]


def test_id_command_replies_in_place_in_a_dm():
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(
            id=777, type="private", title=None, full_name="Alice"
        ),
        effective_user=SimpleNamespace(id=bot.ADMIN_IDS[0]),
        effective_message=SimpleNamespace(reply_text=reply),
    )

    asyncio.run(bot.id_command(update, _ctx()))

    assert "777" in reply.await_args.args[0]


def test_id_command_ignores_non_admins_in_groups():
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(
            id=-100222, type="supergroup", title="G", full_name=None
        ),
        effective_user=SimpleNamespace(id=999999),
        effective_message=SimpleNamespace(reply_text=reply),
    )

    asyncio.run(bot.id_command(update, _ctx()))

    reply.assert_not_awaited()


def test_added_to_group_notifies_admins():
    ctx = _ctx()
    ctx.bot.id = 42
    update = SimpleNamespace(
        my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=-100333, type="supergroup", title="New Group"),
            old_chat_member=SimpleNamespace(status="left"),
            new_chat_member=SimpleNamespace(
                status="administrator", user=SimpleNamespace(id=42)
            ),
        )
    )

    asyncio.run(bot.on_my_chat_member(update, ctx))

    ctx.bot.send_message.assert_awaited()
    assert "-100333" in ctx.bot.send_message.await_args.kwargs["text"]
