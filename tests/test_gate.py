# tests/test_gate.py
"""Alumni Gate: detection, the pinned self-check announcement, and onboarding."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError

import gate
from gate import db as gdb
from gate import handlers as gh
from gate import settings

ALUMNI_GROUP = -100999
MONITORED = -100111
ADMIN = 1


@pytest.fixture()
def live(tmp_path):
    """A temp DB with the gate switched on and one monitored group."""
    path = str(tmp_path / "test.db")
    # ADMIN_IDS is patched because config.py load_dotenv()s the developer's real
    # .env at import time. Without this the admin commands below are tested
    # against whoever happens to be in that file, so they pass on a machine with
    # no .env and fail on the machine that has one.
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch(
        "gate.handlers.ADMIN_IDS", [ADMIN]
    ), patch.multiple(
        settings,
        LIVE=True,
        GROUP_ID=ALUMNI_GROUP,
        MONITORED_GROUP_IDS=[MONITORED],
        ANNOUNCE_INTERVAL_DAYS=5,
        INTRO_MIN_WORDS=50,
        REQUIRE_WATCHED_GROUP=True,
    ):
        asyncio.run(gdb.init_schema())
        # The watched-group list lives in the DB and is cached in memory, so it
        # has to be primed the same way startup does it.
        asyncio.run(gh.load_monitored([MONITORED]))
        try:
            yield path
        finally:
            gh._monitored = set()


def _user(uid=555, username="alice", first="Alice", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first, is_bot=is_bot)


def _ctx(member_status=None, invite="https://t.me/+newlink", watched_status="member"):
    """A context whose bot answers get_chat_member / create_chat_invite_link.

    The two groups are answered separately, because they ask opposite questions:
    ``member_status`` is the status in the *alumni* group (None = not in it, which
    is what makes someone a candidate), while ``watched_status`` is the status in
    every *watched* group and decides eligibility. It defaults to "member" so
    onboarding tests stay about onboarding; pass None for someone in no group.

    Absence is signalled with BadRequest rather than a bare Exception because
    that is how Telegram reports it, and the gate treats the two differently — a
    bare Exception means "couldn't ask", not "not a member".
    """
    bot_obj = MagicMock()
    bot_obj.username = "AlumniBot"

    def _member(chat_id, user_id):
        status = member_status if chat_id == settings.GROUP_ID else watched_status
        if status is None:
            raise BadRequest("user not found")
        return SimpleNamespace(status=status)

    bot_obj.get_chat_member = AsyncMock(side_effect=_member)
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
    assert markup.inline_keyboard[0][0].callback_data == gh.JOIN_CB
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


def test_announcement_offers_both_buttons(live):
    ctx = _ctx(member_status=None)

    asyncio.run(gh.post_announcement(ctx, MONITORED))

    markup = ctx.bot.send_message.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == [gh.JOIN_CB, gh.ALREADY_CB]


def test_join_tap_by_non_member_opens_the_bot_silently(live):
    """They volunteered, so there's nothing to tag them about publicly."""
    ctx = _ctx(member_status=None)
    update, query = _group_tap()

    asyncio.run(gh.on_join_tap(update, ctx))

    ctx.bot.send_message.assert_not_awaited()
    assert query.answer.await_args.kwargs["url"].endswith("?start=alumni")


def test_join_tap_by_existing_member_says_so(live):
    ctx = _ctx(member_status="member")
    update, query = _group_tap()

    asyncio.run(gh.on_join_tap(update, ctx))

    ctx.bot.send_message.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    assert asyncio.run(gdb.get_user(555))["status"] == "member"


def test_already_tap_is_verified_not_trusted(live):
    """The claim is checked. Someone who taps this to dismiss the message, but
    isn't actually in the group, is corrected rather than quietly skipped."""
    ctx = _ctx(member_status=None)   # not actually in the alumni group
    update, query = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    # A private popup for them...
    text = query.answer.await_args.args[0]
    assert "can't find" in text.lower()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    # ...and recorded as chased, never as a member.
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"


