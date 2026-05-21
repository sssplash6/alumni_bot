# bot.py
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import messages as msg
from config import ADMIN_IDS, BOT_TOKEN
from matcher import run_matching

logger = logging.getLogger(__name__)

# ── State constants ────────────────────────────────────────────────────────────

(
    MENTOR_NAME,
    MENTOR_SPHERE,
    MENTOR_EXP,
    MENTOR_TIME,
    MENTOR_MENTEE_PREF,
    MENTOR_EXTRA,
    MENTOR_CONFIRM,
) = range(7)

(
    MENTEE_NAME,
    MENTEE_SPHERE,
    MENTEE_EXP,
    MENTEE_MENTOR_PREF,
    MENTEE_EXTRA,
    MENTEE_TIME,
    MENTEE_CONSENT,
    MENTEE_CONFIRM,
) = range(7, 15)


# ── Keyboard helpers ──────────────────────────────────────────────────────────

def _checkbox_kb(options: list[str], selected: set[str], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if opt in selected else '☑️'} {opt}",
            callback_data=f"toggle:{prefix}:{opt}",
        )]
        for opt in options
    ]
    rows.append([InlineKeyboardButton("Done ✓", callback_data=f"done:{prefix}")])
    return InlineKeyboardMarkup(rows)


def _radio_kb(options: list[str], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(opt, callback_data=f"select:{prefix}:{opt}")]
        for opt in options
    ])


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm:yes"),
        InlineKeyboardButton("✏️ Start over", callback_data="confirm:no"),
    ]])


def _consent_kb(agreed: bool) -> InlineKeyboardMarkup:
    mark = "✅" if agreed else "☑️"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{mark} I agree to be matched and contacted by my mentor",
            callback_data="consent:toggle",
        )],
        [InlineKeyboardButton("Continue →", callback_data="consent:done")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_open = await db.is_applications_open()
    if is_open:
        await update.message.reply_text(
            msg.START_OPEN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Register as Mentor", callback_data="start:mentor"),
                InlineKeyboardButton("🙋 Register as Mentee", callback_data="start:mentee"),
            ]]),
        )
    else:
        await update.message.reply_text(msg.START_CLOSED)


# ── Mentor flow ───────────────────────────────────────────────────────────────

async def mentor_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentor(chat_id):
        await update.message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(msg.WELCOME_MENTOR)
    return MENTOR_NAME


async def mentor_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(msg.WELCOME_MENTOR)
        return MENTOR_NAME
    context.user_data["full_name"] = name
    context.user_data["spheres"] = set()
    await update.message.reply_text(
        msg.ASK_SPHERE,
        reply_markup=_checkbox_kb(msg.SPHERES, set(), "msphere"),
    )
    return MENTOR_SPHERE


async def mentor_sphere_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sphere = query.data.split(":", 2)[2]
    selected: set[str] = context.user_data.setdefault("spheres", set())
    selected.symmetric_difference_update({sphere})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.SPHERES, selected, "msphere")
    )
    return MENTOR_SPHERE


async def mentor_sphere_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("spheres"):
        await query.answer("Please select at least one sphere.", show_alert=True)
        return MENTOR_SPHERE
    await query.answer()
    await query.edit_message_text(
        msg.ASK_MENTOR_EXP,
        reply_markup=_radio_kb(msg.MENTOR_EXP_LEVELS, "mexp"),
    )
    return MENTOR_EXP


async def mentor_got_exp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["exp_level"] = query.data.split(":", 2)[2]
    await query.edit_message_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "mtime"),
    )
    return MENTOR_TIME


async def mentor_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["devote_time"] = query.data.split(":", 2)[2]
    context.user_data["mentee_exp_prefs"] = set()
    await query.edit_message_text(
        msg.ASK_MENTEE_PREFS,
        reply_markup=_checkbox_kb(msg.MENTEE_EXP_LEVELS, set(), "mmenteeexp"),
    )
    return MENTOR_MENTEE_PREF


async def mentor_mentee_pref_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    level = query.data.split(":", 2)[2]
    selected: set[str] = context.user_data.setdefault("mentee_exp_prefs", set())
    selected.symmetric_difference_update({level})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.MENTEE_EXP_LEVELS, selected, "mmenteeexp")
    )
    return MENTOR_MENTEE_PREF


