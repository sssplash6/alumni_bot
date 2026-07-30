# tests/test_event.py
"""Event registration: the two-chat gate, the join follow-up, and admin utilities."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError
from telegram.ext import ConversationHandler

import event
from event import db as edb
from event import handlers as eh
from event import settings

GROUP = -1001308713514
CHANNEL = -1001572190121
ADMIN = 1
POST_CHAT = -100777
POST_MESSAGE = 4242


@pytest.fixture()
def live(tmp_path):
    """A temp DB with the event switched on and a post already set."""
    path = str(tmp_path / "test.db")
    # ADMIN_IDS is patched because config.py load_dotenv()s the developer's real
    # .env at import time; without this the admin commands are tested against
    # whoever happens to be in that file.
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path), patch(
        "event.handlers.ADMIN_IDS", [ADMIN]
    ), patch.multiple(
        settings, LIVE=True, GROUP_ID=GROUP, CHANNEL_ID=CHANNEL
    ):
        asyncio.run(edb.init_schema())
        asyncio.run(edb.set_post(POST_CHAT, POST_MESSAGE))
        yield path


def _user(uid=555, username="alice", first="Alice", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first, is_bot=is_bot)


def _ctx(in_group=True, in_channel=True, invite="https://t.me/+onetime"):
    """A context answering get_chat_member per chat.

    Absence is signalled with BadRequest because that is how Telegram reports it,
    and the code treats a bare exception as "couldn't ask" instead.
    """
    bot_obj = MagicMock()
    bot_obj.username = "AlumniBot"

    def _member(chat_id, user_id):
        present = in_group if chat_id == GROUP else in_channel
        if not present:
            raise BadRequest("user not found")
        return SimpleNamespace(status="member")

    bot_obj.get_chat_member = AsyncMock(side_effect=_member)
    bot_obj.create_chat_invite_link = AsyncMock(
        return_value=SimpleNamespace(invite_link=invite)
    )
    bot_obj.copy_message = AsyncMock(return_value=SimpleNamespace(message_id=99))
    bot_obj.send_message = AsyncMock(return_value=SimpleNamespace(message_id=100))
    return SimpleNamespace(bot=bot_obj)


def _dm(text=None, user=None):
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=user or _user(),
        effective_message=SimpleNamespace(reply_text=reply, reply_to_message=None),
        message=SimpleNamespace(reply_text=reply, text=text, reply_to_message=None),
        callback_query=None,
    )
    return update, reply


def _joined(chat_id=CHANNEL, user=None, status="member"):
    """A chat_member update for someone arriving in one of the two chats."""
    return SimpleNamespace(
        chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, title="Alumni"),
            new_chat_member=SimpleNamespace(status=status, user=user or _user()),
            old_chat_member=SimpleNamespace(status="left"),
        )
    )


# ── Public surface ──────────────────────────────────────────────────────────────

def test_package_exposes_what_the_host_bot_needs():
    assert event.MENU_BUTTON
    assert event.START_PAYLOAD == "event"
    assert callable(event.register)
    assert callable(event.start_registration)
    assert callable(event.init_schema)


# ── The gate ────────────────────────────────────────────────────────────────────

def test_in_both_chats_gets_the_post_and_is_asked_for_a_name(live):
    ctx = _ctx(in_group=True, in_channel=True)
    update, reply = _dm()

    state = asyncio.run(eh.start_registration(update, ctx))

    assert state == eh.ASK_NAME
    ctx.bot.copy_message.assert_awaited_once()
    kwargs = ctx.bot.copy_message.await_args.kwargs
    assert kwargs["from_chat_id"] == POST_CHAT
    assert kwargs["message_id"] == POST_MESSAGE
    assert asyncio.run(edb.get_user(555))["status"] == "awaiting_name"


def test_the_refusal_names_who_to_contact(live):
    """"Contact an admin" leaves someone who believes they belong nowhere to go."""
    from config import ADMIN_CONTACT

    from event import messages as emsg

    assert ADMIN_CONTACT.startswith("@")
    assert ADMIN_CONTACT in emsg.NOT_ALUMNI
    assert "an admin" not in emsg.NOT_ALUMNI


def test_in_neither_chat_is_told_the_event_is_alumni_only(live):
    ctx = _ctx(in_group=False, in_channel=False)
    update, reply = _dm()

    state = asyncio.run(eh.start_registration(update, ctx))

    assert state == ConversationHandler.END
    assert "alumni only" in reply.await_args.args[0].lower()
    # No link is offered, and nothing is recorded.
    ctx.bot.create_chat_invite_link.assert_not_awaited()
    assert asyncio.run(edb.get_user(555)) is None


def test_in_group_but_not_channel_gets_a_channel_link(live):
    ctx = _ctx(in_group=True, in_channel=False)
    update, reply = _dm()

    state = asyncio.run(eh.start_registration(update, ctx))

    assert state == ConversationHandler.END
    assert ctx.bot.create_chat_invite_link.await_args.kwargs["chat_id"] == CHANNEL
    assert ctx.bot.create_chat_invite_link.await_args.kwargs["member_limit"] == 1
    row = asyncio.run(edb.get_user(555))
    assert row["status"] == "awaiting_join"
    assert row["missing"] == "channel"
    assert "channel" in reply.await_args.args[0]


def test_in_channel_but_not_group_gets_a_group_link(live):
    ctx = _ctx(in_group=False, in_channel=True)
    update, reply = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    assert ctx.bot.create_chat_invite_link.await_args.kwargs["chat_id"] == GROUP
    assert asyncio.run(edb.get_user(555))["missing"] == "group"


def test_the_post_is_not_shown_to_someone_still_missing_a_chat(live):
    """The post is the thing being gated, so it must not leak to a half-eligible."""
    ctx = _ctx(in_group=True, in_channel=False)
    update, _ = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    ctx.bot.copy_message.assert_not_awaited()


def test_outage_is_not_a_refusal(live):
    """A network blip must not tell a real alum the event isn't for them."""
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(side_effect=NetworkError("boom"))
    update, reply = _dm()

    state = asyncio.run(eh.start_registration(update, ctx))

    text = reply.await_args.args[0].lower()
    assert state == ConversationHandler.END
    assert "couldn't check" in text
    assert "alumni only" not in text
    assert asyncio.run(edb.get_user(555)) is None