def test_already_tap_when_true_records_membership(live):
    ctx = _ctx(member_status="member")
    update, query = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "member"
    ctx.bot.send_message.assert_not_awaited()


def test_already_tap_accepts_every_present_status(live):
    """Creator, admin and restricted all count as being in the group."""
    for status in ("creator", "administrator", "member", "restricted"):
        ctx = _ctx(member_status=status)
        update, _ = _group_tap(user=_user(uid=600))
        asyncio.run(gh.on_already_tap(update, ctx))
        assert asyncio.run(gdb.get_user(600))["status"] == "member", status


def test_taps_are_inert_while_dormant(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch.multiple(
        settings, LIVE=False, GROUP_ID=ALUMNI_GROUP, MONITORED_GROUP_IDS=[MONITORED]
    ):
        asyncio.run(gdb.init_schema())
        for handler in (gh.on_join_tap, gh.on_already_tap):
            ctx = _ctx(member_status="member")
            update, query = _group_tap()
            asyncio.run(handler(update, ctx))
            assert asyncio.run(gdb.get_user(555)) is None
            ctx.bot.send_message.assert_not_awaited()


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


def test_stranger_in_no_watched_group_is_refused(live):
    """The bot's username is public, so this is the door a stranger walks up to."""
    ctx = _ctx(member_status=None, watched_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert "can't find you" in reply.await_args.args[0].lower()
    # No row: they must not occupy a slot the form poll then asks Airtable about,
    # and nudge-once must not lock them out of a later legitimate attempt.
    assert asyncio.run(gdb.get_user(555)) is None
    ctx.bot.create_chat_invite_link.assert_not_awaited()


def test_member_of_a_watched_group_proceeds(live):
    ctx = _ctx(member_status=None, watched_status="member")
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"


def test_restricted_in_a_watched_group_still_counts(live):
    """Muted in a community group is still being in it."""
    ctx = _ctx(member_status=None, watched_status="restricted")
    update, _ = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"


def test_left_a_watched_group_does_not_count(live):
    ctx = _ctx(member_status=None, watched_status="left")
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert "can't find you" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(555)) is None


def test_eligibility_outage_is_not_a_refusal(live):
    """A network blip must not tell a real member they don't belong."""
    ctx = _ctx(member_status=None)
    ctx.bot.get_chat_member = AsyncMock(side_effect=NetworkError("boom"))
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    text = reply.await_args.args[0].lower()
    assert "couldn't check" in text
    assert "can't find you" not in text
    assert asyncio.run(gdb.get_user(555)) is None


def test_losing_admin_in_a_watched_group_is_not_an_answer(live):
    """Forbidden is our misconfiguration, so it can't be read as 'not in it'."""
    ctx = _ctx(member_status=None)
    ctx.bot.get_chat_member = AsyncMock(side_effect=Forbidden("not enough rights"))
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert "couldn't check" in reply.await_args.args[0].lower()


def test_no_watched_groups_refuses_rather_than_admitting_everyone(live):
    asyncio.run(gdb.remove_monitored_chat(MONITORED))
    asyncio.run(gh.refresh_monitored())
    ctx = _ctx(member_status=None, watched_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert "couldn't check" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(555)) is None
    # Only the alumni-membership check ran; eligibility had nowhere to ask.
    assert ctx.bot.get_chat_member.await_count == 1


def test_eligibility_stops_at_the_first_hit(live):
    """Cost is one call for most people, not one per watched group."""
    for extra in (-100222, -100333, -100444):
        asyncio.run(gdb.add_monitored_chat(extra, "Extra"))
    asyncio.run(gh.refresh_monitored())
    ctx = _ctx(member_status=None, watched_status="member")
    update, _ = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    # One for the alumni-membership check, one for the first watched group.
    assert ctx.bot.get_chat_member.await_count == 2


def test_require_watched_group_can_be_disabled(live, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_WATCHED_GROUP", False)
    ctx = _ctx(member_status=None, watched_status=None)
    update, _ = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"


def test_existing_alumni_member_skips_the_eligibility_check(live):
    """Already in the destination — eligibility is moot and costs nothing."""
    ctx = _ctx(member_status="member", watched_status=None)
    update, reply = _dm(None)

    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "member"
    assert ctx.bot.get_chat_member.await_count == 1


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
        "fetch_completed_for",
        AsyncMock(
            return_value={"by_id": {"111": "One Fullname"}, "by_username": {}}
        ),
    ):
        asyncio.run(gh.poll_forms(ctx))

    moved = asyncio.run(gdb.get_user(111))
    assert moved["status"] == "awaiting_intro"
    assert moved["full_name"] == "One Fullname"
    # Not in Airtable yet.
    assert asyncio.run(gdb.get_user(222))["status"] == "awaiting_form"
    ctx.bot.send_message.assert_awaited_once()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 111


# ── Legacy backlog: rows that predate tg_id ─────────────────────────────────────

def test_poll_matches_legacy_row_by_username(live):
    """A student whose submission predates tg_id must still be advanced — with
    900+ historical rows, re-submitting is not an option."""
    asyncio.run(gdb.mark_awaiting_form(111, "Alecc_lefk", "Alec"))
    ctx = _ctx(member_status=None)

    with patch.object(settings, "airtable_ready", lambda: True), patch.object(
        gh.formcheck,
        "fetch_completed_for",
        AsyncMock(
            return_value={
                "by_id": {},
                "by_username": {"alecc_lefk": "Alec Lefkowitz"},
            }
        ),
    ):
        asyncio.run(gh.poll_forms(ctx))

    moved = asyncio.run(gdb.get_user(111))
    assert moved["status"] == "awaiting_intro"
    assert moved["full_name"] == "Alec Lefkowitz"


def test_poll_prefers_tg_id_over_username(live):
    asyncio.run(gdb.mark_awaiting_form(111, "alice", "Alice"))
    ctx = _ctx(member_status=None)

    with patch.object(settings, "airtable_ready", lambda: True), patch.object(
        gh.formcheck,
        "fetch_completed_for",
        AsyncMock(
            return_value={
                "by_id": {"111": "From tg_id"},
                "by_username": {"alice": "From username"},
            }
        ),
    ):
        asyncio.run(gh.poll_forms(ctx))

    assert asyncio.run(gdb.get_user(111))["full_name"] == "From tg_id"


def test_poll_ignores_users_with_no_username_and_no_id_match(live):
    asyncio.run(gdb.mark_awaiting_form(111, None, "NoHandle"))
    ctx = _ctx(member_status=None)

    with patch.object(settings, "airtable_ready", lambda: True), patch.object(
        gh.formcheck,
        "fetch_completed_for",
        AsyncMock(return_value={"by_id": {}, "by_username": {"someone": None}}),
    ):
        asyncio.run(gh.poll_forms(ctx))

    assert asyncio.run(gdb.get_user(111))["status"] == "awaiting_form"
    ctx.bot.send_message.assert_not_awaited()


def test_check_form_passes_username_for_legacy_matching(live):
    """on_check_form must hand the username to lookup(), or the 900-row backlog
    can only ever be matched by the poll."""
    asyncio.run(gdb.mark_awaiting_form(555, "alice", "Alice"))
    ctx = _ctx(member_status=None)
    reply = AsyncMock()
    query = SimpleNamespace(
        answer=AsyncMock(), message=SimpleNamespace(reply_text=reply)
    )
    update = SimpleNamespace(effective_user=_user(), callback_query=query)
    seen = {}

    async def fake_lookup(tg_id, username=None):
        seen["tg_id"], seen["username"] = tg_id, username
        return {"complete": True, "name": "Ada", "matched_by": "username"}

    with patch.object(gh.formcheck, "lookup", fake_lookup):
        asyncio.run(gh.on_check_form(update, ctx))

    assert seen == {"tg_id": 555, "username": "alice"}
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_intro"


def test_existing_alumni_member_never_touches_airtable(live):
    """The ~900 historical submitters are already in the alumni group. The
    membership check must short-circuit before any Airtable lookup, so they are
    classified silently and never asked to fill in a form again."""
    ctx = _ctx(member_status="member")
    called = []

    async def fake_lookup(*a, **kw):
        called.append(a)
        return {"complete": False, "name": None, "matched_by": None}

    with patch.object(gh.formcheck, "lookup", fake_lookup):
        # Every entry point an existing member could arrive through.
        update, reply = _dm(None)
        asyncio.run(gh.start_onboarding(update, ctx))

        tap_update, query = _group_tap()
        asyncio.run(gh.on_join_tap(tap_update, ctx))
        asyncio.run(gh.on_already_tap(tap_update, ctx))

        cb_reply = AsyncMock()
        cb = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(reply_text=cb_reply))
        asyncio.run(gh.on_check_form(
            SimpleNamespace(effective_user=_user(), callback_query=cb), ctx
        ))

    assert called == [], "an existing member should never trigger an Airtable call"
    assert asyncio.run(gdb.get_user(555))["status"] == "member"
    # And nothing was posted publicly about them.
    ctx.bot.send_message.assert_not_awaited()


