# tests/test_gate.py
"""Alumni Gate: detection, the pinned self-check announcement, and onboarding."""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError

import gate
from gate import db as gdb
from gate import handlers as gh
from gate import messages as gmsg
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
        # Pinned off for the same reason ADMIN_IDS is patched: a developer with a
        # real GATE_CHANNEL_ID in .env would otherwise see every admission grow a
        # second button, and the no-channel assertions would pass or fail
        # depending on whose machine ran them. The channel tests opt in with the
        # with_channel fixture.
        CHANNEL_ID=0,
        MONITORED_GROUP_IDS=[MONITORED],
        # Re-posts on, so the tests that predate the announce-once default still
        # exercise the cadence. Production defaults to 0 — see the announce-once
        # tests below.
        ANNOUNCE_INTERVAL_DAYS=5,
        FOLLOWUP_INTERVAL_DAYS=5,
        INTRO_MIN_WORDS=50,
        REQUIRE_WATCHED_GROUP=True,
        WELCOME_TAG=True,
    ):
        asyncio.run(gdb.init_schema())
        # The watched-group list lives in the DB and is cached in memory, so it
        # has to be primed the same way startup does it.
        asyncio.run(gh.load_monitored([MONITORED]))
        try:
            yield path
        finally:
            gh._monitored = set()
            gh._pending_welcome.clear()


def _user(uid=555, username="alice", first="Alice", is_bot=False):
    return SimpleNamespace(id=uid, username=username, first_name=first, is_bot=is_bot)


class _JobQueue:
    """Enough of PTB's JobQueue to test coalescing: records run_once, runs on demand.

    ``get_jobs_by_name`` is what stops a second newcomer scheduling a second flush,
    so it has to answer from the jobs still pending rather than every job ever
    scheduled — otherwise a flush would never be scheduled again after the first.
    """

    def __init__(self):
        self.pending = []

    def run_once(self, callback, when, chat_id=None, name=None):
        self.pending.append(SimpleNamespace(
            callback=callback, when=when, chat_id=chat_id, name=name
        ))

    def get_jobs_by_name(self, name):
        return [job for job in self.pending if job.name == name]

    def fire(self, ctx):
        """Run every pending job, as the real queue would when its timer expires."""
        due, self.pending = self.pending, []
        for job in due:
            asyncio.run(job.callback(
                SimpleNamespace(bot=ctx.bot, job=job, job_queue=self)
            ))


def _ctx(member_status=None, invite="https://t.me/+newlink", watched_status="member",
         args=None, job_queue=None, channel_invite="https://t.me/+channellink"):
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

    def _invite(chat_id, name=None, member_limit=None):
        # Keyed on chat so a test can tell the group link from the channel one, and
        # so a channel that refuses (bot not an admin there) can be simulated
        # without also breaking the group link.
        if chat_id == settings.CHANNEL_ID and settings.CHANNEL_ID:
            if channel_invite is None:
                raise BadRequest("not enough rights")
            return SimpleNamespace(invite_link=channel_invite)
        return SimpleNamespace(invite_link=invite)

    bot_obj.create_chat_invite_link = AsyncMock(side_effect=_invite)
    bot_obj.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1000))
    bot_obj.pin_chat_message = AsyncMock()
    bot_obj.delete_message = AsyncMock()
    return SimpleNamespace(bot=bot_obj, args=args, job_queue=job_queue)


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

    # The nudge itself is a DM to the person, not a post in the group they were
    # seen in. A public tag is the join path's decision to add on top (see the
    # welcome tests), never something the check does on its own — which is what
    # keeps a first-time poster from being called out mid-conversation.
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 555
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"

    # Second sighting: already classified -> no second nudge.
    ctx.bot.send_message.reset_mock()
    asyncio.run(gh._process_user(ctx, MONITORED, user))
    ctx.bot.send_message.assert_not_awaited()


def test_an_undeliverable_nudge_still_records_them(live):
    """Telegram won't let a bot open a chat with someone who never messaged it.

    That is the common case in a community group, so the record must be written
    regardless — it is what puts them in the follow-up roundup, which is the only
    way the gate can reach them at all.
    """
    ctx = _ctx(member_status=None)
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("bot can't initiate"))

    asyncio.run(gh._process_user(ctx, MONITORED, _user()))

    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "nudged"
    assert row["last_seen_chat_id"] == MONITORED


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


def _age_announcement(chat_id, days):
    """Backdate a group's announcement so the re-post cadence considers it due."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async def run():
        async with aiosqlite.connect(gdb.DB_PATH) as db:
            await db.execute(
                "UPDATE gate_announcements SET posted_at = ? WHERE chat_id = ?",
                (when, chat_id),
            )
            await db.commit()

    asyncio.run(run())


def test_announce_job_reposts_when_the_interval_has_passed(live):
    asyncio.run(gdb.set_announcement(MONITORED, 42))
    _age_announcement(MONITORED, 6)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.announce_job(ctx))

    ctx.bot.send_message.assert_awaited_once()


def test_announce_once_never_reposts(live, monkeypatch):
    """Interval 0 — the production default — means the first post was the only one.

    However old the announcement gets. Re-pinning the same notice at a group that
    has mostly already joined is what the follow-up roundup replaces.
    """
    asyncio.run(gdb.set_announcement(MONITORED, 42))
    _age_announcement(MONITORED, 400)
    monkeypatch.setattr(settings, "ANNOUNCE_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.announce_job(ctx))

    ctx.bot.send_message.assert_not_awaited()
    # And the one on record is untouched, so the welcome tag still fires.
    assert asyncio.run(gdb.get_announcement(MONITORED))["message_id"] == 42


def test_announce_once_still_makes_the_first_post(live, monkeypatch):
    """/gate_watch approves without announcing, so a group can be approved and
    silent. The job has to cover that even with re-posts off."""
    monkeypatch.setattr(settings, "ANNOUNCE_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.announce_job(ctx))

    ctx.bot.send_message.assert_awaited_once()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == MONITORED


def test_gate_announce_reposts_even_with_the_interval_off(live, monkeypatch):
    """The manual command is the escape hatch: it never consults the cadence."""
    asyncio.run(gdb.set_announcement(MONITORED, 42))
    monkeypatch.setattr(settings, "ANNOUNCE_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    assert asyncio.run(gh.post_announcement(ctx, MONITORED)) is True

    ctx.bot.delete_message.assert_awaited_once_with(MONITORED, 42)


def test_announcement_offers_both_buttons(live):
    ctx = _ctx(member_status=None)

    asyncio.run(gh.post_announcement(ctx, MONITORED))

    markup = ctx.bot.send_message.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == [gh.JOIN_CB, gh.ALREADY_CB]


# ── /gate_announce: who may trigger it, and where ───────────────────────────────
#
# The group leader case is what makes setting a group up self-service: they add
# the bot, promote it, and announce, without anyone editing an env var for them.

def _announced_chats(ctx):
    """Chats the announcement itself went to, ignoring confirmation DMs."""
    return [
        call.kwargs["chat_id"]
        for call in ctx.bot.send_message.await_args_list
        if call.kwargs.get("text") == gmsg.GROUP_ANNOUNCE
    ]


def test_announcing_in_a_group_approves_it(live):
    """The command IS the approval — that's what keeps the two from drifting."""
    ctx = _ctx(member_status=None)
    asyncio.run(gh.on_my_chat_member(_promotion(chat_id=-100555), _bot_ctx()))
    assert not gh.is_approved(-100555)

    update, _ = _group_cmd(chat_id=-100555)
    asyncio.run(gh.announce_command(update, ctx))

    assert gh.is_approved(-100555)
    assert _announced_chats(ctx) == [-100555]