def test_not_being_admin_in_a_chat_is_not_a_refusal(live):
    """Forbidden is our misconfiguration, so it can't read as 'not a member'."""
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(side_effect=Forbidden("not enough rights"))
    update, reply = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    assert "couldn't check" in reply.await_args.args[0].lower()


def test_one_unreadable_chat_blocks_the_whole_answer(live):
    """Not knowing about one chat can't be rounded to 'in both' or 'needs a link'."""
    ctx = _ctx()

    def _member(chat_id, user_id):
        if chat_id == CHANNEL:
            raise NetworkError("boom")
        return SimpleNamespace(status="member")

    ctx.bot.get_chat_member = AsyncMock(side_effect=_member)
    update, reply = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    assert "couldn't check" in reply.await_args.args[0].lower()
    ctx.bot.create_chat_invite_link.assert_not_awaited()


def test_restricted_counts_as_being_in_the_chat(live):
    """Muted in the group is still being in it."""
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status="restricted")
    )
    update, _ = _dm()

    assert asyncio.run(eh.start_registration(update, ctx)) == eh.ASK_NAME


def test_left_does_not_count(live):
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="left"))
    update, reply = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    assert "alumni only" in reply.await_args.args[0].lower()


def test_link_failure_is_reported_and_nothing_is_recorded(live):
    ctx = _ctx(in_group=True, in_channel=False)
    ctx.bot.create_chat_invite_link = AsyncMock(side_effect=Exception("nope"))
    update, reply = _dm()

    asyncio.run(eh.start_registration(update, ctx))

    assert "went wrong" in reply.await_args.args[0].lower()
    assert asyncio.run(edb.get_user(555)) is None


