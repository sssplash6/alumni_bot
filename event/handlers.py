"""Event handlers: the two-chat gate, name collection, and admin utilities.

Registration requires being in BOTH the alumni group and the alumni channel.
That gives three outcomes rather than two, and the middle one is the interesting
one:

  * in both        -> show the post, ask for a full name, done;
  * in exactly one -> hand them a one-time link to the chat they're missing, and
                      pick them up automatically when they join it;
  * in neither     -> the event is alumni-only, and no link is offered, since
                      handing an outsider a way in would defeat the point.

The bot must be an admin in both chats: that is what makes membership readable
and invite links mintable, and it is also what makes Telegram deliver the join
events the middle case depends on.

Everything is dormant unless settings.active().
"""
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS

from . import db, settings
from . import messages as msg

logger = logging.getLogger(__name__)

# Chat-member statuses that count as "currently in the chat".
_PRESENT_STATUSES = {"creator", "administrator", "member", "restricted"}

_private = filters.ChatType.PRIVATE

# The only conversation state: waiting for a full name. States are scoped to
# their own ConversationHandler, so this can't collide with the host bot's.
ASK_NAME = 0

ENTER_CB = "event:enter"

# Which chat someone is short of. Values double as the {what} in the copy.
_GROUP = "group"
_CHANNEL = "channel"


def _chat_id_for(what: str) -> int:
    return settings.GROUP_ID if what == _GROUP else settings.CHANNEL_ID


# ── Membership ──────────────────────────────────────────────────────────────────

async def _is_in(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
) -> bool | None:
    """Whether the user is in one chat. None means the question couldn't be asked.

    Telegram reports "not a participant" by raising BadRequest rather than
    returning a status, so a bare except would make an outage indistinguishable
    from an outsider — and telling a real alum the event isn't for them is the
    one outcome worse than asking them to retry. Forbidden means the bot isn't
    an admin in that chat, which is our misconfiguration, so it is also "don't
    know" rather than "not a member".
    """
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except BadRequest:
        return False
    except Forbidden:
        logger.error(
            "Can't read members of chat %d — the bot needs to be an admin there "
            "for event registration to work.", chat_id,
        )
        return None
    except Exception:
        logger.warning("Event membership lookup failed for chat %d", chat_id)
        return None
    return member.status in _PRESENT_STATUSES


async def _missing_chats(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> list[str] | None:
    """Which of the two chats the user is not in. None if we couldn't tell.

    Any single lookup failing makes the whole answer unusable: not knowing about
    one chat means we can't distinguish "in both" from "needs a link", and
    guessing either way is worse than asking them to try again.
    """
    in_group = await _is_in(context, settings.GROUP_ID, user_id)
    in_channel = await _is_in(context, settings.CHANNEL_ID, user_id)
    if in_group is None or in_channel is None:
        return None
    missing = []
    if not in_group:
        missing.append(_GROUP)
    if not in_channel:
        missing.append(_CHANNEL)
    return missing


async def _invite_link(
    context: ContextTypes.DEFAULT_TYPE, what: str, user_id: int
) -> str | None:
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=_chat_id_for(what),
            name=f"Event {user_id}"[:32],
            member_limit=1,
        )
        return invite.invite_link
    except Exception:
        logger.exception(
            "Failed to create event invite link to the %s for user %d", what, user_id
        )
        return None


# ── The flow ────────────────────────────────────────────────────────────────────

async def _send_post(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Copy the admin-set post to the user. False if no post is configured.

    copy_message rather than forward: the post carries an image, and a copy
    arrives as the bot's own message with no "forwarded from" header pointing at
    wherever the admin happened to stage it.
    """
    post = await db.get_post()
    if post is None:
        logger.error(
            "Event post is not set, so nobody can register — reply to the post "
            "with /event_set_post."
        )
        return False
    from_chat_id, message_id = post
    try:
        await context.bot.copy_message(
            chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id
        )
        return True
    except Exception:
        logger.exception("Failed to copy the event post to chat %d", chat_id)
        return False


async def start_registration(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point: the menu button, /event, or the continue nudge's button."""
    user = update.effective_user
    message = update.effective_message
    if update.callback_query is not None:
        await update.callback_query.answer()

    if not settings.LIVE:
        await message.reply_text(msg.COMING_SOON, parse_mode="HTML")
        return ConversationHandler.END
    if not settings.configured():
        await message.reply_text(msg.NOT_CONFIGURED, parse_mode="HTML")
        return ConversationHandler.END

    existing = await db.get_user(user.id)
    if existing and existing["status"] == "registered":
        await message.reply_text(
            msg.ALREADY_REGISTERED.format(
                name=html.escape(existing["full_name"] or user.first_name or "there")
            ),
            parse_mode="HTML",
        )
        return ConversationHandler.END

    missing = await _missing_chats(context, user.id)
    if missing is None:
        await message.reply_text(msg.CHECK_UNAVAILABLE, parse_mode="HTML")
        return ConversationHandler.END

    # In neither: not an alum as far as we can tell, and no link is offered.
    if len(missing) == 2:
        logger.info(
            "Refused event registration for user %d (@%s): in neither chat",
            user.id, user.username,
        )
        await message.reply_text(msg.NOT_ALUMNI, parse_mode="HTML")
        return ConversationHandler.END

    if missing:
        return await _send_join_link(context, user, message.reply_text, missing[0])

    return await _ask_for_name(context, user, message.reply_text)


async def _send_join_link(context, user, reply, what: str) -> int:
    """Hand over a one-time link to the chat they're short of."""
    link = await _invite_link(context, what, user.id)
    if link is None:
        await reply(msg.LINK_FAILED, parse_mode="HTML")
        return ConversationHandler.END

    await db.mark_awaiting_join(user.id, user.username, user.first_name, what)
    await reply(
        msg.JOIN_THE_OTHER.format(what=what),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(msg.JOIN_BUTTON.format(what=what), url=link)
        ]]),
        disable_web_page_preview=True,
    )
    logger.info("Event: sent user %d a one-time link to the %s", user.id, what)
    return ConversationHandler.END