async def mentor_mentee_pref_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("mentee_exp_prefs"):
        await query.answer("Please select at least one preference.", show_alert=True)
        return MENTOR_MENTEE_PREF
    await query.answer()
    await query.edit_message_text(
        msg.ASK_EXTRA_MENTOR,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Skip", callback_data="extra:skip")]]
        ),
    )
    return MENTOR_EXTRA


async def mentor_got_extra_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["extra"] = update.message.text.strip()
    data = _serialize_sets(context.user_data)
    await update.message.reply_text(msg.mentor_summary(data), reply_markup=_confirm_kb())
    return MENTOR_CONFIRM


async def mentor_extra_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["extra"] = None
    data = _serialize_sets(context.user_data)
    await query.edit_message_text(msg.mentor_summary(data), reply_markup=_confirm_kb())
    return MENTOR_CONFIRM


async def mentor_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text(msg.REGISTRATION_CANCELLED)
        return ConversationHandler.END
    data = context.user_data
    try:
        await db.save_mentor(
            chat_id=update.effective_chat.id,
            full_name=data["full_name"],
            spheres=list(data["spheres"]),
            exp_level=data["exp_level"],
            devote_time=data["devote_time"],
            mentee_exp_prefs=list(data["mentee_exp_prefs"]),
            extra=data.get("extra"),
        )
    except Exception:
        logger.exception("Failed to save mentor %d", update.effective_chat.id)
        await query.edit_message_text(msg.SAVE_ERROR)
        return ConversationHandler.END
    await query.edit_message_text(msg.REGISTRATION_SAVED)
    return ConversationHandler.END


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _serialize_sets(data: dict) -> dict:
    """Convert set values to sorted lists so summary formatters can join them."""
    return {
        k: sorted(v) if isinstance(v, set) else v
        for k, v in data.items()
    }


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(msg.REGISTRATION_CANCELLED)
    return ConversationHandler.END


# ── Mentee flow ───────────────────────────────────────────────────────────────

async def mentee_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentee(chat_id):
        await update.message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(msg.WELCOME_MENTEE)
    return MENTEE_NAME


async def mentee_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(msg.WELCOME_MENTEE)
        return MENTEE_NAME
    context.user_data["full_name"] = name
    context.user_data["spheres"] = set()
    await update.message.reply_text(
        msg.ASK_SPHERE,
        reply_markup=_checkbox_kb(msg.SPHERES, set(), "tsphere"),
    )
    return MENTEE_SPHERE


async def mentee_sphere_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sphere = query.data.split(":", 2)[2]
    selected: set[str] = context.user_data.setdefault("spheres", set())
    selected.symmetric_difference_update({sphere})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.SPHERES, selected, "tsphere")
    )
    return MENTEE_SPHERE


async def mentee_sphere_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("spheres"):
        await query.answer("Please select at least one sphere.", show_alert=True)
        return MENTEE_SPHERE
    await query.answer()
    await query.edit_message_text(
        msg.ASK_MENTEE_EXP,
        reply_markup=_radio_kb(msg.MENTEE_EXP_LEVELS, "texp"),
    )
    return MENTEE_EXP


async def mentee_got_exp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["exp_level"] = query.data.split(":", 2)[2]
    context.user_data["mentor_exp_prefs"] = set()
    await query.edit_message_text(
        msg.ASK_MENTOR_PREFS,
        reply_markup=_checkbox_kb(msg.MENTOR_EXP_LEVELS, set(), "tmentorexp"),
    )
    return MENTEE_MENTOR_PREF


async def mentee_mentor_pref_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    level = query.data.split(":", 2)[2]
    selected: set[str] = context.user_data.setdefault("mentor_exp_prefs", set())
    selected.symmetric_difference_update({level})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.MENTOR_EXP_LEVELS, selected, "tmentorexp")
    )
    return MENTEE_MENTOR_PREF


async def mentee_mentor_pref_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("mentor_exp_prefs"):
        await query.answer("Please select at least one preference.", show_alert=True)
        return MENTEE_MENTOR_PREF
    await query.answer()
    await query.edit_message_text(
        msg.ASK_EXTRA_MENTEE,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Skip", callback_data="extra:skip")]]
        ),
    )
    return MENTEE_EXTRA