# ── Dormancy ────────────────────────────────────────────────────────────────────

def test_dormant_says_coming_soon(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path), \
            patch.multiple(settings, LIVE=False, GROUP_ID=GROUP, CHANNEL_ID=CHANNEL):
        asyncio.run(edb.init_schema())
        ctx = _ctx()
        update, reply = _dm()

        asyncio.run(eh.start_registration(update, ctx))

        assert "coming soon" in reply.await_args.args[0].lower()
        ctx.bot.get_chat_member.assert_not_awaited()


def test_unconfigured_chats_say_so(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path), \
            patch.multiple(settings, LIVE=True, GROUP_ID=GROUP, CHANNEL_ID=0):
        asyncio.run(edb.init_schema())
        ctx = _ctx()
        update, reply = _dm()

        asyncio.run(eh.start_registration(update, ctx))

        assert "hasn't been set up" in reply.await_args.args[0].lower()


def test_no_post_set_refuses_rather_than_asking_for_a_name(tmp_path):
    """Registering someone who never saw the post would be meaningless."""
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path), \
            patch.multiple(settings, LIVE=True, GROUP_ID=GROUP, CHANNEL_ID=CHANNEL):
        asyncio.run(edb.init_schema())
        ctx = _ctx()
        update, reply = _dm()

        state = asyncio.run(eh.start_registration(update, ctx))

        assert state == ConversationHandler.END
        assert "no event post" in reply.await_args.args[0].lower()
        assert asyncio.run(edb.get_user(555)) is None


# ── Name collection ─────────────────────────────────────────────────────────────

def test_name_completes_registration(live):
    ctx = _ctx()
    update, _ = _dm()
    asyncio.run(eh.start_registration(update, ctx))

    named, reply = _dm(text="Miroli Karimov")
    state = asyncio.run(eh.got_name(named, ctx))

    assert state == ConversationHandler.END
    row = asyncio.run(edb.get_user(555))
    assert row["status"] == "registered"
    assert row["full_name"] == "Miroli Karimov"
    assert "registered" in reply.await_args.args[0].lower()


def test_blank_name_is_asked_again(live):
    ctx = _ctx()
    update, _ = _dm()
    asyncio.run(eh.start_registration(update, ctx))

    named, reply = _dm(text="   ")
    state = asyncio.run(eh.got_name(named, ctx))

    assert state == eh.ASK_NAME
    assert asyncio.run(edb.get_user(555))["status"] == "awaiting_name"


def test_already_registered_is_not_asked_again(live):
    asyncio.run(edb.mark_registered(555, "alice", "Alice", "Alice Smith"))
    ctx = _ctx()
    update, reply = _dm()

    state = asyncio.run(eh.start_registration(update, ctx))

    assert state == ConversationHandler.END
    assert "already registered" in reply.await_args.args[0].lower()
    ctx.bot.copy_message.assert_not_awaited()


def test_a_name_with_html_is_escaped(live):
    ctx = _ctx()
    update, _ = _dm()
    asyncio.run(eh.start_registration(update, ctx))

    named, reply = _dm(text="<b>Bold</b> Name")
    asyncio.run(eh.got_name(named, ctx))

    assert "&lt;b&gt;" in reply.await_args.args[0]


# ── The join follow-up ──────────────────────────────────────────────────────────