def test_a_group_leader_cannot_announce_or_approve(live):
    """A group's own admin isn't a bot admin. Letting them run this would let
    anyone approve their own group, which is the hole it exists to close."""
    ctx = _ctx(member_status=None, watched_status="creator")
    update, reply = _group_cmd(chat_id=MONITORED, admin=False)

    asyncio.run(gh.announce_command(update, ctx))

    assert _announced_chats(ctx) == []
    # Silent, not refused: a refusal advertises that the command exists.
    reply.assert_not_awaited()


def test_announce_refuses_the_alumni_group_itself(live):
    """Approving the destination would make being in it the qualification for
    getting into it."""
    ctx = _ctx(member_status=None)
    update, _ = _group_cmd(chat_id=ALUMNI_GROUP, title="Alumni")

    asyncio.run(gh.announce_command(update, ctx))

    assert not gh.is_approved(ALUMNI_GROUP)
    assert _announced_chats(ctx) == []


def test_a_bot_admin_in_a_dm_announces_every_approved_group(live):
    asyncio.run(gdb.approve_monitored_chat(-100777, "Another cohort"))
    asyncio.run(gh.refresh_monitored())
    ctx = _ctx(member_status=None)
    update, _ = _group_cmd(chat_id=ADMIN, chat_type="private")

    asyncio.run(gh.announce_command(update, ctx))

    assert sorted(_announced_chats(ctx)) == sorted([MONITORED, -100777])


def test_a_dm_announce_skips_unapproved_groups(live):
    """A DM carries no group to vouch for, so it can't approve one — and must not
    announce into a group nobody has vouched for either."""
    asyncio.run(gdb.add_monitored_chat(-100777, "Someone's random group"))
    asyncio.run(gh.refresh_monitored())
    ctx = _ctx(member_status=None)
    update, _ = _group_cmd(chat_id=ADMIN, chat_type="private")

    asyncio.run(gh.announce_command(update, ctx))

    assert _announced_chats(ctx) == [MONITORED]
    assert not gh.is_approved(-100777)


def test_a_bot_admin_in_a_group_announces_only_there(live):
    asyncio.run(gdb.approve_monitored_chat(-100777, "Another cohort"))
    asyncio.run(gh.refresh_monitored())
    ctx = _ctx(member_status=None)
    update, _ = _group_cmd(chat_id=MONITORED)

    asyncio.run(gh.announce_command(update, ctx))

    assert _announced_chats(ctx) == [MONITORED]


def test_announce_while_dormant_says_so(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path), patch(
        "gate.handlers.ADMIN_IDS", [ADMIN]
    ), patch.multiple(settings, LIVE=False, GROUP_ID=ALUMNI_GROUP):
        asyncio.run(gdb.init_schema())
        ctx = _ctx(member_status=None)
        update, reply = _group_cmd(chat_id=ADMIN, chat_type="private")

        asyncio.run(gh.announce_command(update, ctx))

        assert "dormant" in reply.await_args.args[0].lower()
        assert _announced_chats(ctx) == []


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


# ── The announcements channel, handed over alongside the group ──────────────────
# Optional: with GATE_CHANNEL_ID unset nothing about a channel is said, which is
# what every test above this line asserts by not mentioning one.

CHANNEL = -1001572190121


@pytest.fixture()
def with_channel():
    """GATE_CHANNEL_ID configured, as a deployment that has a channel would be."""
    with patch.object(settings, "CHANNEL_ID", CHANNEL):
        yield CHANNEL


def _admit(uid=555, ctx=None):
    """Take someone through the final intro step and return (ctx, reply)."""
    asyncio.run(gdb.mark_awaiting_intro(uid, "alice", "Alice", "Ada Lovelace"))
    ctx = ctx or _ctx(member_status=None, invite="https://t.me/+minted")
    update, reply = _dm(_intro(), user=_user(uid=uid))
    asyncio.run(gh.on_private_text(update, ctx))
    return ctx, reply


def _urls(markup):
    return [b.url for row in markup.inline_keyboard for b in row]


def test_admission_hands_over_the_channel_too(live, with_channel):
    ctx, reply = _admit()

    assert _urls(reply.await_args.kwargs["reply_markup"]) == [
        "https://t.me/+minted", "https://t.me/+channellink",
    ]
    row = asyncio.run(gdb.get_user(555))
    assert row["invite_link"] == "https://t.me/+minted"
    assert row["channel_invite_link"] == "https://t.me/+channellink"


def test_the_channel_link_is_single_use_and_per_person(live, with_channel):
    """A reusable channel link would be forwardable to anyone who never onboarded."""
    ctx, _ = _admit()

    calls = {
        call.kwargs["chat_id"]: call.kwargs
        for call in ctx.bot.create_chat_invite_link.await_args_list
    }
    assert calls[CHANNEL]["member_limit"] == 1
    assert "555" in calls[CHANNEL]["name"]