async def mentee_got_extra_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["extra"] = update.message.text.strip()
    await update.message.reply_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "ttime"),
    )
    return MENTEE_TIME


async def mentee_extra_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["extra"] = None
    await query.edit_message_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "ttime"),
    )
    return MENTEE_TIME


async def mentee_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["devote_time"] = query.data.split(":", 2)[2]
    context.user_data["consent"] = False
    await query.edit_message_text(
        msg.ASK_CONSENT,
        reply_markup=_consent_kb(False),
    )
    return MENTEE_CONSENT


async def mentee_consent_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["consent"] = not context.user_data.get("consent", False)
    await query.edit_message_reply_markup(
        reply_markup=_consent_kb(context.user_data["consent"])
    )
    return MENTEE_CONSENT


async def mentee_consent_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("consent"):
        await query.answer(msg.CONSENT_REQUIRED, show_alert=True)
        return MENTEE_CONSENT
    await query.answer()
    data = _serialize_sets(context.user_data)
    await query.edit_message_text(msg.mentee_summary(data), reply_markup=_confirm_kb())
    return MENTEE_CONFIRM


async def mentee_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text(msg.REGISTRATION_CANCELLED)
        return ConversationHandler.END
    data = context.user_data
    try:
        await db.save_mentee(
            chat_id=update.effective_chat.id,
            full_name=data["full_name"],
            spheres=list(data["spheres"]),
            exp_level=data["exp_level"],
            mentor_exp_prefs=list(data["mentor_exp_prefs"]),
            extra=data.get("extra"),
            devote_time=data["devote_time"],
            consent=data["consent"],
        )
    except Exception:
        logger.exception("Failed to save mentee %d", update.effective_chat.id)
        await query.edit_message_text(msg.SAVE_ERROR)
        return ConversationHandler.END
    await query.edit_message_text(msg.REGISTRATION_SAVED)
    return ConversationHandler.END


# ── Admin commands ─────────────────────────────────────────────────────────────

async def admin_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    await db.set_applications_open(True)
    await update.message.reply_text(msg.APPS_OPENED)


async def admin_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    await db.set_applications_open(False)
    await update.message.reply_text(msg.APPS_CLOSED_ADMIN)


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    mentor_count, mentee_count, match_count = await db.get_registration_counts()
    mentor_pending, mentee_pending = await db.get_pending_counts()
    is_open = await db.is_applications_open()
    await update.message.reply_text(
        msg.status_text(mentor_count, mentee_count, match_count, is_open, mentor_pending, mentee_pending)
    )


async def admin_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    if await db.is_applications_open():
        await update.message.reply_text(msg.MATCH_BLOCKED_OPEN)
        return

    _, _, existing_matches = await db.get_registration_counts()
    if existing_matches > 0:
        await update.message.reply_text(msg.MATCH_ALREADY_RAN)
        return

    mentors = await db.get_all_mentors()
    mentees = await db.get_all_mentees()
    if not mentors or not mentees:
        await update.message.reply_text(msg.MATCH_BLOCKED_EMPTY)
        return

    matches = run_matching(mentors, mentees)
    await db.save_matches(matches)

    mentor_by_id = {m["chat_id"]: m for m in mentors}
    mentee_by_id = {m["chat_id"]: m for m in mentees}
    matched_mentor_ids = {m[0] for m in matches}
    matched_mentee_ids = {m[1] for m in matches}

    for mentor_id, mentee_id, _ in matches:
        mentee = mentee_by_id[mentee_id]
        mentor = mentor_by_id[mentor_id]
        try:
            await context.bot.send_message(
                chat_id=mentor_id,
                text=msg.mentor_match_text(mentee),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Message your mentee",
                        url=f"tg://user?id={mentee_id}",
                    )
                ]]),
            )
        except Exception:
            logger.exception("Failed to notify mentor %d", mentor_id)
        try:
            await context.bot.send_message(
                chat_id=mentee_id,
                text=msg.mentee_match_text(mentor),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Message your mentor",
                        url=f"tg://user?id={mentor_id}",
                    )
                ]]),
            )
        except Exception:
            logger.exception("Failed to notify mentee %d", mentee_id)

    for mentor in mentors:
        if mentor["chat_id"] not in matched_mentor_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentor["chat_id"], text=msg.NO_MATCH_MENTOR
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentor %d", mentor["chat_id"])

    for mentee in mentees:
        if mentee["chat_id"] not in matched_mentee_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentee["chat_id"], text=msg.NO_MATCH_MENTEE
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentee %d", mentee["chat_id"])

    await update.message.reply_text(
        msg.MATCH_DONE.format(
            matched=len(matches),
            unmatched_mentors=len(mentors) - len(matched_mentor_ids),
            unmatched_mentees=len(mentees) - len(matched_mentee_ids),
        )
    )