def test_joining_the_missing_chat_nudges_them_to_continue(live):
    ctx = _ctx(in_group=True, in_channel=False)
    update, _ = _dm()
    asyncio.run(eh.start_registration(update, ctx))
    assert asyncio.run(edb.get_user(555))["status"] == "awaiting_join"

    # They join the channel, so now they're in both.
    ctx2 = _ctx(in_group=True, in_channel=True)
    asyncio.run(eh.on_chat_member(_joined(CHANNEL), ctx2))

    kwargs = ctx2.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 555
    assert "in both" in kwargs["text"].lower()
    # The button is the only way on: the conversation isn't active out here, so a
    # typed name would land on no handler.
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(b.callback_data == eh.ENTER_CB for b in buttons)
    # The post is not sent from here — start_registration owns that.
    ctx2.bot.copy_message.assert_not_awaited()


def test_the_nudge_button_completes_the_whole_journey(live):
    """group-only -> link -> joins channel -> taps Finish -> post -> name -> done."""
    ctx = _ctx(in_group=True, in_channel=False)
    update, _ = _dm()
    asyncio.run(eh.start_registration(update, ctx))

    ctx2 = _ctx(in_group=True, in_channel=True)
    asyncio.run(eh.on_chat_member(_joined(CHANNEL), ctx2))

    # Tapping the nudge re-enters the conversation, which is what sends the post.
    tapped, reply = _dm()
    tapped.callback_query = SimpleNamespace(answer=AsyncMock())
    state = asyncio.run(eh.start_registration(tapped, ctx2))

    assert state == eh.ASK_NAME
    ctx2.bot.copy_message.assert_awaited_once()
    assert asyncio.run(edb.get_user(555))["status"] == "awaiting_name"

    named, done_reply = _dm(text="Miroli Karimov")
    asyncio.run(eh.got_name(named, ctx2))

    row = asyncio.run(edb.get_user(555))
    assert row["status"] == "registered"
    assert row["full_name"] == "Miroli Karimov"


def test_join_is_ignored_for_someone_not_mid_registration(live):
    """Otherwise every new arrival in either chat would get an unsolicited DM."""
    ctx = _ctx()

    asyncio.run(eh.on_chat_member(_joined(CHANNEL), ctx))

    ctx.bot.send_message.assert_not_awaited()
    assert asyncio.run(edb.get_user(555)) is None


def test_join_that_still_leaves_a_chat_missing_does_nothing(live):
    """They joined one they were already in, or left the other meanwhile."""
    asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
    ctx = _ctx(in_group=True, in_channel=False)

    asyncio.run(eh.on_chat_member(_joined(CHANNEL), ctx))

    assert asyncio.run(edb.get_user(555))["status"] == "awaiting_join"
    ctx.bot.copy_message.assert_not_awaited()


def test_join_in_an_unrelated_chat_is_ignored(live):
    asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
    ctx = _ctx()

    asyncio.run(eh.on_chat_member(_joined(-100999), ctx))

    ctx.bot.get_chat_member.assert_not_awaited()


def test_a_bot_joining_is_ignored(live):
    asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
    ctx = _ctx()
    bot_user = _user(uid=555, is_bot=True)

    asyncio.run(eh.on_chat_member(_joined(CHANNEL, user=bot_user), ctx))

    ctx.bot.copy_message.assert_not_awaited()


def test_someone_leaving_is_not_treated_as_joining(live):
    asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
    ctx = _ctx()

    asyncio.run(eh.on_chat_member(_joined(CHANNEL, status="left"), ctx))

    ctx.bot.copy_message.assert_not_awaited()


def test_join_follow_up_is_dormant_when_switched_off(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path), \
            patch.multiple(settings, LIVE=False, GROUP_ID=GROUP, CHANNEL_ID=CHANNEL):
        asyncio.run(edb.init_schema())
        asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
        ctx = _ctx()

        asyncio.run(eh.on_chat_member(_joined(CHANNEL), ctx))

        ctx.bot.get_chat_member.assert_not_awaited()


# ── Admin ───────────────────────────────────────────────────────────────────────