def test_the_group_text_still_says_where_the_intro_goes(live, with_channel):
    """Two destinations, so the intro instruction has to name the group.

    A channel is broadcast-only — someone who reads "post your intro" and opens
    the channel cannot post at all.
    """
    _, reply = _admit()

    text = (reply.await_args.args[0] if reply.await_args.args else
            reply.await_args.kwargs.get("text", "")).lower()
    group_part, _, channel_part = text.partition("📣")
    assert "group" in group_part and "intro" in group_part
    assert "intro" not in channel_part


def test_a_channel_that_refuses_does_not_cost_them_their_place(live, with_channel):
    """The group is what admission means; the channel is the extra."""
    ctx = _ctx(member_status=None, invite="https://t.me/+minted", channel_invite=None)

    _, reply = _admit(ctx=ctx)

    assert _urls(reply.await_args.kwargs["reply_markup"]) == ["https://t.me/+minted"]
    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "registered"
    assert row["channel_invite_link"] is None


def test_a_failed_group_link_still_fails_the_admission(live, with_channel):
    """The channel must not paper over the one link that actually matters."""
    ctx = _ctx(member_status=None, invite=None)
    ctx.bot.create_chat_invite_link = AsyncMock(side_effect=BadRequest("no rights"))

    _, reply = _admit(ctx=ctx)

    assert "went wrong" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_intro"


def test_returning_for_the_link_again_gets_both(live, with_channel):
    _admit()
    ctx = _ctx(member_status=None)
    update, reply = _dm("anything")

    asyncio.run(gh.on_private_text(update, ctx))

    assert _urls(reply.await_args.kwargs["reply_markup"]) == [
        "https://t.me/+minted", "https://t.me/+channellink",
    ]
    # Re-handed, not re-minted: the stored links are reused as they were.
    ctx.bot.create_chat_invite_link.assert_not_awaited()


def test_someone_registered_before_the_channel_existed_is_backfilled(live):
    """Alumni already through the gate must not be left out of the channel forever."""
    _admit()  # no channel configured yet
    assert asyncio.run(gdb.get_user(555))["channel_invite_link"] is None

    with patch.object(settings, "CHANNEL_ID", CHANNEL):
        ctx = _ctx(member_status=None)
        update, reply = _dm("anything")
        asyncio.run(gh.on_private_text(update, ctx))

    assert _urls(reply.await_args.kwargs["reply_markup"]) == [
        "https://t.me/+minted", "https://t.me/+channellink",
    ]
    # Minted once and kept, so coming back a third time doesn't mint again.
    assert asyncio.run(gdb.get_user(555))["channel_invite_link"] == \
        "https://t.me/+channellink"


def test_the_channel_column_is_added_to_an_existing_database(tmp_path):
    """The live DB predates the column, so boot has to migrate it in place.

    Asserted against the pre-channel schema rather than a fresh one, because a
    fresh DB gets the column from the same ALTER and so proves nothing about the
    rows already in production.
    """
    import aiosqlite

    path = str(tmp_path / "old.db")

    async def build_old_db():
        async with aiosqlite.connect(path) as db:
            await db.execute("""
                CREATE TABLE gate_users (
                    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                    full_name TEXT, status TEXT NOT NULL, nudged_at TEXT,
                    registered_at TEXT, invite_link TEXT, updated_at TEXT NOT NULL
                )
            """)
            await db.execute(
                "INSERT INTO gate_users (user_id, status, invite_link, updated_at) "
                "VALUES (7, 'registered', 'https://t.me/+old', 'x')"
            )
            await db.commit()

    asyncio.run(build_old_db())

    with patch("config.DB_PATH", path), patch("gate.db.DB_PATH", path):
        asyncio.run(gdb.init_schema())
        row = asyncio.run(gdb.get_user(7))
        assert row["invite_link"] == "https://t.me/+old"  # nothing lost
        assert row["channel_invite_link"] is None

        asyncio.run(gdb.set_channel_invite_link(7, "https://t.me/+chan"))
        # A second boot must neither error on the existing column nor undo that.
        asyncio.run(gdb.init_schema())
        assert asyncio.run(gdb.get_user(7))["channel_invite_link"] == \
            "https://t.me/+chan"


def test_a_backfill_never_overwrites_a_link_already_issued(live):
    """Two links to the same channel for one person would be one link too many."""
    _admit()
    asyncio.run(gdb.set_channel_invite_link(555, "https://t.me/+first"))
    asyncio.run(gdb.set_channel_invite_link(555, "https://t.me/+second"))

    assert asyncio.run(gdb.get_user(555))["channel_invite_link"] == \
        "https://t.me/+first"


def test_without_a_channel_nothing_changes(live):
    """The whole feature is inert until GATE_CHANNEL_ID is set."""
    ctx, reply = _admit()

    assert _urls(reply.await_args.kwargs["reply_markup"]) == ["https://t.me/+minted"]
    assert ctx.bot.create_chat_invite_link.await_count == 1
    assert asyncio.run(gdb.get_user(555))["channel_invite_link"] is None


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


# ── False membership claims are corrected privately ─────────────────────────────

def test_false_already_claim_is_corrected_privately(live):
    ctx = _ctx(member_status=None)   # not actually in the alumni group
    update, query = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    # Corrected in a DM, not in front of the group.
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 555
    assert "isn't in the alumni group" in kwargs["text"].lower()
    # Recorded as chased, in the right group, so the sweep doesn't double up.
    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "nudged"
    assert row["last_seen_chat_id"] == MONITORED
    query.answer.assert_awaited_once()


def test_false_claim_is_recorded_even_if_the_dm_bounces(live):
    """The popup and the DM are both best-effort; the record is not. Without it
    the claim would be a way to quietly opt out of ever being chased again."""
    ctx = _ctx(member_status=None)
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("bot can't initiate"))
    update, query = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"
    assert "can't find" in query.answer.await_args.args[0].lower()


def test_true_already_claim_says_nothing_at_all(live):
    ctx = _ctx(member_status="member")
    update, _ = _group_tap()

    asyncio.run(gh.on_already_tap(update, ctx))

    ctx.bot.send_message.assert_not_awaited()


# ── The intro reminder ──────────────────────────────────────────────────────────
#
# The last step of onboarding — re-posting the intro in the alumni group — is the
# one with nothing enforcing it: by then they already hold the invite link.

