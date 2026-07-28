"""Alumni Gate handlers: detection, onboarding, and admin utilities.

The gate makes sure everyone in the monitored groups ends up in the one official
alumni group. Because the Bot API can't enumerate a group's roster, detection is
event-driven rather than scan-based, on three triggers:

  * someone joins a monitored group;
  * someone posts in one for the first time we've seen;
  * someone taps either button on the pinned announcement.

The third exists because the first two can't see anyone who was already in a
group before the bot arrived and who never posts. The tap supplies the one thing
the membership check needs — the person's user ID — and it also means the bot has
now *seen* them, which is what makes a tg://user mention resolve into a real ping.

Both announcement buttons are verified against real membership, including the one
that says "I'm already in it": the tap has already given us the ID, so a claim
costs the same to confirm as to accept, and someone wrongly skipped is skipped for
good. A cycle later, followup_job chases everyone still unengaged — but only those
the bot has an ID for, which is a genuine ceiling, not an implementation gap.

Onboarding is gated on an Airtable form submission and then on the person sending
a real intro, at which point they get a personal single-use invite link.

Everything here is dormant unless settings.active() — the master switch plus a
configured alumni group.
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS

from . import db, formcheck, settings
from . import messages as msg

logger = logging.getLogger(__name__)

# Chat-member statuses that count as "currently in the group".
_PRESENT_STATUSES = {"creator", "administrator", "member", "restricted"}

_private = filters.ChatType.PRIVATE

# Callback data. Must not collide with the host bot's patterns.
CHECK_FORM_CB = "gate:check_form"
JOIN_CB = "gate:join"
ALREADY_CB = "gate:already"
ENTER_CB = "start:alumnigate"

# The announcement job wakes up far more often than the re-post interval and lets
# the recorded timestamp decide whether a group is due, so the cadence holds even
# across bot restarts.
_ANNOUNCE_TICK_SECONDS = 3600

# The follow-up sweep names people in batches: Telegram caps a group at roughly 20
# messages a minute, so one message each would hit the flood limit and bury the
# group, while a batched mention still notifies everyone named.
_FOLLOWUP_BATCH = 8
_FOLLOWUP_BATCH_PAUSE = 4.0

# Which groups are being watched. Held in memory because it's consulted on every
# group message, and refreshed whenever it changes — safe because exactly one
# instance of this bot may run at a time (two would fight over getUpdates anyway).
_monitored: set[int] = set()


def monitored_ids() -> set[int]:
    """The groups currently being watched."""
    return set(_monitored)


def is_monitored(chat_id: int | None) -> bool:
    return chat_id is not None and chat_id in _monitored


async def refresh_monitored() -> set[int]:
    """Reload the watched-group cache from the database."""
    global _monitored
    _monitored = {row["chat_id"] for row in await db.monitored_chats()}
    return set(_monitored)


async def load_monitored(seed: list[int] | None = None) -> set[int]:
    """Prime the cache at startup, seeding from GATE_MONITORED_GROUP_IDS.

    The environment variable is a bootstrap only: anything listed there is copied
    into the database once, after which the group list is managed live with
    /gate_watch and /gate_unwatch. Removing an id from the variable does not
    unwatch it — use /gate_unwatch.
    """
    for chat_id in settings.MONITORED_GROUP_IDS if seed is None else seed:
        if chat_id == settings.GROUP_ID:
            continue  # never watch the destination group
        await db.add_monitored_chat(chat_id, None)
    return await refresh_monitored()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if the user is currently in the alumni group.

    A non-member typically makes get_chat_member raise ("user not found") or
    return status 'left'; both mean "not a member". A genuine failure (e.g. the
    bot isn't admin in the alumni group) also lands here — we log it and treat the
    user as a non-member, so setup problems surface as over-nudging rather than
    silent success.
    """
    try:
        member = await context.bot.get_chat_member(settings.GROUP_ID, user_id)
    except Exception:
        logger.debug("gate get_chat_member failed for user %d", user_id)
        return False
    return member.status in _PRESENT_STATUSES


async def _create_invite_link(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> str | None:
    """Mint a single-use invite link to the alumni group, or None on failure."""
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=settings.GROUP_ID,
            name=f"Alumni {user_id}"[:32],
            member_limit=1,
        )
        return invite.invite_link
    except Exception:
        logger.exception("Failed to create alumni invite link for user %d", user_id)
        return None