async def _ask_for_name(context, user, reply) -> int:
    """Eligible — show the post, then ask for the name."""
    if not await _send_post(context, user.id):
        await reply(msg.POST_MISSING, parse_mode="HTML")
        return ConversationHandler.END

    await db.mark_awaiting_name(user.id, user.username, user.first_name)
    await reply(msg.ASK_NAME, parse_mode="HTML")
    return ASK_NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(msg.NAME_REQUIRED, parse_mode="HTML")
        return ASK_NAME

    user = update.effective_user
    await db.mark_registered(user.id, user.username, user.first_name, name)
    await update.message.reply_text(
        msg.REGISTERED.format(name=html.escape(name)), parse_mode="HTML"
    )
    logger.info("Event: registered user %d (@%s) as %r", user.id, user.username, name)
    return ConversationHandler.END


# ── Picking people up when they join the chat they were missing ──────────────────

async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Someone's membership changed in the group or the channel.

    Only people mid-registration are of interest: without the awaiting_join
    check this would DM every new arrival in either chat, most of whom never
    asked about the event.
    """
    if not settings.active():
        return
    result = update.chat_member
    if result is None:
        return
    if result.chat.id not in (settings.GROUP_ID, settings.CHANNEL_ID):
        return

    user = result.new_chat_member.user
    if user.is_bot:
        return
    if result.new_chat_member.status not in _PRESENT_STATUSES:
        return

    row = await db.get_user(user.id)
    if row is None or row["status"] != "awaiting_join":
        return

    # Re-check both rather than trusting this one event: they may have joined the
    # chat they were already in, or left the other in the meantime.
    missing = await _missing_chats(context, user.id)
    if missing is None or missing:
        return

    # Nudge them back into the flow rather than continuing it here. The post and
    # the name question belong to start_registration, and asking for a name from
    # outside it would be a trap: the ConversationHandler would not be in its
    # ASK_NAME state, so a typed reply would land on no handler at all. The
    # button re-enters properly, and re-runs the checks while it's there.
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=msg.JOIN_DETECTED,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(msg.CONTINUE_BUTTON, callback_data=ENTER_CB)
            ]]),
        )
    except Exception:
        # A bot can't open a DM with someone who has never messaged it. They
        # reached us through the bot, so this should be rare — and they can
        # always tap the menu button again.
        logger.warning("Couldn't DM user %d after they joined", user.id)
        return

    logger.info("Event: user %d is now in both chats, nudged to continue", user.id)


# ── Admin ───────────────────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def set_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to the event post with /event_set_post to save it."""
    if not _is_admin(update.effective_user.id):
        return
    reply = update.message.reply_to_message
    if reply is None:
        await update.message.reply_text(msg.SET_POST_USAGE, parse_mode="HTML")
        return
    await db.set_post(reply.chat.id, reply.message_id)
    await update.message.reply_text(msg.SET_POST_SAVED, parse_mode="HTML")
    logger.info(
        "Event post set to chat %d message %d", reply.chat.id, reply.message_id
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    rows = await db.registered()
    if not rows:
        await update.message.reply_text(msg.LIST_EMPTY, parse_mode="HTML")
        return
    lines = [msg.LIST_HEADER.format(count=len(rows))]
    for idx, row in enumerate(rows, start=1):
        username_part = f" (@{row['username']})" if row["username"] else ""
        lines.append(msg.LIST_ENTRY.format(
            idx=idx,
            full_name=html.escape(row["full_name"] or "—"),
            username_part=html.escape(username_part),
        ))
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    counts = await db.counts()
    await update.message.reply_text(
        msg.STATS.format(
            registered=counts["registered"],
            awaiting_join=counts["awaiting_join"],
            awaiting_name=counts["awaiting_name"],
        ),
        parse_mode="HTML",
    )


# ── Wiring ──────────────────────────────────────────────────────────────────────

def register(app: Application) -> None:
    """Add the event's handlers. Call before gate.register(), which must be last."""
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("event", start_registration, filters=_private),
            MessageHandler(
                _private & filters.Text([msg.MENU_BUTTON]), start_registration
            ),
            CallbackQueryHandler(start_registration, pattern=f"^{ENTER_CB}$"),
        ],
        states={
            ASK_NAME: [
                MessageHandler(_private & filters.TEXT & ~filters.COMMAND, got_name)
            ],
        },
        fallbacks=[],
        per_message=False,
    ))

    app.add_handler(CommandHandler("event_set_post", set_post_command, filters=_private))
    app.add_handler(CommandHandler("event_list", list_command, filters=_private))
    app.add_handler(CommandHandler("event_stats", stats_command, filters=_private))

    # group=2 because the gate already claims CHAT_MEMBER in group 0 and
    # MY_CHAT_MEMBER in group 1, and only the first match within a handler group
    # runs. A separate group lets both features see the same update.
    app.add_handler(
        ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER), group=2
    )

    if not settings.active():
        logger.info("Event flow is dormant (EVENT_LIVE off or the chat IDs unset).")