def _admin_dm(text=None, reply_to=None, uid=ADMIN):
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="admin", first_name="A"),
        effective_message=SimpleNamespace(reply_text=reply),
        message=SimpleNamespace(reply_text=reply, text=text, reply_to_message=reply_to),
        callback_query=None,
    )
    return update, reply


def test_set_post_stores_the_replied_to_message(live):
    target = SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=77)
    update, reply = _admin_dm(reply_to=target)

    asyncio.run(eh.set_post_command(update, _ctx()))

    assert asyncio.run(edb.get_post()) == (-100123, 77)
    assert "saved" in reply.await_args.args[0].lower()


def test_set_post_without_a_reply_explains_itself(live):
    update, reply = _admin_dm(reply_to=None)

    asyncio.run(eh.set_post_command(update, _ctx()))

    assert "reply to" in reply.await_args.args[0].lower()


def test_set_post_ignores_non_admins(live):
    target = SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=77)
    update, reply = _admin_dm(reply_to=target, uid=999999)

    asyncio.run(eh.set_post_command(update, _ctx()))

    reply.assert_not_awaited()
    assert asyncio.run(edb.get_post()) == (POST_CHAT, POST_MESSAGE)


def test_list_shows_registered_names(live):
    asyncio.run(edb.mark_registered(1, "alice", "Alice", "Alice Smith"))
    asyncio.run(edb.mark_registered(2, None, "Bob", "Bob Jones"))
    update, reply = _admin_dm()

    asyncio.run(eh.list_command(update, _ctx()))

    text = reply.await_args.args[0]
    assert "Alice Smith" in text and "@alice" in text
    assert "Bob Jones" in text


def test_list_excludes_the_half_finished(live):
    asyncio.run(edb.mark_awaiting_join(1, "alice", "Alice", "channel"))
    asyncio.run(edb.mark_awaiting_name(2, "bob", "Bob"))
    update, reply = _admin_dm()

    asyncio.run(eh.list_command(update, _ctx()))

    assert "no event registrations" in reply.await_args.args[0].lower()


def test_list_ignores_non_admins(live):
    asyncio.run(edb.mark_registered(1, "alice", "Alice", "Alice Smith"))
    update, reply = _admin_dm(uid=999999)

    asyncio.run(eh.list_command(update, _ctx()))

    reply.assert_not_awaited()


def test_stats_counts_each_status(live):
    asyncio.run(edb.mark_registered(1, "a", "A", "A A"))
    asyncio.run(edb.mark_awaiting_join(2, "b", "B", "channel"))
    asyncio.run(edb.mark_awaiting_name(3, "c", "C"))
    update, reply = _admin_dm()

    asyncio.run(eh.stats_command(update, _ctx()))

    text = reply.await_args.args[0]
    assert "Registered: 1" in text
    assert "other chat: 1" in text
    assert "not yet answered: 1" in text


# ── Persistence ─────────────────────────────────────────────────────────────────

def test_reentering_does_not_blank_an_earlier_name(live):
    """COALESCE keeps full_name when a later write doesn't carry one."""
    asyncio.run(edb.mark_registered(555, "alice", "Alice", "Alice Smith"))
    asyncio.run(edb.mark_awaiting_name(555, "alice", "Alice"))

    assert asyncio.run(edb.get_user(555))["full_name"] == "Alice Smith"


def test_missing_marker_survives_later_writes(live):
    asyncio.run(edb.mark_awaiting_join(555, "alice", "Alice", "channel"))
    asyncio.run(edb.mark_awaiting_name(555, "alice", "Alice"))

    assert asyncio.run(edb.get_user(555))["missing"] == "channel"


def test_get_post_is_none_until_set(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("event.db.DB_PATH", path):
        asyncio.run(edb.init_schema())
        assert asyncio.run(edb.get_post()) is None


def test_counts_are_zero_filled(live):
    counts = asyncio.run(edb.counts())
    assert counts == {"awaiting_join": 0, "awaiting_name": 0, "registered": 0}