def _joined(uid=777, hours_ago=0):
    """Someone who walked into the alumni group `hours_ago` hours back."""
    asyncio.run(gdb.mark_joined_group(uid, f"u{uid}", f"U{uid}"))
    if hours_ago:
        import aiosqlite
        from datetime import datetime, timedelta, timezone

        async def _backdate():
            when = (
                datetime.now(timezone.utc) - timedelta(hours=hours_ago)
            ).isoformat()
            async with aiosqlite.connect(gdb.DB_PATH) as db:
                await db.execute(
                    "UPDATE gate_users SET joined_group_at = ? WHERE user_id = ?",
                    (when, uid),
                )
                await db.commit()

        asyncio.run(_backdate())


def _answer_intro(uid=777, yes=True):
    """Tap one of the two buttons on the question."""
    reply = AsyncMock()
    query = SimpleNamespace(
        data=gh.INTRO_YES_CB if yes else gh.INTRO_NO_CB,
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=reply),
    )
    update = SimpleNamespace(callback_query=query, effective_user=_user(uid=uid))
    asyncio.run(gh.on_intro_answer(update, _ctx()))
    return query, reply


def test_joining_the_alumni_group_starts_the_clock(live):
    ctx = _ctx()
    asyncio.run(gh.on_chat_member(SimpleNamespace(chat_member=SimpleNamespace(
        chat=SimpleNamespace(id=ALUMNI_GROUP),
        old_chat_member=SimpleNamespace(status="left"),
        new_chat_member=SimpleNamespace(status="member", user=_user(uid=777)),
    )), ctx))

    row = asyncio.run(gdb.get_user(777))
    assert row["status"] == "member"
    assert row["joined_group_at"] is not None


def test_someone_silent_for_three_hours_is_asked(live):
    _joined(777, hours_ago=4)
    ctx = _ctx()

    asyncio.run(gh.intro_check_job(ctx))

    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 777
    assert "intro" in kwargs["text"].lower()
    data = [b.callback_data
            for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert data == [gh.INTRO_YES_CB, gh.INTRO_NO_CB]
    assert asyncio.run(gdb.get_user(777))["intro_reminded_at"] is not None


def test_answering_yes_closes_it_out(live):
    _joined(777, hours_ago=4)
    asyncio.run(gh.intro_check_job(_ctx()))

    query, reply = _answer_intro(777, yes=True)

    assert asyncio.run(gdb.get_user(777))["intro_posted_at"] is not None
    assert "thanks" in reply.await_args.args[0].lower()
    # Buttons removed, so the question can't be answered twice.
    query.edit_message_reply_markup.assert_awaited_once()


def test_answering_not_yet_nudges_without_recording_it(live):
    _joined(777, hours_ago=4)
    asyncio.run(gh.intro_check_job(_ctx()))

    _, reply = _answer_intro(777, yes=False)

    assert asyncio.run(gdb.get_user(777))["intro_posted_at"] is None
    assert "make sure to post it" in reply.await_args.args[0].lower()


def test_someone_who_only_just_joined_is_left_alone(live):
    _joined(777, hours_ago=1)
    ctx = _ctx()

    asyncio.run(gh.intro_check_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_the_question_is_asked_once_and_only_once(live):
    """Including after 'not yet' — being asked twice about the same thing is
    nagging, and they're already in the group either way."""
    _joined(777, hours_ago=4)
    ctx = _ctx()

    asyncio.run(gh.intro_check_job(ctx))
    _answer_intro(777, yes=False)
    asyncio.run(gh.intro_check_job(ctx))

    assert ctx.bot.send_message.await_count == 1


def test_an_undeliverable_question_is_not_retried_forever(live):
    """Someone added to the group by hand may never have started the bot. That
    won't change, so a failed send must still close the row out."""
    _joined(777, hours_ago=4)
    ctx = _ctx()
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("bot can't initiate"))

    asyncio.run(gh.intro_check_job(ctx))

    assert asyncio.run(gdb.get_user(777))["intro_reminded_at"] is not None


def test_people_who_predate_the_gate_are_never_asked(live):
    """No join event was ever seen for them, so there is no clock to run — they
    must not be asked about an intro nobody ever requested of them."""
    asyncio.run(gdb.mark_member(777, "old", "Old"))
    ctx = _ctx()

    asyncio.run(gh.intro_check_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_the_alumni_group_is_never_treated_as_watched(live):
    """The destination is not a group the gate polices, and nothing about the
    intro check may change that."""
    asyncio.run(gh.on_group_message(SimpleNamespace(
        effective_chat=SimpleNamespace(id=ALUMNI_GROUP, type="supergroup"),
        effective_user=_user(uid=901),
    ), _ctx()))

    assert asyncio.run(gdb.get_user(901)) is None
    assert not gh.is_approved(ALUMNI_GROUP)


def test_the_check_is_off_when_the_delay_is_zero(live):
    _joined(777, hours_ago=4)
    ctx = _ctx()

    with patch.object(settings, "INTRO_REMINDER_HOURS", 0):
        asyncio.run(gh.intro_check_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_rejoining_does_not_hand_out_a_fresh_grace_period(live):
    _joined(777, hours_ago=4)
    before = asyncio.run(gdb.get_user(777))["joined_group_at"]

    asyncio.run(gdb.mark_joined_group(777, "u777", "U777"))

    assert asyncio.run(gdb.get_user(777))["joined_group_at"] == before


# ── Invite tokens: admitting someone who's in none of the approved groups ───────

def _expire_all_tokens():
    """Backdate every token's expiry so the TTL branch can be tested."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    async def _go():
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        async with aiosqlite.connect(gdb.DB_PATH) as db:
            await db.execute(
                "UPDATE gate_invite_tokens SET expires_at = ?", (past,)
            )
            await db.commit()

    asyncio.run(_go())


def _issue(note="Guest speaker"):
    """Mint a token the way /gate_token does, returning it and the admin's reply."""
    update, reply = _dm(None, user=_user(uid=ADMIN, username="root", first="Root"))
    asyncio.run(gh.token_command(update, _ctx(args=note.split())))
    shown = reply.await_args.args[0]
    token = re.search(r"FA-[A-Z2-9]{5}-[A-Z2-9]{5}", shown).group(0)
    return token, shown


def _redeem(token, uid=777, ctx=None):
    ctx = ctx or _ctx(member_status=None, watched_status=None)
    update, reply = _dm(token, user=_user(uid=uid))
    asyncio.run(gh.on_private_text(update, ctx))
    return reply


def test_a_token_admits_someone_in_no_approved_group(live):
    token, _ = _issue()

    # watched_status=None: in none of the approved groups, so the normal door
    # is shut to them.
    reply = _redeem(token)

    assert asyncio.run(gdb.get_user(777))["status"] == "awaiting_form"
    assert "code accepted" in reply.await_args_list[0].args[0].lower()


def test_the_exemption_outlives_the_token(live):
    """Eligibility is re-asked every time someone re-enters onboarding, so a
    one-shot bypass would strand anyone who wandered off and came back."""
    token, _ = _issue()
    ctx = _ctx(member_status=None, watched_status=None)
    _redeem(token, ctx=ctx)

    # Later, with no code in hand.
    again, reply = _dm(None, user=_user(uid=777))
    asyncio.run(gh.start_onboarding(again, ctx))

    assert "can't find you" not in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(777))["status"] == "awaiting_form"


def test_a_token_works_once(live):
    token, _ = _issue()
    _redeem(token, uid=777)

    reply = _redeem(token, uid=888)

    assert "already been used" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(888)) is None


def test_a_revoked_token_is_dead(live):
    token, shown = _issue()
    token_id = int(re.search(r"#(\d+)", shown).group(1))
    admin, admin_reply = _dm(None, user=_user(uid=ADMIN))

    asyncio.run(gh.revoke_command(admin, _ctx(args=[str(token_id)])))

    assert "cancelled" in admin_reply.await_args.args[0].lower()
    reply = _redeem(token)
    assert "already been used" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(777)) is None


