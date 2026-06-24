# bot.py
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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

# Hard-fixed reviewers who receive Admissions Program Fair registrations and
# can approve/reject them. Carried over from the freshbot.
APF_REVIEWER_CHAT_IDS: list[int] = [7185151344]

# Groups the bot belongs to as a member. Elysium Fair registrants are
# auto-approved if they belong to at least one of these, else auto-rejected.
ELIGIBILITY_GROUP_IDS: list[int] = [-1001308713514, -1004219790871]

# Telegram chat-member statuses that count as "currently in the group".
_PRESENT_STATUSES = {"creator", "administrator", "member", "restricted"}

# People who manually confirm "Join Elysium pre-2025" requests.
ELYSIUM_CONFIRMER_CHAT_IDS: list[int] = [8836861446]

# Link handed to applicants after they register. Confirmation/approval happens
# inside the group (e.g. via join requests approved by group admins).
ELYSIUM_GROUP_INVITE_LINK = "https://t.me/+L_mi3g_VSlk5ZGQ9"

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

(
    APF_NAME,
    APF_COHORT,
) = range(15, 17)

(
    ELYSIUM_NAME,
    ELYSIUM_COHORT,
) = range(17, 19)


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

def _main_kb(is_open: bool) -> ReplyKeyboardMarkup:
    if is_open:
        rows = [
            [KeyboardButton(msg.BTN_MENTOR), KeyboardButton(msg.BTN_MENTEE)],
            [KeyboardButton(msg.BTN_ELYSIUM)],
        ]
    else:
        rows = [[KeyboardButton(msg.BTN_ELYSIUM)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_open = await db.is_applications_open()
    await update.message.reply_text(
        msg.START_OPEN if is_open else msg.START_CLOSED,
        reply_markup=_main_kb(is_open),
    )


# ── Mentor flow ───────────────────────────────────────────────────────────────

async def mentor_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.effective_message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentor(chat_id):
        await update.effective_message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text(msg.WELCOME_MENTOR)
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
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.effective_message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentee(chat_id):
        await update.effective_message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text(msg.WELCOME_MENTEE)
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

# ── Review ────────────────────────────────────────────────────────────────────

def _review_kb(role: str, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"review:approve:{role}:{chat_id}"),
        InlineKeyboardButton("❌ Deny", callback_data=f"review:deny:{role}:{chat_id}"),
    ]])


async def admin_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    if await db.is_applications_open():
        await update.message.reply_text(msg.REVIEW_BLOCKED_OPEN)
        return
    pending_mentors = await db.get_pending_mentors()
    pending_mentees = await db.get_pending_mentees()
    if not pending_mentors and not pending_mentees:
        await update.message.reply_text(msg.REVIEW_NO_PENDING)
        return
    if pending_mentors:
        mentor = pending_mentors[0]
        await update.message.reply_text(
            msg.mentor_review_card(mentor, len(pending_mentors)),
            reply_markup=_review_kb("mentor", mentor["chat_id"]),
        )
    else:
        mentee = pending_mentees[0]
        await update.message.reply_text(
            msg.mentee_review_card(mentee, len(pending_mentees)),
            reply_markup=_review_kb("mentee", mentee["chat_id"]),
        )