# ── False membership claims are corrected publicly ──────────────────────────────

def test_false_already_claim_is_corrected_in_the_group(live):
    ctx = _ctx(member_status=None)   # not actually in the alumni group
    update, query = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == MONITORED
    assert "tg://user?id=555" in kwargs["text"]
    assert "isn't in the alumni group" in kwargs["text"].lower()
    # Recorded as chased, in the right group, so the sweep doesn't double up.
    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "nudged"
    assert row["last_seen_chat_id"] == MONITORED
    query.answer.assert_awaited_once()


def test_true_already_claim_posts_nothing(live):
    ctx = _ctx(member_status="member")
    update, _ = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    ctx.bot.send_message.assert_not_awaited()


# ── The follow-up sweep ─────────────────────────────────────────────────────────

def _age_nudge(user_id, days, chat_id=MONITORED):
    """Backdate someone's nudge so the sweep considers them stale."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async def run():
        async with aiosqlite.connect(gdb.DB_PATH) as db:
            await db.execute(
                "UPDATE gate_users SET nudged_at = ?, last_seen_chat_id = ? "
                "WHERE user_id = ?",
                (when, chat_id, user_id),
            )
            await db.commit()
    asyncio.run(run())


def test_followup_chases_only_the_unengaged(live):
    # Tagged 6 days ago, never tapped anything -> chase.
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 6)
    # Tagged 6 days ago but has since started onboarding -> leave alone.
    asyncio.run(gdb.mark_nudged(222, "two", "Two", MONITORED))
    _age_nudge(222, 6)
    asyncio.run(gdb.mark_awaiting_form(222, "two", "Two"))
    # Tagged yesterday -> too recent.
    asyncio.run(gdb.mark_nudged(333, "three", "Three", MONITORED))
    _age_nudge(333, 1)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_awaited_once()
    text = ctx.bot.send_message.await_args.kwargs["text"]
    assert "tg://user?id=111" in text
    assert "tg://user?id=222" not in text, "someone mid-onboarding must be left alone"
    assert "tg://user?id=333" not in text, "recently tagged must be left alone"


def test_followup_advances_the_timestamp_so_it_does_not_repeat(live):
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 6)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))
    ctx.bot.send_message.reset_mock()
    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_followup_batches_mentions(live, monkeypatch):
    """One message per person would hit Telegram's ~20/min group flood limit."""
    monkeypatch.setattr(gh, "_FOLLOWUP_BATCH_PAUSE", 0)
    for uid in range(600, 620):        # 20 people
        asyncio.run(gdb.mark_nudged(uid, f"u{uid}", f"U{uid}", MONITORED))
        _age_nudge(uid, 6)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    # 20 people in batches of 8 -> 3 messages, not 20.
    assert ctx.bot.send_message.await_count == 3
    named = "".join(
        c.kwargs["text"] for c in ctx.bot.send_message.await_args_list
    )
    for uid in range(600, 620):
        assert f"tg://user?id={uid}" in named