def test_a_redeemed_token_cannot_be_revoked_after_the_fact(live):
    """Revoke reports honestly rather than pretending it undid something."""
    token, shown = _issue()
    token_id = int(re.search(r"#(\d+)", shown).group(1))
    _redeem(token)
    admin, admin_reply = _dm(None, user=_user(uid=ADMIN))

    asyncio.run(gh.revoke_command(admin, _ctx(args=[str(token_id)])))

    assert "nothing to cancel" in admin_reply.await_args.args[0].lower()


def test_an_expired_token_is_refused(live):
    token, _ = _issue()
    _expire_all_tokens()

    reply = _redeem(token)

    assert "expired" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(777)) is None


def test_a_made_up_code_admits_nobody(live):
    """Well-formed but unknown — told it isn't recognised, not that it's a typo."""
    reply = _redeem("FA-ZZZZZ-ZZZZZ")

    assert "don't recognise" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(777)) is None


@pytest.mark.parametrize("attempt", ["FA-ABC-DEF", "FA-ABCDE", "fa-abcde-fghj"])
def test_a_mistyped_code_gets_an_answer_not_silence(live, attempt):
    """Silence on a near-miss reads as a broken bot, and a dropped character is
    the likeliest thing to go wrong between a screenshot and a keyboard."""
    reply = _redeem(attempt)

    answer = reply.await_args.args[0].lower()
    assert "nearly" in answer and "not quite" in answer
    assert asyncio.run(gdb.get_user(777)) is None


def test_ordinary_chatter_is_still_ignored(live):
    """Only FA- prefixed messages get the code treatment; everything else falls
    through to the flow it belongs to."""
    reply = _redeem("hey, is anyone there?")

    reply.assert_not_awaited()


def test_a_code_is_accepted_however_they_paste_it(live):
    """Lowercased, with stray spaces — both are what people actually send."""
    token, _ = _issue()

    _redeem(f"  {token.lower()} ")

    assert asyncio.run(gdb.get_user(777))["status"] == "awaiting_form"


def test_an_intro_is_never_mistaken_for_a_code(live):
    """The code branch runs before the intro branch, so it matches the whole
    message only — an intro opening with a code-shaped word is still an intro."""
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", "Alice A"))
    ctx = _ctx(member_status=None)
    update, _ = _dm("FA-ABCDE-FGHJK " + _intro(60), user=_user())

    asyncio.run(gh.on_private_text(update, ctx))

    assert asyncio.run(gdb.get_user(555))["status"] == "registered"


def test_tokens_are_not_stored_in_the_clear(live):
    """The DB is backed up on a schedule; a redeemable code in it is a key."""
    token, _ = _issue()

    rows = asyncio.run(gdb.invite_tokens())

    assert token not in str(rows)
    assert rows[0]["token_hash"] != token


def test_only_admins_can_mint_codes(live):
    update, reply = _dm(None, user=_user(uid=999999))

    asyncio.run(gh.token_command(update, _ctx(args=["Sneaky"])))

    reply.assert_not_awaited()
    assert asyncio.run(gdb.invite_tokens()) == []


def test_tokens_are_not_stored_in_the_clear(live):
    """The DB is backed up on a schedule; a redeemable code in it is a key."""
    token, _ = _issue()

    rows = asyncio.run(gdb.invite_tokens())

    assert token not in str(rows)
    assert rows[0]["token_hash"] != token


def test_only_admins_can_mint_codes(live):
    update, reply = _dm(None, user=_user(uid=999999))

    asyncio.run(gh.token_command(update, _ctx()))

    reply.assert_not_awaited()
    assert asyncio.run(gdb.invite_tokens()) == []


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
    monkeypatch.setattr(settings, "FOLLOWUP_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


def test_followup_survives_announce_once(live, monkeypatch):
    """The two cadences share nothing.

    They used to read one setting, so turning off the announcement re-post also
    silently killed the roundup — which is the half you actually want kept when
    the announcement is a one-off.
    """
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 6)
    monkeypatch.setattr(settings, "ANNOUNCE_INTERVAL_DAYS", 0)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_awaited_once()
    assert "tg://user?id=111" in ctx.bot.send_message.await_args.kwargs["text"]


def test_followup_uses_its_own_cutoff(live, monkeypatch):
    """Staleness is measured in follow-up days, not announcement days."""
    asyncio.run(gdb.mark_nudged(111, "one", "One", MONITORED))
    _age_nudge(111, 6)
    monkeypatch.setattr(settings, "FOLLOWUP_INTERVAL_DAYS", 30)
    ctx = _ctx(member_status=None)

    asyncio.run(gh.followup_job(ctx))

    ctx.bot.send_message.assert_not_awaited()