async def review_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return
    parts = query.data.split(":")
    if len(parts) != 4:
        logger.warning("review_decision received unexpected callback data: %r", query.data)
        await query.edit_message_text(msg.REVIEW_ERROR)
        return
    _, action, role, chat_id_str = parts
    chat_id = int(chat_id_str)
    status = "approved" if action == "approve" else "denied"
    try:
        if role == "mentor":
            await db.set_mentor_status(chat_id, status)
        else:
            await db.set_mentee_status(chat_id, status)
    except Exception:
        logger.exception("Failed to set %s status for %s %d", status, role, chat_id)
        await query.edit_message_text(msg.REVIEW_ERROR)
        return
    pending_mentors = await db.get_pending_mentors()
    pending_mentees = await db.get_pending_mentees()
    if pending_mentors:
        mentor = pending_mentors[0]
        await query.edit_message_text(
            msg.mentor_review_card(mentor, len(pending_mentors)),
            reply_markup=_review_kb("mentor", mentor["chat_id"]),
        )
    elif pending_mentees:
        mentee = pending_mentees[0]
        await query.edit_message_text(
            msg.mentee_review_card(mentee, len(pending_mentees)),
            reply_markup=_review_kb("mentee", mentee["chat_id"]),
        )
    else:
        summary = await db.get_review_summary()
        await query.edit_message_text(msg.review_complete_text(summary))


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

    approved_mentors = await db.get_approved_mentors()
    approved_mentees = await db.get_approved_mentees()
    if not approved_mentors or not approved_mentees:
        await update.message.reply_text(msg.MATCH_BLOCKED_EMPTY)
        return

    matches = run_matching(approved_mentors, approved_mentees)
    await db.save_matches(matches)

    mentor_by_id = {m["chat_id"]: m for m in approved_mentors}
    mentee_by_id = {m["chat_id"]: m for m in approved_mentees}
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

    # Notify unmatched: approved-but-unmatched + denied (not pending)
    all_mentors = await db.get_all_mentors()
    all_mentees = await db.get_all_mentees()

    for mentor in all_mentors:
        if mentor["status"] == "pending":
            continue
        if mentor["chat_id"] not in matched_mentor_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentor["chat_id"], text=msg.NO_MATCH_MENTOR
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentor %d", mentor["chat_id"])

    for mentee in all_mentees:
        if mentee["status"] == "pending":
            continue
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
            unmatched_mentors=len(approved_mentors) - len(matched_mentor_ids),
            unmatched_mentees=len(approved_mentees) - len(matched_mentee_ids),
        )
    )


# ── Admissions Program Fair ─────────────────────────────────────────────────────