def test_followup_ignores_other_groups(live):
    asyncio.run(gdb.mark_nudged(111, "one", "One", -100777))
    _age_nudge(111, 6, chat_id=-100777)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_followup_retries_after_a_send_failure(live):
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 6)
    ctx = _ctx(member_status=None)
    ctx.bot.send_message = AsyncMock(side_effect=Exception("flood wait"))

    asyncio.run(gh.followup_job(ctx))

    # nudged_at untouched, so the next sweep picks them up again.
    ctx.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    asyncio.run(gh.followup_job(ctx))
    ctx.bot.send_message.assert_awaited_once()


def test_followup_dormant_when_interval_disabled(live, monkeypatch):
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 60)
    monkeypatch.setattr(settings, "ANNOUNCE_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


# ── Watching groups is managed live, not via config ─────────────────────────────

def _group_cmd(chat_id=-100555, title="Cohort 2026", chat_type="supergroup", admin=True):
    reply = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type, title=title),
        effective_user=SimpleNamespace(id=ADMIN if admin else 999999),
        effective_message=SimpleNamespace(reply_text=reply),
        message=SimpleNamespace(reply_text=reply),
    ), reply


def test_watch_adds_a_group_without_a_restart(live):
    update, reply = _group_cmd()

    asyncio.run(gh.watch_command(update, _ctx()))

    assert -100555 in gh.monitored_ids()
    assert gh.is_monitored(-100555)
    assert "-100555" in reply.await_args.args[0]
    # And it survives a cache reload, i.e. it was persisted.
    asyncio.run(gh.refresh_monitored())
    assert gh.is_monitored(-100555)