# ── Welcoming newcomers: the one channel that always lands ──────────────────────
# A brand-new member has almost never messaged the bot, so the DM nudge silently
# fails for them and the pinned announcement only works if they scroll. A public
# tag on arrival is what closes that gap.

def _announced(chat_id=MONITORED):
    """Put an announcement on record for a group, as posting one would."""
    asyncio.run(gdb.set_announcement(chat_id, 500))


def _joins(uid=555, chat_id=MONITORED, first="Alice"):
    """A chat_member update for someone walking into a group."""
    return SimpleNamespace(chat_member=SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        old_chat_member=SimpleNamespace(status="left"),
        new_chat_member=SimpleNamespace(
            status="member", user=_user(uid=uid, first=first)
        ),
    ))


def _group_posts(ctx, chat_id=MONITORED):
    """Every message the bot sent to the group (not the DMs to individuals)."""
    return [
        call.kwargs for call in ctx.bot.send_message.await_args_list
        if call.kwargs.get("chat_id") == chat_id
    ]


def test_a_newcomer_missing_from_alumni_is_tagged_in_the_group(live):
    _announced()
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)

    asyncio.run(gh.on_chat_member(_joins(), ctx))
    # Nothing public yet — the tag is coalesced, so it lands on the flush.
    assert _group_posts(ctx) == []
    queue.fire(ctx)

    posts = _group_posts(ctx)
    assert len(posts) == 1
    assert "Welcome" in posts[0]["text"]
    assert 'tg://user?id=555' in posts[0]["text"]
    assert posts[0]["reply_markup"].inline_keyboard[0][0].url.endswith(
        settings.START_PAYLOAD
    )


def test_a_newcomer_is_left_alone_until_the_group_is_announced_in(live):
    """No announcement yet means no context for a tag pointing at the bot."""
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)

    asyncio.run(gh.on_chat_member(_joins(), ctx))
    queue.fire(ctx)

    assert _group_posts(ctx) == []
    # Still recorded, so the five-day sweep reaches them once it is announced.
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"


def test_a_newcomer_already_in_alumni_is_not_tagged(live):
    _announced()
    queue = _JobQueue()
    ctx = _ctx(member_status="member", job_queue=queue)

    asyncio.run(gh.on_chat_member(_joins(), ctx))
    queue.fire(ctx)

    assert _group_posts(ctx) == []
    assert asyncio.run(gdb.get_user(555))["status"] == "member"


def test_newcomers_arriving_together_share_one_message(live):
    """A bulk add must not fire one message per person into the flood limit."""
    _announced()
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)

    for uid in (101, 102, 103):
        asyncio.run(gh.on_chat_member(_joins(uid=uid, first=f"U{uid}"), ctx))

    # One flush scheduled for the group, however many people arrived.
    assert len(queue.pending) == 1
    queue.fire(ctx)

    posts = _group_posts(ctx)
    assert len(posts) == 1
    for uid in (101, 102, 103):
        assert f"tg://user?id={uid}" in posts[0]["text"]


def test_a_newcomer_who_taps_before_the_flush_is_not_tagged(live):
    """The delay is long enough for a keen newcomer to already be onboarding.

    Tagging them then would be wrong twice: a public call-out for something they
    have already done, and a re-stamp to 'nudged' that discards their progress.
    """
    _announced()
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)
    asyncio.run(gh.on_chat_member(_joins(), ctx))

    asyncio.run(gdb.mark_awaiting_form(555, "alice", "Alice"))
    queue.fire(ctx)

    assert _group_posts(ctx) == []
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_form"


def test_tagging_a_newcomer_restarts_their_five_day_clock(live):
    """The tag is a chase, so the next sweep counts from it rather than the join."""
    _announced()
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)
    asyncio.run(gh.on_chat_member(_joins(), ctx))
    _age_nudge(555, 60)  # as if they had been sitting unengaged for days

    queue.fire(ctx)

    assert len(_group_posts(ctx)) == 1
    # Freshly stamped, so the sweep leaves them alone for another cycle.
    ctx.bot.send_message.reset_mock()
    asyncio.run(gh.followup_job(ctx))
    assert _group_posts(ctx) == []


def test_a_first_time_poster_is_not_tagged(live):
    """Only joins get a public tag; someone already talking is left to the sweep."""
    _announced()
    ctx = _ctx(member_status=None, job_queue=_JobQueue())

    asyncio.run(gh.on_group_message(SimpleNamespace(
        effective_chat=SimpleNamespace(id=MONITORED, type="supergroup"),
        effective_user=_user(),
    ), ctx))

    assert _group_posts(ctx) == []
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"


def test_welcome_tag_can_be_switched_off(live, monkeypatch):
    _announced()
    monkeypatch.setattr(settings, "WELCOME_TAG", False)
    queue = _JobQueue()
    ctx = _ctx(member_status=None, job_queue=queue)

    asyncio.run(gh.on_chat_member(_joins(), ctx))
    queue.fire(ctx)

    assert _group_posts(ctx) == []
    assert asyncio.run(gdb.get_user(555))["status"] == "nudged"


def test_welcome_falls_back_to_posting_now_without_a_job_queue(live):
    """The gate warns rather than dies when python-telegram-bot[job-queue] is absent."""
    _announced()
    ctx = _ctx(member_status=None, job_queue=None)

    asyncio.run(gh.on_chat_member(_joins(), ctx))

    assert len(_group_posts(ctx)) == 1


# ── Watching groups is managed live, not via config ─────────────────────────────

def _group_cmd(chat_id=-100555, title="Cohort 2026", chat_type="supergroup", admin=True):
    reply = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type, title=title),
        effective_user=SimpleNamespace(id=ADMIN if admin else 999999),
        effective_message=SimpleNamespace(reply_text=reply),
        message=SimpleNamespace(reply_text=reply),
    ), reply


def _admin_answer(ctx, reply):
    """What an admin command answered with.

    Commands typed in a group answer in the admin's DM, so the group is left with
    only the announcement and the roundup; in a private chat they reply in place.
    """
    if ctx.bot.send_message.await_args is not None:
        return ctx.bot.send_message.await_args.kwargs["text"]
    return reply.await_args.args[0]