async def _is_eligibility_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if the user currently belongs to at least one eligibility group."""
    for group_id in ELIGIBILITY_GROUP_IDS:
        try:
            member = await context.bot.get_chat_member(chat_id=group_id, user_id=user_id)
        except Exception:
            logger.exception(
                "Failed to check membership of user %d in group %d", user_id, group_id
            )
            continue
        if member.status in _PRESENT_STATUSES:
            return True
    return False


async def apf_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = update.effective_chat.id

    existing = await db.apf_get_submission(chat_id)
    if existing and existing["status"] == "approved":
        await update.effective_message.reply_text(msg.APF_ALREADY_APPROVED)
        return ConversationHandler.END
    if existing and existing["status"] == "pending":
        await update.effective_message.reply_text(msg.APF_ALREADY_PENDING)
        return ConversationHandler.END

    # Forward the configured fair post first, then start the registration questions.
    post_chat_id = await db.get_setting("apf_post_chat_id")
    post_message_id = await db.get_setting("apf_post_message_id")
    if post_chat_id and post_message_id:
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=int(post_chat_id),
                message_id=int(post_message_id),
            )
        except Exception:
            logger.exception("Failed to forward APF post to chat_id=%d", chat_id)
            await update.effective_message.reply_text(msg.APF_INTRO, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(msg.APF_INTRO, parse_mode="HTML")

    context.user_data.clear()
    await update.effective_message.reply_text(msg.APF_ASK_NAME)
    return APF_NAME


async def apf_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(msg.APF_NAME_REQUIRED)
        return APF_NAME
    context.user_data["apf_full_name"] = name
    await update.message.reply_text(msg.APF_ASK_COHORT)
    return APF_COHORT


async def apf_got_cohort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cohort = update.message.text.strip()
    if not cohort:
        await update.message.reply_text(msg.APF_COHORT_REQUIRED)
        return APF_COHORT

    chat_id = update.effective_chat.id
    full_name = context.user_data.get("apf_full_name", "")
    user = update.effective_user
    first_name = user.first_name or "Unknown"
    username = user.username

    await db.apf_save_submission(chat_id, username, first_name, full_name, cohort)

    # Auto-decide based on membership in the eligibility groups: a member of at
    # least one group is approved, everyone else is rejected. No manual review.
    is_member = await _is_eligibility_group_member(context, chat_id)
    if is_member:
        await db.apf_set_status(chat_id, "approved")
        await update.message.reply_text(msg.APF_APPROVED)
        reviewer_status = msg.APF_REVIEWER_AUTO_APPROVED
    else:
        await db.apf_set_status(chat_id, "rejected")
        await update.message.reply_text(msg.APF_REJECTED)
        reviewer_status = msg.APF_REVIEWER_AUTO_REJECTED

    username_part = f" (@{username})" if username else ""
    reviewer_text = msg.APF_REVIEWER_ENTRY.format(
        chat_id=chat_id,
        first_name=first_name,
        username_part=username_part,
        full_name=full_name,
        cohort=cohort,
    )
    reviewer_text = f"{reviewer_text}\n\n{reviewer_status}"
    for reviewer_id in APF_REVIEWER_CHAT_IDS:
        try:
            await context.bot.send_message(
                chat_id=reviewer_id,
                text=reviewer_text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send APF registration to reviewer chat_id=%d", reviewer_id)

    return ConversationHandler.END


async def _apf_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, decision: str) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in APF_REVIEWER_CHAT_IDS:
        return

    applicant_chat_id = int(query.data.split(":")[1])
    submission = await db.apf_get_submission(applicant_chat_id)
    if not submission or submission["status"] != "pending":
        await query.answer(msg.APF_REVIEWER_ALREADY_DECIDED, show_alert=True)
        return

    base_text = query.message.text or ""
    if decision == "approved":
        await db.apf_set_status(applicant_chat_id, "approved")
        applicant_msg = msg.APF_APPROVED
        reviewer_confirmation = msg.APF_REVIEWER_APPROVED
    else:
        await db.apf_set_status(applicant_chat_id, "rejected")
        applicant_msg = msg.APF_REJECTED
        reviewer_confirmation = msg.APF_REVIEWER_REJECTED

    try:
        await context.bot.send_message(chat_id=applicant_chat_id, text=applicant_msg)
    except Exception:
        logger.exception("Failed to notify APF applicant chat_id=%d", applicant_chat_id)

    await query.edit_message_text(
        text=f"{base_text}\n\n{reviewer_confirmation}", reply_markup=None
    )


async def apf_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _apf_decision(update, context, "approved")


async def apf_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _apf_decision(update, context, "rejected")


async def apf_set_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in APF_REVIEWER_CHAT_IDS:
        return
    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(msg.APF_SET_POST_USAGE)
        return
    await db.set_setting("apf_post_chat_id", str(reply.chat.id))
    await db.set_setting("apf_post_message_id", str(reply.message_id))
    await update.message.reply_text(msg.APF_SET_POST_SUCCESS)


async def apf_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in APF_REVIEWER_CHAT_IDS:
        return
    approved = await db.apf_get_by_status(["approved"])
    if not approved:
        await update.message.reply_text(msg.APF_LIST_EMPTY)
        return
    lines = [msg.APF_LIST_HEADER.format(count=len(approved))]
    for idx, sub in enumerate(approved, start=1):
        username_part = f" (@{sub['username']})" if sub.get("username") else ""
        lines.append(msg.APF_LIST_ENTRY.format(
            idx=idx,
            full_name=sub["full_name"],
            cohort=sub["cohort"],
            username_part=username_part,
        ))
    await update.message.reply_text("\n".join(lines))


# ── Elysium pre-2025 ────────────────────────────────────────────────────────────

async def elysium_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = update.effective_chat.id

    existing = await db.elysium_get_submission(chat_id)
    if existing and existing["status"] == "approved":
        # Already registered — just resend their link.
        await update.effective_message.reply_text(
            msg.ELYSIUM_ALREADY_APPROVED.format(invite_link=ELYSIUM_GROUP_INVITE_LINK),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    # Forward the configured post first, then start the registration questions.
    post_chat_id = await db.get_setting("elysium_post_chat_id")
    post_message_id = await db.get_setting("elysium_post_message_id")
    if post_chat_id and post_message_id:
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=int(post_chat_id),
                message_id=int(post_message_id),
            )
        except Exception:
            logger.exception("Failed to forward Elysium post to chat_id=%d", chat_id)
            await update.effective_message.reply_text(msg.ELYSIUM_INTRO, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(msg.ELYSIUM_INTRO, parse_mode="HTML")

    context.user_data.clear()
    await update.effective_message.reply_text(msg.ELYSIUM_ASK_NAME)
    return ELYSIUM_NAME


async def elysium_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text(msg.ELYSIUM_NAME_REQUIRED)
        return ELYSIUM_NAME
    context.user_data["elysium_full_name"] = name
    await update.message.reply_text(msg.ELYSIUM_ASK_COHORT)
    return ELYSIUM_COHORT


async def elysium_got_cohort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cohort = update.message.text.strip()
    if not cohort:
        await update.message.reply_text(msg.ELYSIUM_COHORT_REQUIRED)
        return ELYSIUM_COHORT

    chat_id = update.effective_chat.id
    full_name = context.user_data.get("elysium_full_name", "")
    user = update.effective_user
    first_name = user.first_name or "Unknown"
    username = user.username

    await db.elysium_save_submission(chat_id, username, first_name, full_name, cohort)
    await db.elysium_set_status(chat_id, "approved")
    await update.message.reply_text(
        msg.ELYSIUM_APPROVED.format(invite_link=ELYSIUM_GROUP_INVITE_LINK),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    return ConversationHandler.END


async def elysium_set_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ELYSIUM_CONFIRMER_CHAT_IDS + ADMIN_IDS:
        return
    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(msg.ELYSIUM_SET_POST_USAGE)
        return
    await db.set_setting("elysium_post_chat_id", str(reply.chat.id))
    await db.set_setting("elysium_post_message_id", str(reply.message_id))
    await update.message.reply_text(msg.ELYSIUM_SET_POST_SUCCESS)


async def elysium_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ELYSIUM_CONFIRMER_CHAT_IDS + ADMIN_IDS:
        return
    submissions = await db.elysium_get_all()
    if not submissions:
        await update.message.reply_text(msg.ELYSIUM_LIST_EMPTY)
        return
    lines = [msg.ELYSIUM_LIST_HEADER.format(count=len(submissions))]
    for idx, sub in enumerate(submissions, start=1):
        username_part = f" (@{sub['username']})" if sub.get("username") else ""
        lines.append(msg.ELYSIUM_LIST_ENTRY.format(
            idx=idx,
            full_name=sub["full_name"],
            cohort=sub["cohort"],
            username_part=username_part,
        ))
    await update.message.reply_text("\n".join(lines))


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
            MessageHandler(_private & filters.Text([msg.BTN_MENTOR]), mentor_start),
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
            MessageHandler(_private & filters.Text([msg.BTN_MENTEE]), mentee_start),
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

    apf_conv = ConversationHandler(
        entry_points=[
            CommandHandler("fair", apf_start, filters=_private),
            MessageHandler(_private & filters.Text([msg.BTN_APF]), apf_start),
            CallbackQueryHandler(apf_start, pattern=r"^start:fair$"),
        ],
        states={
            APF_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, apf_got_name)],
            APF_COHORT: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, apf_got_cohort)],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    elysium_conv = ConversationHandler(
        entry_points=[
            CommandHandler("elysium", elysium_start, filters=_private),
            MessageHandler(_private & filters.Text([msg.BTN_ELYSIUM]), elysium_start),
            CallbackQueryHandler(elysium_start, pattern=r"^start:elysium$"),
        ],
        states={
            ELYSIUM_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, elysium_got_name)],
            ELYSIUM_COHORT: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, elysium_got_cohort)],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start, filters=_private))
    app.add_handler(mentor_conv)
    app.add_handler(mentee_conv)
    app.add_handler(apf_conv)
    app.add_handler(elysium_conv)
    app.add_handler(CommandHandler("apf_set_post", apf_set_post, filters=_private))
    app.add_handler(CommandHandler("apf_list", apf_list, filters=_private))
    app.add_handler(CallbackQueryHandler(apf_approve_callback, pattern=r"^apf_approve:"))
    app.add_handler(CallbackQueryHandler(apf_reject_callback, pattern=r"^apf_reject:"))
    app.add_handler(CommandHandler("elysium_set_post", elysium_set_post, filters=_private))
    app.add_handler(CommandHandler("elysium_list", elysium_list, filters=_private))
    app.add_handler(CommandHandler("open", admin_open, filters=_private))
    app.add_handler(CommandHandler("close", admin_close, filters=_private))
    app.add_handler(CommandHandler("status", admin_status, filters=_private))
    app.add_handler(CommandHandler("match", admin_match, filters=_private))
    app.add_handler(CommandHandler("review", admin_review, filters=_private))
    app.add_handler(CallbackQueryHandler(review_decision, pattern=r"^review:"))

    return app
