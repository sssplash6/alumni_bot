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
from config import ADMIN_ID, BOT_TOKEN
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
    await update.message.reply_text(msg.START_TEXT)


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