def test_watch_adds_a_group_without_a_restart(live):
    ctx = _ctx()
    update, reply = _group_cmd()

    asyncio.run(gh.watch_command(update, ctx))

    assert -100555 in gh.monitored_ids()
    assert gh.is_monitored(-100555)
    # Confirmed in the admin's DM; nothing lands in the group itself.
    reply.assert_not_awaited()
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == ADMIN
    assert "-100555" in _admin_answer(ctx, reply)
    # And it survives a cache reload, i.e. it was persisted.
    asyncio.run(gh.refresh_monitored())
    assert gh.is_monitored(-100555)


def test_watch_falls_back_to_the_group_if_the_admin_dm_bounces(live):
    """Silence would read as the command being broken, so a bounced DM is the one
    case where a confirmation is allowed to land in the group."""
    ctx = _ctx()
    ctx.bot.send_message = AsyncMock(side_effect=Forbidden("bot can't initiate"))
    update, reply = _group_cmd()

    asyncio.run(gh.watch_command(update, ctx))

    assert gh.is_monitored(-100555)
    assert "-100555" in reply.await_args.args[0]


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
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == 777


def test_watch_twice_is_idempotent(live):
    ctx = _ctx()
    update, reply = _group_cmd()
    asyncio.run(gh.watch_command(update, ctx))
    asyncio.run(gh.watch_command(update, ctx))

    assert "already watching" in _admin_answer(ctx, reply).lower()
    assert len(asyncio.run(gdb.monitored_chats())) == 2  # the seed + this one


def test_watch_refuses_the_alumni_group_itself(live):
    ctx = _ctx()
    update, reply = _group_cmd(chat_id=ALUMNI_GROUP, title="Alumni")

    asyncio.run(gh.watch_command(update, ctx))

    assert not gh.is_monitored(ALUMNI_GROUP)
    assert "destination" in _admin_answer(ctx, reply).lower()


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
    ctx = _ctx()
    update, reply = _group_cmd(chat_id=-100999123)

    asyncio.run(gh.unwatch_command(update, ctx))

    assert "wasn't watching" in _admin_answer(ctx, reply).lower()


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


def test_promotion_to_admin_starts_watching_but_not_approving(live):
    ctx = _bot_ctx()

    asyncio.run(gh.on_my_chat_member(_promotion(), ctx))

    assert gh.is_monitored(-100555)
    assert not gh.is_approved(-100555)
    # Admins are told, so a group they don't recognise can be dealt with.
    assert ctx.bot.send_message.await_args.kwargs["chat_id"] == ADMIN


def test_a_promoted_but_unapproved_group_is_completely_inert(live):
    """Anyone can create a group and promote the bot, so promotion alone must buy
    nothing: no detection, no announcement, no roundup."""
    ctx = _bot_ctx()
    asyncio.run(gh.on_my_chat_member(_promotion(), ctx))
    ctx.bot.send_message.reset_mock()

    asyncio.run(gh.on_group_message(SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100555, type="supergroup"),
        effective_user=_user(uid=901),
    ), ctx))
    asyncio.run(gh.announce_job(ctx))

    assert asyncio.run(gdb.get_user(901)) is None
    assert -100555 not in _announced_chats(ctx)


def _their_own_group_only(own=-100666):
    """A bot that finds this person ONLY in the group they made themselves.

    The shared _ctx answers every group with the same status, which would make an
    attacker look like a member of the legitimately-approved group too — and hide
    the very thing these two tests are about.
    """
    ctx = _bot_ctx(member_status=None)

    def _member(chat_id, user_id):
        if chat_id == own:
            return SimpleNamespace(status="creator")
        raise BadRequest("user not found")

    ctx.bot.get_chat_member = AsyncMock(side_effect=_member)
    return ctx


def test_a_stranger_cannot_manufacture_their_own_eligibility(live):
    """The attack the approval split exists to stop, end to end.

    Make a group, add the bot, promote it — auto-watch enrols it. If eligibility
    counted watched groups, that alone would let the person through onboarding,
    and the form behind it is public and verifies nothing.
    """
    ctx = _their_own_group_only()
    asyncio.run(gh.on_my_chat_member(_promotion(chat_id=-100666), ctx))
    assert gh.is_monitored(-100666)          # they got it watched...
    assert not gh.is_approved(-100666)       # ...but that grants nothing

    update, reply = _dm(None, user=_user(uid=902))
    asyncio.run(gh.start_onboarding(update, ctx))

    # Refused, and left with no row that a later poll would act on.
    assert "can't find you" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(902)) is None
    ctx.bot.create_chat_invite_link.assert_not_awaited()


def test_approving_that_same_group_then_admits_them(live):
    """The other half: once an admin vouches for it, membership counts."""
    ctx = _their_own_group_only()
    asyncio.run(gh.on_my_chat_member(_promotion(chat_id=-100666), ctx))

    admin_update, _ = _group_cmd(chat_id=-100666, title="Cohort 2026")
    asyncio.run(gh.announce_command(admin_update, ctx))
    assert gh.is_approved(-100666)

    update, _ = _dm(None, user=_user(uid=902))
    asyncio.run(gh.start_onboarding(update, ctx))

    assert asyncio.run(gdb.get_user(902))["status"] == "awaiting_form"


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


# ── The by-name fallback ────────────────────────────────────────────────────────

def _found(name="Sevinch Xasanova"):
    return {"complete": True, "name": name, "matched_by": "full_name"}


_MISSED = {"complete": False, "name": None, "matched_by": None}


def _check_update(user=None):
    reply = AsyncMock()
    query = SimpleNamespace(
        answer=AsyncMock(), message=SimpleNamespace(reply_text=reply)
    )
    return SimpleNamespace(
        callback_query=query, effective_user=user or _user()
    ), reply


def test_form_not_found_asks_for_a_name_when_the_fallback_is_on(live):
    ctx = _ctx(member_status=None)
    update, reply = _check_update()

    with patch.object(gh.formcheck, "lookup", AsyncMock(return_value=_MISSED)), \
            patch.object(gh.formcheck, "name_lookup_available", lambda: True):
        asyncio.run(gh.on_check_form(update, ctx))

    assert "full name" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_name"


def test_form_not_found_says_so_when_the_fallback_is_off(live):
    """Without a name field configured the old message is still the right one."""
    ctx = _ctx(member_status=None)
    update, reply = _check_update()

    with patch.object(gh.formcheck, "lookup", AsyncMock(return_value=_MISSED)), \
            patch.object(gh.formcheck, "name_lookup_available", lambda: False):
        asyncio.run(gh.on_check_form(update, ctx))

    assert "can't see your completed form" in reply.await_args.args[0].lower()