def _mention(user_id: int, first_name: str | None) -> str:
    """An HTML text-mention that pings the user even without a username."""
    name = html.escape(first_name or "there")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def _register_url(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start={settings.START_PAYLOAD}"


def _join_markup(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(msg.JOIN_BUTTON, url=link)]])


def _parse_ts(raw: str | None) -> datetime | None:
    """Parse a stored ISO timestamp, tolerating naive values as UTC."""
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


# ── Detection ───────────────────────────────────────────────────────────────────

async def _post_nudge(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user) -> bool:
    """Tag the user publicly with the Register button. True if it went out."""
    button = InlineKeyboardButton(
        msg.GROUP_NUDGE_BUTTON, url=_register_url(context.bot.username)
    )
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg.GROUP_NUDGE.format(mention=_mention(user.id, user.first_name)),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[button]]),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception(
            "Failed to post gate nudge for user %d in chat %d", user.id, chat_id
        )
        return False

    # Record which group, so the follow-up sweep chases them where they actually
    # are rather than in every monitored group.
    await db.mark_nudged(user.id, user.username, user.first_name, chat_id)
    logger.info("Gate-nudged user %d (@%s) in chat %d", user.id, user.username, chat_id)
    return True


async def _process_user(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user
) -> None:
    """Check one user against the alumni group and nudge them once if missing.

    Anyone already classified (member / nudged / onboarding / registered) is
    skipped — this enforces "nudge once" and caches the membership lookup.
    """
    if user is None or user.is_bot:
        return
    if not settings.active():
        return  # dormant or not configured

    if await db.get_user(user.id) is not None:
        return

    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        return

    await _post_nudge(context, chat_id, user)


def _just_joined(result) -> bool:
    old = result.old_chat_member.status
    new = result.new_chat_member.status
    return old not in _PRESENT_STATUSES and new in _PRESENT_STATUSES


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Someone's membership changed in a group where the bot is admin."""
    result = update.chat_member
    if result is None or not _just_joined(result):
        return

    chat_id = result.chat.id
    user = result.new_chat_member.user

    if chat_id == settings.GROUP_ID and settings.GROUP_ID != 0:
        # They made it into the alumni group — record it so stats stay honest and
        # we never nudge them.
        await db.mark_member(user.id, user.username, user.first_name)
        return

    if is_monitored(chat_id):
        await _process_user(context, chat_id, user)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The bot's own status changed in a chat — start or stop watching it.

    Being promoted to administrator is treated as the opt-in: the gate cannot
    function without admin rights, so someone granting them has already decided
    this group should be handled. That saves running /gate_watch by hand in every
    group, which doesn't scale when groups are added regularly.

    Removal is the opposite signal, so the group is dropped — posting into a chat
    the bot has been thrown out of only produces errors.
    """
    result = update.my_chat_member
    if result is None or result.new_chat_member.user.id != context.bot.id:
        return

    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    new_status = result.new_chat_member.status

    if new_status in ("left", "kicked"):
        if await db.remove_monitored_chat(chat.id):
            await refresh_monitored()
            logger.info("Removed from chat %d — stopped watching it", chat.id)
        return

    # Never watch the destination group: it would nudge people about the very
    # group they're already in.
    if chat.id == settings.GROUP_ID:
        return
    if not settings.AUTO_WATCH or new_status != "administrator":
        return
    if is_monitored(chat.id):
        return

    await db.add_monitored_chat(chat.id, chat.title)
    await refresh_monitored()
    logger.info("Promoted to admin in chat %d (%s) — now watching it",
                chat.id, chat.title)

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=msg.AUTO_WATCHED.format(
                    title=html.escape(chat.title or str(chat.id)), chat_id=chat.id
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("Could not tell admin %d about auto-watching", admin_id)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """First-time posters in a monitored group get checked."""
    chat = update.effective_chat
    if chat is None or not is_monitored(chat.id):
        return
    await _process_user(context, chat.id, update.effective_user)


# ── Group announcement: the cold-start path ──────────────────────────────────────

def _announce_markup() -> InlineKeyboardMarkup:
    """Two buttons: one to start joining, one to declare you're already in.

    Both are verified against the real membership — see on_already_tap for why
    the second is not taken at face value.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(msg.GROUP_ANNOUNCE_JOIN_BUTTON, callback_data=JOIN_CB)],
        [InlineKeyboardButton(
            msg.GROUP_ANNOUNCE_ALREADY_BUTTON, callback_data=ALREADY_CB
        )],
    ])