# ── App builder ────────────────────────────────────────────────────────────────

def build_app() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    _private = filters.ChatType.PRIVATE

    mentor_conv = ConversationHandler(
        entry_points=[
            CommandHandler("mentor", mentor_start, filters=_private),
            CallbackQueryHandler(mentor_start, pattern=r"^start:mentor$"),
        ],
        states={
            MENTOR_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentor_got_name)],
            MENTOR_SPHERE: [
                CallbackQueryHandler(mentor_sphere_toggle, pattern=r"^toggle:msphere:"),
                CallbackQueryHandler(mentor_sphere_done, pattern=r"^done:msphere$"),
            ],
            MENTOR_EXP: [CallbackQueryHandler(mentor_got_exp, pattern=r"^select:mexp:")],
            MENTOR_TIME: [CallbackQueryHandler(mentor_got_time, pattern=r"^select:mtime:")],
            MENTOR_MENTEE_PREF: [
                CallbackQueryHandler(mentor_mentee_pref_toggle, pattern=r"^toggle:mmenteeexp:"),
                CallbackQueryHandler(mentor_mentee_pref_done, pattern=r"^done:mmenteeexp$"),
            ],
            MENTOR_EXTRA: [
                MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentor_got_extra_text),
                CallbackQueryHandler(mentor_extra_skip, pattern=r"^extra:skip$"),
            ],
            MENTOR_CONFIRM: [CallbackQueryHandler(mentor_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    mentee_conv = ConversationHandler(
        entry_points=[
            CommandHandler("mentee", mentee_start, filters=_private),
            CallbackQueryHandler(mentee_start, pattern=r"^start:mentee$"),
        ],
        states={
            MENTEE_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentee_got_name)],
            MENTEE_SPHERE: [
                CallbackQueryHandler(mentee_sphere_toggle, pattern=r"^toggle:tsphere:"),
                CallbackQueryHandler(mentee_sphere_done, pattern=r"^done:tsphere$"),
            ],
            MENTEE_EXP: [CallbackQueryHandler(mentee_got_exp, pattern=r"^select:texp:")],
            MENTEE_MENTOR_PREF: [
                CallbackQueryHandler(mentee_mentor_pref_toggle, pattern=r"^toggle:tmentorexp:"),
                CallbackQueryHandler(mentee_mentor_pref_done, pattern=r"^done:tmentorexp$"),
            ],
            MENTEE_EXTRA: [
                MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentee_got_extra_text),
                CallbackQueryHandler(mentee_extra_skip, pattern=r"^extra:skip$"),
            ],
            MENTEE_TIME: [CallbackQueryHandler(mentee_got_time, pattern=r"^select:ttime:")],
            MENTEE_CONSENT: [
                CallbackQueryHandler(mentee_consent_toggle, pattern=r"^consent:toggle$"),
                CallbackQueryHandler(mentee_consent_done, pattern=r"^consent:done$"),
            ],
            MENTEE_CONFIRM: [CallbackQueryHandler(mentee_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start, filters=_private))
    app.add_handler(mentor_conv)
    app.add_handler(mentee_conv)
    app.add_handler(CommandHandler("open", admin_open, filters=_private))
    app.add_handler(CommandHandler("close", admin_close, filters=_private))
    app.add_handler(CommandHandler("status", admin_status, filters=_private))
    app.add_handler(CommandHandler("match", admin_match, filters=_private))

    return app
