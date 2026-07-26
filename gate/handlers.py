"""Alumni Gate handlers: detection, onboarding, and admin utilities.

The gate makes sure everyone in the monitored groups ends up in the one official
alumni group. Because the Bot API can't enumerate a group's roster, detection is
event-driven rather than scan-based, on three triggers:

  * someone joins a monitored group;
  * someone posts in one for the first time we've seen;
  * someone taps the pinned announcement's "check if I'm in" button.

The third exists because the first two can't see anyone who was already in a
group before the bot arrived and who never posts. The tap supplies the one thing
the membership check needs — the person's user ID — and it also means the bot has
now *seen* them, which is what makes a tg://user mention resolve into a real ping.

Onboarding is gated on an Airtable form submission and then on the person sending
a real intro, at which point they get a personal single-use invite link.

Everything here is dormant unless settings.active() — the master switch plus a
configured alumni group.
"""
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
CHECK_ME_CB = "gate:check_me"
ENTER_CB = "start:alumnigate"

# The announcement job wakes up far more often than the re-post interval and lets
# the recorded timestamp decide whether a group is due, so the cadence holds even
# across bot restarts.
_ANNOUNCE_TICK_SECONDS = 3600


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

    await db.mark_nudged(user.id, user.username, user.first_name)
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

    if chat_id in settings.MONITORED_GROUP_IDS:
        await _process_user(context, chat_id, user)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """First-time posters in a monitored group get checked."""
    chat = update.effective_chat
    if chat is None or chat.id not in settings.MONITORED_GROUP_IDS:
        return
    await _process_user(context, chat.id, update.effective_user)


# ── Group announcement: the cold-start path ──────────────────────────────────────

def _announce_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(msg.GROUP_ANNOUNCE_BUTTON, callback_data=CHECK_ME_CB)]]
    )


def _nudged_recently(row: dict | None) -> bool:
    """True if this person was already tagged within the current cycle.

    Repeat taps on the announcement shouldn't produce repeat public tags, but a
    tag from a cycle ago is fair game again. With the re-post disabled we keep the
    stricter "nudge once, ever" rule.
    """
    when = _parse_ts(row["nudged_at"]) if row else None
    if when is None:
        return False
    if settings.ANNOUNCE_INTERVAL_DAYS <= 0:
        return True
    return datetime.now(timezone.utc) - when < timedelta(
        days=settings.ANNOUNCE_INTERVAL_DAYS
    )


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
    for chat_id in settings.MONITORED_GROUP_IDS:
        if _announcement_due(await db.get_announcement(chat_id)):
            await post_announcement(context, chat_id)


async def on_check_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The announcement's "Check if I'm in the Alumni group" button.

    Tapping is what makes the cold-start case work at all: the tap carries the
    person's user ID, so the membership check can run on someone the bot has never
    otherwise seen.

    Already a member -> private popup, nothing posted; the group never learns who
                        checked or who was already in.
    Not a member     -> tagged publicly (at most once per cycle) and the tap itself
                        opens the bot, so onboarding starts immediately.
    """
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat
    chat_id = chat.id if chat is not None else None

    if not settings.active():
        await query.answer(msg.CB_NOT_CONFIGURED, show_alert=True)
        return

    if await _is_member(context, user.id):
        await db.mark_member(user.id, user.username, user.first_name)
        await query.answer(msg.CB_ALREADY_MEMBER, show_alert=True)
        return

    row = await db.get_user(user.id)
    onboarded = bool(row and row["status"] == "registered" and row["invite_link"])

    # Someone who already finished onboarding doesn't need telling to register —
    # opening the bot re-hands them their existing link.
    if (
        chat_id in settings.MONITORED_GROUP_IDS
        and not onboarded
        and not _nudged_recently(row)
    ):
        await _post_nudge(context, chat_id, user)

    # Answering with a t.me deep link opens the bot on their device, so the same
    # tap that identified them also starts onboarding. The public tag is the
    # reminder and the record.
    await query.answer(url=_register_url(context.bot.username))


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
    completed = await formcheck.fetch_completed()
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
        if chat.id in settings.MONITORED_GROUP_IDS
        else list(settings.MONITORED_GROUP_IDS)
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
    app.add_handler(CallbackQueryHandler(on_check_me, pattern=f"^{CHECK_ME_CB}$"))

    app.add_handler(CommandHandler("gate_stats", stats_command, filters=_private))
    app.add_handler(CommandHandler("gate_list", list_command, filters=_private))
    app.add_handler(CommandHandler("gate_announce", announce_command))

    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
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

    if settings.ANNOUNCE_INTERVAL_DAYS > 0 and settings.MONITORED_GROUP_IDS:
        app.job_queue.run_repeating(
            announce_job, interval=_ANNOUNCE_TICK_SECONDS, first=60
        )
        logger.info(
            "Gate announcement re-posts every %d days", settings.ANNOUNCE_INTERVAL_DAYS
        )