def test_watched_group_is_immediately_active(live):
    """The whole point: a newly watched group works with no restart."""
    ctx = _ctx(member_status=None)
    update, _ = _group_cmd(chat_id=-100555)
    asyncio.run(gh.watch_command(update, ctx))
    ctx.bot.send_message.reset_mock()

    # A message in the new group now triggers detection.
    msg_update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100555, type="supergroup"),
        effective_user=_user(uid=777),
    )
    asyncio.run(gh.on_group_message(msg_update, ctx))

    assert asyncio.run(gdb.get_user(777))["status"] == "nudged"
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == -100555


def test_watch_twice_is_idempotent(live):
    update, reply = _group_cmd()
    asyncio.run(gh.watch_command(update, _ctx()))
    asyncio.run(gh.watch_command(update, _ctx()))

    assert "already watching" in reply.await_args.args[0].lower()
    assert len(asyncio.run(gdb.monitored_chats())) == 2  # the seed + this one


def test_watch_refuses_the_alumni_group_itself(live):
    update, reply = _group_cmd(chat_id=ALUMNI_GROUP, title="Alumni")

    asyncio.run(gh.watch_command(update, _ctx()))

    assert not gh.is_monitored(ALUMNI_GROUP)
    assert "destination" in reply.await_args.args[0].lower()


def test_watch_refuses_a_private_chat(live):
    update, reply = _group_cmd(chat_type="private", title=None)

    asyncio.run(gh.watch_command(update, _ctx()))

    assert "inside a group" in reply.await_args.args[0].lower()


def test_watch_ignores_non_admins(live):
    update, reply = _group_cmd(admin=False)

    asyncio.run(gh.watch_command(update, _ctx()))

    assert not gh.is_monitored(-100555)
    reply.assert_not_awaited()


def test_unwatch_stops_detection(live):
    ctx = _ctx(member_status=None)
    update, reply = _group_cmd(chat_id=MONITORED, title="Watched")

    asyncio.run(gh.unwatch_command(update, ctx))

    assert not gh.is_monitored(MONITORED)
    msg_update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=MONITORED, type="supergroup"),
        effective_user=_user(uid=888),
    )
    asyncio.run(gh.on_group_message(msg_update, ctx))
    assert asyncio.run(gdb.get_user(888)) is None


def test_unwatch_a_group_never_watched(live):
    update, reply = _group_cmd(chat_id=-100999123)

    asyncio.run(gh.unwatch_command(update, _ctx()))

    assert "wasn't watching" in reply.await_args.args[0].lower()