def test_a_matching_name_moves_them_to_the_intro(live):
    asyncio.run(gdb.mark_awaiting_name(555, "alice", "Alice"))
    update, reply = _dm("Sevinch Xasanova")

    with patch.object(gh.formcheck, "lookup_by_name", AsyncMock(return_value=_found())):
        asyncio.run(gh.on_private_text(update, _ctx()))

    row = asyncio.run(gdb.get_user(555))
    assert row["status"] == "awaiting_intro"
    assert row["full_name"] == "Sevinch Xasanova"
    assert "intro" in reply.await_args.args[0].lower()


def test_an_unmatched_name_leaves_them_free_to_try_again(live):
    asyncio.run(gdb.mark_awaiting_name(555, "alice", "Alice"))
    update, reply = _dm("Nobody Here")

    with patch.object(gh.formcheck, "lookup_by_name", AsyncMock(return_value=_MISSED)):
        asyncio.run(gh.on_private_text(update, _ctx()))

    # Still awaiting_name, so another spelling can be tried without starting over.
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_name"
    assert "still can't find" in reply.await_args.args[0].lower()


def test_a_single_word_is_not_accepted_as_a_full_name(live):
    asyncio.run(gdb.mark_awaiting_name(555, "alice", "Alice"))
    update, reply = _dm("Sevinch")

    with patch.object(gh.formcheck, "lookup_by_name", AsyncMock()) as lookup:
        asyncio.run(gh.on_private_text(update, _ctx()))

    lookup.assert_not_awaited()
    assert "first and last" in reply.await_args.args[0].lower()


def test_an_airtable_outage_during_name_lookup_is_not_a_rejection(live):
    asyncio.run(gdb.mark_awaiting_name(555, "alice", "Alice"))
    update, reply = _dm("Sevinch Xasanova")

    with patch.object(gh.formcheck, "lookup_by_name", AsyncMock(return_value=None)):
        asyncio.run(gh.on_private_text(update, _ctx()))

    assert "couldn't reach" in reply.await_args.args[0].lower()
    assert asyncio.run(gdb.get_user(555))["status"] == "awaiting_name"


def test_a_name_typed_by_someone_awaiting_an_intro_is_not_a_name_lookup(live):
    """The two text states must not bleed into each other."""
    asyncio.run(gdb.mark_awaiting_intro(555, "alice", "Alice", None))
    update, _ = _dm("Sevinch Xasanova")

    with patch.object(gh.formcheck, "lookup_by_name", AsyncMock()) as lookup:
        asyncio.run(gh.on_private_text(update, _ctx()))

    lookup.assert_not_awaited()


# ── Stats and the roster ────────────────────────────────────────────────────────
# Both used to key off status = 'registered', which a successful registration
# destroys: joining the alumni group flips the row to 'member'. The counter
# therefore read zero precisely when the gate was working, and the roster lost
# everyone who completed the journey.

def test_registering_then_joining_still_counts_as_registered(live):
    """The status flip to 'member' must not erase the registration."""
    asyncio.run(gdb.mark_registered(555, "alice", "Alice", "Ada", "https://t.me/+a"))
    asyncio.run(gdb.mark_joined_group(555, "alice", "Alice"))

    stats = asyncio.run(gdb.stats())
    assert stats["registered_ever"] == 1
    assert stats["member"] == 1      # they are counted as a member, and
    assert stats["registered"] == 0  # no longer waiting to use their link


def test_the_registered_bucket_means_link_not_used_yet(live):
    """It's a mid-flight count, not a lifetime one — the two must not be conflated."""
    asyncio.run(gdb.mark_registered(1, "ann", "Ann", "Ann A", "https://t.me/+a"))
    asyncio.run(gdb.mark_joined_group(1, "ann", "Ann"))
    asyncio.run(gdb.mark_registered(2, "bob", "Bob", "Bob B", "https://t.me/+b"))

    stats = asyncio.run(gdb.stats())
    assert stats["registered"] == 1       # only Bob still holds an unused link
    assert stats["registered_ever"] == 2  # but both went through the bot


def test_the_roster_keeps_people_who_made_it_into_the_group(live):
    asyncio.run(gdb.mark_registered(1, "ann", "Ann", "Ann A", "https://t.me/+a"))
    asyncio.run(gdb.mark_joined_group(1, "ann", "Ann"))
    asyncio.run(gdb.mark_registered(2, "bob", "Bob", "Bob B", "https://t.me/+b"))

    roster = asyncio.run(gdb.registered_users())
    assert [r["full_name"] for r in roster] == ["Ann A", "Bob B"]


def test_the_roster_excludes_people_who_never_registered(live):
    """A member the bot merely discovered was never admitted through the gate."""
    asyncio.run(gdb.mark_member(3, "cid", "Cid"))
    asyncio.run(gdb.mark_nudged(4, "dee", "Dee", MONITORED))

    assert asyncio.run(gdb.registered_users()) == []
    assert asyncio.run(gdb.stats())["registered_ever"] == 0


def test_every_status_is_shown_and_the_buckets_add_up_to_the_total(live):
    """A status missing from the message would silently break the arithmetic."""
    asyncio.run(gdb.mark_member(1, "ann", "Ann"))
    asyncio.run(gdb.mark_nudged(2, "bob", "Bob", MONITORED))
    asyncio.run(gdb.mark_awaiting_form(3, "cid", "Cid"))
    asyncio.run(gdb.mark_awaiting_name(4, "dee", "Dee"))
    asyncio.run(gdb.mark_awaiting_intro(5, "eve", "Eve", "Eve E"))
    asyncio.run(gdb.mark_registered(6, "fay", "Fay", "Fay F", "https://t.me/+f"))

    stats = asyncio.run(gdb.stats())
    assert sum(stats[s] for s in gdb.VALID_STATUSES) == stats["total"] == 6
    for status in gdb.VALID_STATUSES:
        assert "{" + status + "}" in gmsg.STATS, f"{status} has no line in the message"
    rendered = gmsg.STATS.format(**stats)  # and every placeholder is supplied
    assert "Registered through the bot: <b>1</b>" in rendered