def _announcement_due(previous: dict | None) -> bool:
    """True if this group is due a fresh announcement."""
    posted = _parse_ts(previous["posted_at"]) if previous else None
    if posted is None:
        return True
    return datetime.now(timezone.utc) - posted >= timedelta(
        days=settings.ANNOUNCE_INTERVAL_DAYS
    )


async def post_announcement(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Put a fresh pinned announcement in a group, clearing the previous one.

    Posting is the only step that must succeed; deleting the stale message and
    pinning the new one are best-effort (they need pin rights, and the old message
    may already be gone).
    """
    previous = await db.get_announcement(chat_id)
    try:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=msg.GROUP_ANNOUNCE,
            parse_mode="HTML",
            reply_markup=_announce_markup(),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to post gate announcement in chat %d", chat_id)
        return False

    await db.set_announcement(chat_id, sent.message_id)

    if previous:
        # Deleting also drops its pin, so there's no separate unpin.
        try:
            await context.bot.delete_message(chat_id, previous["message_id"])
        except Exception:
            logger.debug(
                "Could not delete old announcement %s in chat %d",
                previous["message_id"], chat_id,
            )
    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id, message_id=sent.message_id, disable_notification=True
        )
    except Exception:
        logger.debug("Could not pin announcement in chat %d", chat_id)

    logger.info("Posted gate announcement %d in chat %d", sent.message_id, chat_id)
    return True


async def announce_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-post the announcement in any monitored group that's due one."""
    if not settings.active():
        return
    for chat_id in sorted(monitored_ids()):
        if _announcement_due(await db.get_announcement(chat_id)):
            await post_announcement(context, chat_id)


async def followup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A cycle after being tagged, chase everyone who still hasn't engaged.

    Targets are people with status 'nudged' whose tag is older than
    GATE_ANNOUNCE_INTERVAL_DAYS — i.e. seen in a monitored group, told once, and
    they've tapped nothing since.

    HARD LIMIT worth understanding: this can only reach people the bot has a user
    ID for. Telegram never enumerates a group's members, so someone who has never
    joined, posted, or tapped since the bot arrived does not exist as far as this
    sweep is concerned and cannot be tagged. The pinned announcement is what
    converts those people into known IDs.
    """
    if not settings.active() or settings.ANNOUNCE_INTERVAL_DAYS <= 0:
        return

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=settings.ANNOUNCE_INTERVAL_DAYS)
    ).isoformat()

    for chat_id in sorted(monitored_ids()):
        stale = await db.stale_nudged_users(cutoff, chat_id)
        if not stale:
            continue

        logger.info(
            "Follow-up sweep: chasing %d unengaged people in chat %d",
            len(stale), chat_id,
        )
        button = InlineKeyboardButton(
            msg.GROUP_NUDGE_BUTTON, url=_register_url(context.bot.username)
        )

        # Batch the mentions. Telegram caps a group at roughly 20 messages a
        # minute, so one message per person would both hit the flood limit and
        # bury the group. Everyone named still gets a real notification.
        for start in range(0, len(stale), _FOLLOWUP_BATCH):
            batch = stale[start:start + _FOLLOWUP_BATCH]
            mentions = ", ".join(
                _mention(row["user_id"], row["first_name"]) for row in batch
            )
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg.GROUP_FOLLOWUP.format(mentions=mentions),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[button]]),
                    disable_web_page_preview=True,
                )
            except Exception:
                # Leave their nudged_at alone so the next sweep retries them.
                logger.exception(
                    "Follow-up batch failed in chat %d; will retry next sweep",
                    chat_id,
                )
                break

            for row in batch:
                await db.mark_nudged(
                    row["user_id"], row["username"], row["first_name"], chat_id
                )
            # Space the batches out, but don't idle after the last one.
            if start + _FOLLOWUP_BATCH < len(stale):
                await asyncio.sleep(_FOLLOWUP_BATCH_PAUSE)


async def on_join_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The announcement's "Join the Alumni group" button.

    Tapping is what makes the cold-start case work at all: the tap carries the
    person's user ID, so membership can be checked for someone the bot has never
    otherwise seen.

    Already a member -> a private popup saying so; nothing is posted.
    Not a member     -> the tap itself opens the bot, so onboarding starts
                        immediately. Nothing is posted publicly either: they
                        volunteered, so a public tag would only be noise.
    """
    query = update.callback_query
    user = query.from_user

    if not settings.active():
        await query.answer(msg.CB_NOT_CONFIGURED, show_alert=True)
        return

    # Check anyway — someone who's already in shouldn't be walked through a form.
    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        await query.answer(msg.CB_ALREADY_MEMBER, show_alert=True)
        return

    # Answering with a t.me deep link opens the bot on their device, so the same
    # tap that identified them also starts onboarding. start_onboarding re-hands
    # an existing invite link if they've already been through this.
    await query.answer(url=_register_url(context.bot.username))


async def on_already_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The announcement's "I'm already in it" button.

    Deliberately NOT taken at face value. The tap hands us the user ID, and
    checking real membership costs one API call and no user effort, so there's no
    reason to accept a claim when we can confirm it. People tap this to dismiss a
    message, and someone wrongly skipped is skipped for good — which is exactly
    the person the gate exists to find.

    Confirmed  -> recorded as a member, so they're never asked again.
    Contradicted -> told privately, and pointed at the Join button. Nothing is
                    posted publicly; being publicly corrected would be worse than
                    the problem.
    """
    query = update.callback_query
    user = query.from_user

    if not settings.active():
        await query.answer(msg.CB_NOT_CONFIGURED, show_alert=True)
        return

    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        await query.answer(msg.CB_ALREADY_MEMBER, show_alert=True)
        return

    # They believe they're in and they're not — most often a second Telegram
    # account, or they left at some point. Correct it publicly so the claim can't
    # be used to quietly opt out, and record the tag so the follow-up sweep counts
    # them as already chased.
    logger.info(
        "User %d (@%s) claimed alumni membership but isn't in the group",
        user.id, user.username,
    )
    chat = update.effective_chat
    if chat is not None and is_monitored(chat.id):
        button = InlineKeyboardButton(
            msg.GROUP_NUDGE_BUTTON, url=_register_url(context.bot.username)
        )
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=msg.GROUP_NOT_ACTUALLY_MEMBER.format(
                    mention=_mention(user.id, user.first_name)
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[button]]),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "Failed to post false-claim correction for user %d", user.id
            )
        else:
            await db.mark_nudged(user.id, user.username, user.first_name, chat.id)

    await query.answer(msg.CB_NOT_ACTUALLY_MEMBER, show_alert=True)


# ── Onboarding ──────────────────────────────────────────────────────────────────

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: the menu button, /alumni, or the group nudge's deep link.

    Short-circuits if the gate is dormant or the person is already in the alumni
    group. Otherwise it records them as awaiting their form and posts the
    onboarding brief with the form / doc / check buttons.
    """
    user = update.effective_user
    message = update.effective_message
    if update.callback_query is not None:
        await update.callback_query.answer()

    if not settings.LIVE:
        await message.reply_text(msg.COMING_SOON, parse_mode="HTML")
        return
    if settings.GROUP_ID == 0:
        await message.reply_text(msg.NOT_CONFIGURED, parse_mode="HTML")
        return

    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        await message.reply_text(msg.ALREADY_MEMBER, parse_mode="HTML")
        return

    # Already cleared but not in the group yet (never tapped their link, or it
    # expired on them) — re-hand the link instead of sending them back to step 1.
    existing = await db.get_user(user.id)
    if existing and existing["status"] == "registered" and existing["invite_link"]:
        await message.reply_text(
            msg.ALREADY_REGISTERED,
            parse_mode="HTML",
            reply_markup=_join_markup(existing["invite_link"]),
        )
        return

    await db.mark_awaiting_form(user.id, user.username, user.first_name)

    keyboard: list[list[InlineKeyboardButton]] = []
    form_url = formcheck.personalized_form_url(user.id)
    if form_url:
        keyboard.append([InlineKeyboardButton(msg.FORM_BUTTON, url=form_url)])
    if settings.VALUES_DOC_URL:
        keyboard.append([InlineKeyboardButton(msg.DOC_BUTTON, url=settings.VALUES_DOC_URL)])
    keyboard.append([InlineKeyboardButton(msg.CHECK_BUTTON, callback_data=CHECK_FORM_CB)])

    await message.reply_text(
        msg.WELCOME_ONBOARDING,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


async def on_check_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The "I've completed the form ✅" button: verify against Airtable.

    Complete  -> move them to the intro step and ask for it.
    Not found -> tell them to finish/submit the form.
    Can't check (Airtable down/unconfigured) -> say so; never reject on error.
    """
    query = update.callback_query
    if query is not None:
        await query.answer()
    user = update.effective_user
    reply = query.message.reply_text

    if not settings.active():
        await reply(msg.NOT_CONFIGURED, parse_mode="HTML")
        return

    existing = await db.get_user(user.id)
    if existing and existing["status"] == "registered" and existing["invite_link"]:
        await reply(
            msg.ALREADY_REGISTERED,
            parse_mode="HTML",
            reply_markup=_join_markup(existing["invite_link"]),
        )
        return

    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        await reply(msg.ALREADY_MEMBER, parse_mode="HTML")
        return

    # The username is passed so a submission predating tg_id can still be found.
    result = await formcheck.lookup(user.id, user.username)
    if result is None:
        await reply(msg.CHECK_UNAVAILABLE, parse_mode="HTML")
        return
    if not result["complete"]:
        await reply(msg.FORM_NOT_VERIFIED, parse_mode="HTML")
        return

    await db.mark_awaiting_intro(
        user.id, user.username, user.first_name, result.get("name")
    )
    await reply(msg.ASK_INTRO, parse_mode="HTML")


async def _issue_invite(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    username: str | None,
    first_name: str | None,
    full_name: str | None,
) -> str | None:
    """Mint the one-time link and mark the user registered. None on failure."""
    link = await _create_invite_link(context, user_id)
    if not link:
        return None
    await db.mark_registered(user_id, username, first_name, full_name, link)
    return link


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Private DMs. The only text the gate acts on is the intro from someone whose
    form is already verified — receiving it is the final gate; we don't store or
    forward it, since they re-post it in the alumni group after joining.

    Registered by the host bot's build_app AFTER every ConversationHandler, so
    this never steals input from another flow.
    """
    user = update.effective_user
    if user is None:
        return
    row = await db.get_user(user.id)
    if row is None:
        return

    if row["status"] == "registered" and row["invite_link"]:
        # Already onboarded — re-hand their link rather than mint a fresh one.
        await update.message.reply_text(
            msg.ALREADY_REGISTERED,
            parse_mode="HTML",
            reply_markup=_join_markup(row["invite_link"]),
        )
        return

    if row["status"] != "awaiting_intro":
        return  # not expecting an intro from this person

    text = (update.message.text or "").strip()
    if not text:
        return

    # The brief asks for 50-100 words; hold the gate if this clearly isn't that.
    words = len(text.split())
    if words < settings.INTRO_MIN_WORDS:
        await update.message.reply_text(
            msg.INTRO_TOO_SHORT.format(
                count=words,
                plural="" if words == 1 else "s",
                minimum=settings.INTRO_MIN_WORDS,
            ),
            parse_mode="HTML",
        )
        return

    link = await _issue_invite(
        context, user.id, user.username, user.first_name, row["full_name"]
    )
    if not link:
        await update.message.reply_text(msg.LINK_FAILED, parse_mode="HTML")
        return

    name = html.escape(row["full_name"] or user.first_name or "there")
    await update.message.reply_text(
        msg.ADMITTED.format(name=name),
        parse_mode="HTML",
        reply_markup=_join_markup(link),
    )
    logger.info("Gate admitted user %d (@%s) after form + intro", user.id, user.username)


async def on_private_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Private DMs that aren't text — voice notes, photos, stickers, forwards.

    Only people we're actually waiting on get a reply; silence for everyone else
    matches how plain text is treated. Without this a student who records their
    intro as a voice note gets no response at all and assumes the bot is broken.
    """
    user = update.effective_user
    if user is None or update.message is None:
        return
    row = await db.get_user(user.id)
    if row is None:
        return

    if row["status"] == "registered" and row["invite_link"]:
        await update.message.reply_text(
            msg.ALREADY_REGISTERED,
            parse_mode="HTML",
            reply_markup=_join_markup(row["invite_link"]),
        )
        return

    if row["status"] == "awaiting_intro":
        await update.message.reply_text(msg.INTRO_NEEDS_TEXT, parse_mode="HTML")


# ── Background poll: advance people whose form is now complete ────────────────────

async def poll_forms(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-check everyone still on the form step and, for those whose submission
    has landed, move them to the intro step and prompt them — so a student who
    submits the form but never taps the button isn't left stuck.
    """
    if not settings.active() or not settings.airtable_ready():
        return
    waiting = await db.awaiting_form_users()
    if not waiting:
        return
    # Ask about these specific people rather than scanning the table: cost scales
    # with the number mid-onboarding, not with how many rows exist.
    completed = await formcheck.fetch_completed_for(waiting)
    if not completed:
        return
    by_id = completed.get("by_id", {})
    by_username = completed.get("by_username", {})

    for row in waiting:
        key = str(row["user_id"])
        if key in by_id:
            found = by_id[key]
        else:
            # Fall back to a pre-tg_id submission matched on username.
            handle = formcheck._normalize_username(row["username"])
            if not handle or handle not in by_username:
                continue
            found = by_username[handle]
            logger.info(
                "Poll matched user %d to a pre-tg_id submission by username @%s",
                row["user_id"], handle,
            )
        name = found or row["full_name"] or row["first_name"]
        await db.mark_awaiting_intro(
            row["user_id"], row["username"], row["first_name"], name
        )
        try:
            await context.bot.send_message(
                chat_id=row["user_id"], text=msg.ASK_INTRO, parse_mode="HTML"
            )
        except Exception:
            logger.exception("Failed to DM intro prompt to user %d", row["user_id"])


# ── Admin ───────────────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    counts = await db.stats()
    await update.message.reply_text(msg.STATS.format(**counts), parse_mode="HTML")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    registered = await db.registered_users()
    if not registered:
        await update.message.reply_text(msg.LIST_EMPTY)
        return
    lines = [msg.LIST_HEADER.format(count=len(registered))]
    for idx, row in enumerate(registered, start=1):
        username_part = f" (@{row['username']})" if row.get("username") else ""
        lines.append(msg.LIST_ENTRY.format(
            idx=idx,
            full_name=row.get("full_name") or row.get("first_name") or "—",
            username_part=username_part,
        ))
    await update.message.reply_text("\n".join(lines))


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start watching the group this is run in (admins only).

    Run in the group itself rather than taking an ID argument: you're already
    there, and it can't be pointed at the wrong chat by a typo.
    """
    chat = update.effective_chat
    user = update.effective_user
    if user is None or not _is_admin(user.id):
        return
    if chat is None or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            msg.WATCH_NOT_A_GROUP, parse_mode="HTML"
        )
        return
    if chat.id == settings.GROUP_ID:
        await update.effective_message.reply_text(
            msg.WATCH_IS_ALUMNI_GROUP, parse_mode="HTML"
        )
        return

    title = chat.title or str(chat.id)
    added = await db.add_monitored_chat(chat.id, chat.title)
    await refresh_monitored()

    template = msg.WATCH_ADDED if added else msg.WATCH_ALREADY
    await update.effective_message.reply_text(
        template.format(title=html.escape(title), chat_id=chat.id), parse_mode="HTML"
    )
    logger.info("Now watching chat %d (%s)", chat.id, title)


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop watching the group this is run in (admins only)."""
    chat = update.effective_chat
    user = update.effective_user
    if user is None or not _is_admin(user.id):
        return
    if chat is None or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            msg.WATCH_NOT_A_GROUP, parse_mode="HTML"
        )
        return

    removed = await db.remove_monitored_chat(chat.id)
    await refresh_monitored()
    template = msg.UNWATCH_DONE if removed else msg.UNWATCH_NOT_WATCHED
    await update.effective_message.reply_text(
        template.format(title=html.escape(chat.title or str(chat.id))),
        parse_mode="HTML",
    )
    logger.info("Stopped watching chat %d", chat.id)


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the watched groups and the destination (admins only, DM)."""
    if not _is_admin(update.effective_user.id):
        return
    chats = await db.monitored_chats()
    if not chats:
        await update.message.reply_text(msg.GROUPS_EMPTY, parse_mode="HTML")
        return
    lines = [
        msg.GROUPS_HEADER.format(
            count=len(chats), destination=settings.GROUP_ID or "not set"
        ),
        "",
    ]
    for row in chats:
        lines.append(msg.GROUPS_ENTRY.format(
            title=html.escape(row["title"] or "—"), chat_id=row["chat_id"]
        ))
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def announce_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post the announcement now, without waiting for the cycle (admins only).

    Run inside a monitored group it posts there; run in a DM it posts to every
    monitored group. Unlike the job this ignores the re-post interval, since an
    admin asking for it is the whole point.
    """
    chat = update.effective_chat
    user = update.effective_user

    if user is None or not _is_admin(user.id):
        # Stay silent in groups so members can't discover the command by noise.
        if chat is not None and chat.type == "private":
            return
        return

    if not settings.active():
        await update.effective_message.reply_text(
            msg.ANNOUNCE_DORMANT, parse_mode="HTML"
        )
        return

    targets = (
        [chat.id]
        if is_monitored(chat.id)
        else sorted(monitored_ids())
    )
    if not targets:
        await update.effective_message.reply_text(
            msg.ANNOUNCE_NO_TARGETS, parse_mode="HTML"
        )
        return

    posted = 0
    for chat_id in targets:
        if await post_announcement(context, chat_id):
            posted += 1

    # In a group the announcement itself is the feedback.
    if chat.type == "private":
        await update.message.reply_text(
            msg.ANNOUNCE_DONE.format(ok=posted, total=len(targets)), parse_mode="HTML"
        )


# ── Wiring ──────────────────────────────────────────────────────────────────────

def register(app: Application) -> None:
    """Add the gate's handlers and jobs to the host application.

    Call this LAST in build_app: the private-message handlers below match broadly,
    and registering them after every ConversationHandler means an in-progress
    conversation always wins.
    """
    app.add_handler(CommandHandler("alumni", start_onboarding, filters=_private))
    app.add_handler(
        MessageHandler(_private & filters.Text([msg.MENU_BUTTON]), start_onboarding)
    )
    app.add_handler(CallbackQueryHandler(start_onboarding, pattern=f"^{ENTER_CB}$"))
    app.add_handler(CallbackQueryHandler(on_check_form, pattern=f"^{CHECK_FORM_CB}$"))
    app.add_handler(CallbackQueryHandler(on_join_tap, pattern=f"^{JOIN_CB}$"))
    app.add_handler(CallbackQueryHandler(on_already_tap, pattern=f"^{ALREADY_CB}$"))

    app.add_handler(CommandHandler("gate_stats", stats_command, filters=_private))
    app.add_handler(CommandHandler("gate_list", list_command, filters=_private))
    app.add_handler(CommandHandler("gate_announce", announce_command))
    app.add_handler(CommandHandler("gate_watch", watch_command))
    app.add_handler(CommandHandler("gate_unwatch", unwatch_command))
    app.add_handler(CommandHandler("gate_groups", groups_command, filters=_private))

    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    # group=1: the host bot already handles MY_CHAT_MEMBER to report chat IDs, and
    # within one handler group only the first match runs. A separate group lets
    # both fire for the same update.
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL & ~filters.COMMAND,
            on_group_message,
        )
    )

    # Private DMs: the intro step, then a fallback so non-text gets an answer.
    app.add_handler(
        MessageHandler(_private & filters.TEXT & ~filters.COMMAND, on_private_text)
    )
    app.add_handler(
        MessageHandler(
            _private & ~filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            on_private_non_text,
        )
    )

    if app.job_queue is None:
        logger.warning(
            "No JobQueue available — the gate's form poll and announcement "
            "re-posts will not run. Install python-telegram-bot[job-queue]."
        )
        return

    if not settings.active():
        logger.info("Alumni Gate is dormant (GATE_LIVE off or GATE_GROUP_ID unset).")
        return

    if settings.POLL_INTERVAL_MINUTES > 0 and settings.airtable_ready():
        seconds = settings.POLL_INTERVAL_MINUTES * 60
        app.job_queue.run_repeating(poll_forms, interval=seconds, first=seconds)
        logger.info(
            "Gate form poll scheduled every %d min", settings.POLL_INTERVAL_MINUTES
        )

    if settings.ANNOUNCE_INTERVAL_DAYS > 0:
        app.job_queue.run_repeating(
            announce_job, interval=_ANNOUNCE_TICK_SECONDS, first=60
        )
        # Same hourly tick, offset so the two never post in the same minute. Each
        # decides for itself whether anything is actually due.
        app.job_queue.run_repeating(
            followup_job, interval=_ANNOUNCE_TICK_SECONDS, first=900
        )
        logger.info(
            "Gate announcement re-posts every %d days; unengaged people are "
            "chased on the same cycle", settings.ANNOUNCE_INTERVAL_DAYS
        )