def test_env_seed_is_a_bootstrap_only(live):
    """The env var seeds the DB once; after that the DB is the source of truth."""
    asyncio.run(gh.load_monitored([-100555, -100666]))
    assert {-100555, -100666} <= gh.monitored_ids()

    # Re-seeding with a shorter list must not unwatch anything.
    asyncio.run(gh.load_monitored([-100555]))
    assert -100666 in gh.monitored_ids()


def test_env_seed_skips_the_alumni_group(live):
    """The destination in GATE_MONITORED_GROUP_IDS is ignored, not watched.

    /gate_watch and the auto-watch promotion both refuse the destination, but
    the env seed is the path with no human in the loop to see the refusal —
    pasting the alumni id into the variable is a plausible slip, and watching
    the destination would nudge people about the group they are already in.
    """
    asyncio.run(gh.load_monitored([MONITORED, ALUMNI_GROUP]))

    assert ALUMNI_GROUP not in gh.monitored_ids()
    assert MONITORED in gh.monitored_ids()   # the rest of the list still seeds


def test_groups_command_lists_what_is_watched(live):
    update, reply = _group_cmd(chat_id=-100555)
    asyncio.run(gh.watch_command(update, _ctx()))
    dm, dm_reply = _group_cmd(chat_type="private")

    asyncio.run(gh.groups_command(dm, _ctx()))

    text = dm_reply.await_args.args[0]
    assert "-100555" in text
    assert str(ALUMNI_GROUP) in text     # the destination is shown too


# ── Auto-watch: promotion to admin is the opt-in ────────────────────────────────

def _promotion(chat_id=-100555, title="New Cohort", new_status="administrator",
               old_status="member", chat_type="supergroup", bot_id=42):
    return SimpleNamespace(
        my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=chat_type, title=title),
            old_chat_member=SimpleNamespace(status=old_status),
            new_chat_member=SimpleNamespace(
                status=new_status, user=SimpleNamespace(id=bot_id)
            ),
        )
    )


def _bot_ctx(**kw):
    ctx = _ctx(**kw)
    ctx.bot.id = 42
    return ctx


def test_promotion_to_admin_starts_watching(live):
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(_promotion(), ctx))

    assert gh.is_monitored(-100555)
    # Admins are told, so an unintended one can be undone.
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == ADMIN


def test_watched_group_from_promotion_is_immediately_live(live):
    ctx = _bot_ctx()
    asyncio.run(gh.on_my_chat_member(_promotion(), ctx))
    ctx.bot.send_message.reset_mock()

    asyncio.run(gh.on_group_message(SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100555, type="supergroup"),
        effective_user=_user(uid=901),
    ), ctx))

    assert asyncio.run(gdb.get_user(901))["status"] == "nudged"


def test_merely_being_added_does_not_start_watching(live):
    """Admin rights are the signal — a plain member add isn't enough."""
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(
        _promotion(old_status="left", new_status="member"), ctx))

    assert not gh.is_monitored(-100555)


def test_promotion_in_the_alumni_group_is_ignored(live):
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(_promotion(chat_id=ALUMNI_GROUP), ctx))

    assert not gh.is_monitored(ALUMNI_GROUP)


def test_removal_stops_watching(live):
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(
        _promotion(chat_id=MONITORED, old_status="administrator",
                   new_status="kicked"), ctx))

    assert not gh.is_monitored(MONITORED)


def test_auto_watch_can_be_disabled(live, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_WATCH", False)
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(_promotion(), ctx))

    assert not gh.is_monitored(-100555)


def test_other_bots_promotions_are_ignored(live):
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(_promotion(bot_id=999), ctx))

    assert not gh.is_monitored(-100555)


def test_promotion_is_idempotent(live):
    ctx = _bot_ctx()
    asyncio.run(gh.on_my_chat_member(_promotion(chat_id=MONITORED), ctx))

    # Already watched (seeded), so no duplicate notification.
    ctx.bot.send_message.assert_not_awaited()
