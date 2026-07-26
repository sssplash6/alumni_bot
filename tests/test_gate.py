# tests/test_gate.py
"""Alumni Gate: detection, the pinned self-check announcement, and onboarding."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gate
from gate import db as gdb
from gate import handlers as gh
from gate import settings

ALUMNI_GROUP = -100999
MONITORED = -100111


@pytest.fixture()
def live(tmp_path):
    """A temp DB with the gate switched on and one monitored group."""
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch.multiple(
        settings,
        LIVE=True,
        GROUP_ID=ALUMNI_GROUP,
        MONITORED_GROUP_IDS=[MONITORED],
        ANNOUNCE_INTERVAL_DAYS=5,
        INTRO_MIN_WORDS=50,
    ):
        asyncio.run(gdb.init_schema())
        yield path


def _user(uid=555, username="alice", first="Alice", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first, is_bot=is_bot)


def _ctx(member_status=None, invite="https://t.me/+newlink"):
    """A context whose bot answers get_chat_member / create_chat_invite_link."""
    bot_obj = MagicMock()
    bot_obj.username = "AlumniBot"
    if member_status is None:
        bot_obj.get_chat_member = AsyncMock(side_effect=Exception("user not found"))
    else:
        bot_obj.get_chat_member = AsyncMock(
            return_value=SimpleNamespace(status=member_status)
        )
    bot_obj.create_chat_invite_link = AsyncMock(
        return_value=SimpleNamespace(invite_link=invite)
    )
    bot_obj.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1000))
    bot_obj.pin_chat_message = AsyncMock()
    bot_obj.delete_message = AsyncMock()
    return SimpleNamespace(bot=bot_obj)


def _group_tap(user=None, chat_id=MONITORED):
    """A tap on the announcement button inside a group."""
    user = user or _user()
    query = SimpleNamespace(answer=AsyncMock(), from_user=user, message=None)
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        callback_query=query,
    ), query


def _dm(text, user=None):
    """A private message update with a captured reply mock."""
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=user or _user(),
        effective_message=SimpleNamespace(reply_text=reply),
        message=SimpleNamespace(reply_text=reply, text=text),
        callback_query=None,
    )
    return update, reply


def _intro(words=60):
    return " ".join(["word"] * words)


# ── Public surface ──────────────────────────────────────────────────────────────

def test_package_exposes_what_the_host_bot_needs():
    assert gate.MENU_BUTTON
    assert gate.START_PAYLOAD == "alumni"
    assert callable(gate.register)
    assert callable(gate.start_onboarding)
    assert callable(gate.init_schema)


# ── Detection ───────────────────────────────────────────────────────────────────

def test_non_member_gets_nudged_once(live):
    ctx = _ctx(member_status=None)
    user = _user()

    asyncio.run(gh._process_user(ctx, MONITORED, user))

    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == MONITORED
    assert "tg://user?id=555" in kwargs["text"]
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"

    # Second sighting: already classified -> no second nudge.
    ctx.bot.send_message.reset_mock()
    asyncio.run(gh._process_user(ctx, MONITORED, user))
    ctx.bot.send_message.assert_not_awaited()


def test_existing_member_not_nudged(live):
    ctx = _ctx(member_status="member")

    asyncio.run(gh._process_user(ctx, MONITORED, _user()))

    ctx.bot.send_message.assert_not_awaited()
    assert asyncio.run(gdb.get_user(555))["status"] == "member"


def test_bots_are_ignored(live):
    ctx = _ctx(member_status=None)

    asyncio.run(gh._process_user(ctx, MONITORED, _user(is_bot=True)))

    ctx.bot.send_message.assert_not_awaited()
    assert asyncio.run(gdb.get_user(555)) is None


def test_dormant_gate_does_nothing(tmp_path):
    """With the master switch off, detection must stay silent."""
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch.multiple(
        settings, LIVE=False, GROUP_ID=ALUMNI_GROUP, MONITORED_GROUP_IDS=[MONITORED]
    ):
        asyncio.run(gdb.init_schema())
        ctx = _ctx(member_status=None)

        asyncio.run(gh._process_user(ctx, MONITORED, _user()))

        ctx.bot.send_message.assert_not_awaited()
        assert asyncio.run(gdb.get_user(555)) is None


# ── The pinned announcement ─────────────────────────────────────────────────────

def test_announcement_posts_and_pins(live):
    ctx = _ctx(member_status=None)

    assert asyncio.run(gh.post_announcement(ctx, MONITORED)) is True

    markup = ctx.bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == gh.CHECK_ME_CB
    ctx.bot.pin_chat_message.assert_awaited_once()
    ctx.bot.delete_message.assert_not_awaited()  # nothing to clear on first post
    assert asyncio.run(gdb.get_announcement(MONITORED))["message_id"] == 1000


def test_announcement_replaces_previous(live):
    asyncio.run(gdb.set_announcement(MONITORED, 42))
    ctx = _ctx(member_status=None)

    asyncio.run(gh.post_announcement(ctx, MONITORED))

    ctx.bot.delete_message.assert_awaited_once_with(MONITORED, 42)
    assert asyncio.run(gdb.get_announcement(MONITORED))["message_id"] == 1000


def test_announce_job_skips_groups_not_due(live):
    asyncio.run(gdb.set_announcement(MONITORED, 42))  # posted just now
    ctx = _ctx(member_status=None)

    asyncio.run(gh.announce_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_check_me_member_is_silent_in_group(live):
    ctx = _ctx(member_status="member")
    update, query = _group_tap()

    asyncio.run(gh.on_check_me(update, ctx))

    # Nothing posted publicly — only a private popup to the tapper.
    ctx.bot.send_message.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    assert asyncio.run(gdb.get_user(555))["status"] == "member"


def test_check_me_non_member_tagged_and_deep_linked(live):
    ctx = _ctx(member_status=None)
    update, query = _group_tap()

    asyncio.run(gh.on_check_me(update, ctx))

    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == MONITORED
    assert "tg://user?id=555" in kwargs["text"]
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"
    # The tap itself opens the bot, so onboarding starts right away.
    assert query.answer.await_args.kwargs["url"].endswith("?start=alumni")


def test_check_me_repeat_tap_does_not_retag_within_cycle(live):
    ctx = _ctx(member_status=None)
    update, query = _group_tap()

    asyncio.run(gh.on_check_me(update, ctx))
    ctx.bot.send_message.reset_mock()
    asyncio.run(gh.on_check_me(update, ctx))

    ctx.bot.send_message.assert_not_awaited()
    assert query.answer.await_args.kwargs["url"].endswith("?start=alumni")


def test_check_me_registered_user_is_not_retagged(live):
    asyncio.run(
        gdb.mark_registered(555, "alice", "Alice", "Ada", "https://t.me/+old")
    )
    ctx = _ctx(member_status=None)
    update, query = _group_tap()

    asyncio.run(gh.on_check_me(update, ctx))

    ctx.bot.send_message.assert_not_awaited()
    assert query.answer.await_args.kwargs["url"].endswith("?start=alumni")


# ── Onboarding ──────────────────────────────────────────────────────────────────

def test_start_onboarding_sends_brief(live):
    ctx = _ctx(member_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"
    ctx.bot.create_chat_invite_link.assert_not_awaited()
    markup = reply.await_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(getattr(b, "callback_data", None) == gh.CHECK_FORM_CB for b in buttons)


def test_start_onboarding_when_dormant_says_coming_soon(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch.multiple(
        settings, LIVE=False, GROUP_ID=ALUMNI_GROUP
    ):
        asyncio.run(gdb.init_schema())
        ctx = _ctx(member_status=None)
        update, reply = _dm(None)

        asyncio.run(gh.start_onboarding(update, ctx))

        assert "coming soon" in reply.await_args.args[0].lower()
        assert asyncio.run(gdb.get_user(555)) is None


def test_start_onboarding_rehands_link_to_registered_non_member(live):
    """The nudge deep-links non-members into onboarding; someone who already
    finished must get their link back, not be sent to step 1."""
    asyncio.run(
        gdb.mark_registered(555, "alice", "Alice", "Ada", "https://t.me/+old")
    )
    ctx = _ctx(member_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "registered"
    ctx.bot.create_chat_invite_link.assert_not_awaited()
    markup = reply.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/+old"


def test_check_form_complete_asks_for_intro(live):
    asyncio.run(gdb.mark_awaiting_form(555, "alice", "Alice"))
    ctx = _ctx(member_status=None)
    reply = AsyncMock()
    query = SimpleNamespace(
        answer=AsyncMock(), message=SimpleNamespace(reply_text=reply)
    )
    update = SimpleNamespace(effective_user=_user(), callback_query=query)

    with patch.object(
        gh.formcheck,
        "lookup",
        AsyncMock(return_value={"complete": True, "name": "Ada Lovelace"}),
    ):
        asyncio.run(gh.on_check_form(update, ctx))

    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "awaiting_intro"
    assert row["full_name"] == "Ada Lovelace"
    ctx.bot.create_chat_invite_link.assert_not_awaited()


def test_check_form_unavailable_does_not_reject(live):
    asyncio.run(gdb.mark_awaiting_form(555, "alice", "Alice"))
    ctx = _ctx(member_status=None)
    reply = AsyncMock()
    query = SimpleNamespace(
        answer=AsyncMock(), message=SimpleNamespace(reply_text=reply)
    )
    update = SimpleNamespace(effective_user=_user(), callback_query=query)

    with patch.object(gh.formcheck, "lookup", AsyncMock(return_value=None)):
        asyncio.run(gh.on_check_form(update, ctx))

    # Left on the form step: not advanced, not rejected.
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"
    reply.assert_awaited_once()


def test_intro_admits(live):
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", "Ada Lovelace"))
    ctx = _ctx(member_status=None, invite="https://t.me/+minted")
    update, reply = _dm(_intro())

    asyncio.run(gh.on_private_text(update, ctx))

    ctx.bot.create_chat_invite_link.assert_awaited_once()
    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "registered"
    assert row["invite_link"] == "https://t.me/+minted"
    markup = reply.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/+minted"


def test_short_intro_is_rejected_and_gate_holds(live):
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", "Ada"))
    ctx = _ctx(member_status=None)
    update, reply = _dm("ok")

    asyncio.run(gh.on_private_text(update, ctx))

    ctx.bot.create_chat_invite_link.assert_not_awaited()
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_intro"
    assert "short" in reply.await_args.args[0].lower()


def test_intro_at_exactly_the_minimum_is_accepted(live):
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", "Ada"))
    ctx = _ctx(member_status=None)
    update, _ = _dm(_intro(settings.INTRO_MIN_WORDS))

    asyncio.run(gh.on_private_text(update, ctx))

    ctx.bot.create_chat_invite_link.assert_awaited_once()
    assert asyncio.run(gdb.get_user(555))["status"] == "registered"


def test_non_text_intro_gets_told_to_use_text(live):
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", "Ada"))
    ctx = _ctx(member_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.on_private_non_text(update, ctx))

    ctx.bot.create_chat_invite_link.assert_not_awaited()
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_intro"
    assert "text message" in reply.await_args.args[0].lower()


def test_text_from_unknown_user_ignored(live):
    ctx = _ctx(member_status=None)
    update, reply = _dm("hello")

    asyncio.run(gh.on_private_text(update, ctx))

    reply.assert_not_awaited()
    assert asyncio.run(gdb.get_user(555)) is None


# ── Background poll ─────────────────────────────────────────────────────────────

def test_poll_advances_completed_to_intro(live):
    asyncio.run(gdb.mark_awaiting_form(111, "u1", "One"))
    asyncio.run(gdb.mark_awaiting_form(222, "u2", "Two"))
    ctx = _ctx(member_status=None)

    with patch.object(settings, "airtable_ready", lambda: True), patch.object(
        gh.formcheck,
        "fetch_completed",
        AsyncMock(return_value={"111": "One Fullname"}),
    ):
        asyncio.run(gh.poll_forms(ctx))

    moved = asyncio.run(gdb.get_user(111))
    assert moved["status"] == "awaiting_intro"
    assert moved["full_name"] == "One Fullname"
    # Not in Airtable yet.
    assert asyncio.run(gdb.get_user(222))["status"] == "awaiting_form"
    ctx.bot.send_message.assert_awaited_once()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 111
