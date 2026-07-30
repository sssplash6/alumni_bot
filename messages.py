# messages.py

SPHERES = [
    "Technology",
    "Business/Finance",
    "Medicine",
    "Law",
    "Science/Research",
    "Arts/Design",
    "Education",
    "Other",
]

MENTOR_EXP_LEVELS = ["0–2 yrs", "3–5 yrs", "6–10 yrs", "10+ yrs"]

MENTEE_EXP_LEVELS = [
    "New to field",
    "HS Graduate",
    "College Student",
    "University Graduate",
]

DEVOTE_TIME_OPTIONS = ["1–2 hrs/week", "3–5 hrs/week", "5+ hrs/week"]

# ── Start landing ─────────────────────────────────────────────────────────────

START_OPEN = "🎓 Welcome to the Alumni System of Freshman Academy."
START_CLOSED = "🎓 Welcome to the Alumni System of Freshman Academy."

# ── General ──────────────────────────────────────────────────────────────────

APPS_CLOSED = "Applications are currently closed. Stay tuned for the next round!"
ALREADY_REGISTERED = (
    "You're already registered. If you need to make changes, please contact the admin."
)
REGISTRATION_CANCELLED = (
    "Registration cancelled. You can start again with /mentor or /mentee."
)
REGISTRATION_SAVED = (
    "You're registered! We'll notify you when matches are announced. Good luck!"
)
CONSENT_REQUIRED = "You must agree to the terms to complete registration."
SAVE_ERROR = "Something went wrong saving your registration. Please try again later."

# ── Review ────────────────────────────────────────────────────────────────────

REVIEW_BLOCKED_OPEN = "Close applications first with /close before reviewing."
REVIEW_NO_PENDING = "No pending applications to review."
REVIEW_ERROR = "Something went wrong. Please try /review again."

# ── Mentor form ───────────────────────────────────────────────────────────────

WELCOME_MENTOR = (
    "Welcome to mentor registration! This takes about 2 minutes.\n\n"
    "First — what's your full name?"
)
ASK_SPHERE = "Which sphere(s) are you in? Select all that apply, then tap Done ✓"
ASK_MENTOR_EXP = "What is your experience level?"
ASK_DEVOTE_TIME = "How much time can you devote per week?"
ASK_MENTEE_PREFS = (
    "What experience level(s) are you open to mentoring?\n"
    "Select all that apply, then tap Done ✓"
)
ASK_EXTRA_MENTOR = (
    "Anything extra you'd like your mentee to know? "
    "(Optional — tap Skip to skip)"
)

# ── Mentee form ───────────────────────────────────────────────────────────────

WELCOME_MENTEE = (
    "Welcome to mentee registration! This takes about 2 minutes.\n\n"
    "First — what's your full name?"
)
ASK_MENTEE_EXP = "What is your current experience level?"
ASK_MENTOR_PREFS = (
    "What experience level(s) would you prefer in a mentor?\n"
    "Select all that apply, then tap Done ✓"
)
ASK_EXTRA_MENTEE = (
    "Anything you'd like your mentor to know ahead of time? "
    "(Optional — tap Skip to skip)"
)
ASK_CONSENT = (
    "Last step: please confirm you agree to be matched with a mentor "
    "and contacted through Telegram."
)

# ── Summaries ─────────────────────────────────────────────────────────────────

def mentor_summary(data: dict) -> str:
    return (
        "Here's your mentor profile:\n\n"
        f"Name: {data['full_name']}\n"
        f"Sphere(s): {', '.join(data['spheres'])}\n"
        f"Experience: {data['exp_level']}\n"
        f"Time per week: {data['devote_time']}\n"
        f"Open to mentoring: {', '.join(data['mentee_exp_prefs'])}\n"
        f"Extra: {data['extra'] or '—'}\n\n"
        "Does this look correct?"
    )


def mentee_summary(data: dict) -> str:
    return (
        "Here's your mentee profile:\n\n"
        f"Name: {data['full_name']}\n"
        f"Sphere(s): {', '.join(data['spheres'])}\n"
        f"Experience: {data['exp_level']}\n"
        f"Preferred mentor level: {', '.join(data['mentor_exp_prefs'])}\n"
        f"Time per week: {data['devote_time']}\n"
        f"Extra: {data['extra'] or '—'}\n\n"
        "Does this look correct?"
    )

# ── Match notifications ───────────────────────────────────────────────────────

def mentor_match_text(mentee: dict) -> str:
    return (
        "You've been matched with a mentee!\n\n"
        f"Name: {mentee['full_name']}\n"
        f"Sphere(s): {', '.join(mentee['spheres'])}\n"
        f"Experience: {mentee['exp_level']}\n"
        f"Time available: {mentee['devote_time']}\n"
        f"Message: {mentee['extra'] or '—'}"
    )


def mentee_match_text(mentor: dict) -> str:
    return (
        "Great news — you've been matched with a mentor!\n\n"
        f"Name: {mentor['full_name']}\n"
        f"Sphere(s): {', '.join(mentor['spheres'])}\n"
        f"Experience: {mentor['exp_level']}\n"
        f"Time available: {mentor['devote_time']}\n"
        f"Message: {mentor['extra'] or '—'}"
    )


NO_MATCH_MENTOR = (
    "Unfortunately, no suitable mentee was available for you this round. "
    "We hope to see you in the next one!"
)
NO_MATCH_MENTEE = (
    "Unfortunately, no mentor slot was available for you this round. "
    "We hope to see you in the next one!"
)

# ── Admin ─────────────────────────────────────────────────────────────────────

# What /broadcast actually sends. Deliberately NOT the /start landing text: that
# one stays a bare welcome, while this exists to announce something. Sent with
# parse_mode="HTML" and disable_web_page_preview=True — without the latter the
# map link drags a preview card in and the message stops being scannable.
OFFICE_MAP_URL = "https://yandex.uz/maps/-/CPhRZWNb"

BROADCAST_MESSAGE = (
    "📸 <b>How to promote a project or personal brand on Instagram?</b>\n\n"
    "This Friday the Freshman Academy office hosts a special meeting for students "
    "and alumni with <b>Mirolim</b> — founder of his own production studio and "
    "author of a blog with an audience of over 25,000.\n\n"
    "Seven years in media: he started filming on an ordinary phone straight after "
    "lyceum, and today leads a team of 10. His studio has worked with Yandex "
    "Uzbekistan, Uzum Bank, Coca-Cola, PepsiCo, HONOR and UzBAT, and shot music "
    "videos with budgets in the tens of thousands.\n\n"
    "He'll cover:\n"
    "— building a personal brand through Instagram;\n"
    "— creating content that attracts a quality audience;\n"
    "— promoting your own project or business;\n"
    "— turning a blog into a real professional tool.\n\n"
    "Afterwards you can ask questions, discuss your own projects, and get "
    "practical advice.\n\n"
    "📅 Friday, 31 July · 🕐 13:00 (Tashkent)\n"
    f'📍 <a href="{OFFICE_MAP_URL}">Freshman Academy office</a>\n\n'
    "Tap <b>{button}</b> below to register. Open to Freshman Academy students and "
    "alumni. 🎓"
)

BROADCAST_PREVIEW = (
    "📣 <b>Broadcast preview</b>\n\n"
    "This would go to <b>{count}</b> people, replacing their keyboard with the "
    "current one. Here's exactly what they'd see 👇\n\n"
    "Send <code>/broadcast confirm</code> to actually do it."
)
BROADCAST_STARTED = "📣 Broadcasting to {count} people — I'll report back when done."
BROADCAST_DONE = (
    "✅ <b>Broadcast finished</b>\n\n"
    "Delivered: {sent}\n"
    "Failed: {failed} <i>(blocked the bot, or deleted their account)</i>"
)

APPS_OPENED = (
    "Applications are now open.\n"
    "Mentors: /mentor | Mentees: /mentee"
)
APPS_CLOSED_ADMIN = "Applications are now closed. Run /match to pair mentors with mentees."
MATCH_BLOCKED_OPEN = "Close applications first with /close before running /match."
MATCH_BLOCKED_EMPTY = "Cannot run matching: need at least one mentor and one mentee."
MATCH_ALREADY_RAN = "Matching already ran. Check /status for existing matches."
MATCH_DONE = (
    "Matching complete!\n\n"
    "Matched pairs: {matched}\n"
    "Unmatched mentors: {unmatched_mentors}\n"
    "Unmatched mentees: {unmatched_mentees}"
)


def status_text(
    mentors: int,
    mentees: int,
    matches: int,
    is_open: bool,
    mentor_pending: int = 0,
    mentee_pending: int = 0,
) -> str:
    mentor_str = str(mentors)
    if mentor_pending:
        mentor_str += f" ({mentor_pending} pending review)"
    mentee_str = str(mentees)
    if mentee_pending:
        mentee_str += f" ({mentee_pending} pending review)"
    return (
        "Status\n\n"
        f"Applications: {'Open ✅' if is_open else 'Closed 🔒'}\n"
        f"Registered mentors: {mentor_str}\n"
        f"Registered mentees: {mentee_str}\n"
        f"Existing matches: {matches}"
    )


def mentor_review_card(mentor: dict, pending_count: int) -> str:
    return (
        f"👤 Mentor — {pending_count} pending\n\n"
        f"Name: {mentor['full_name']}\n"
        f"Sphere(s): {', '.join(mentor['spheres'])}\n"
        f"Experience: {mentor['exp_level']}\n"
        f"Time/week: {mentor['devote_time']}\n"
        f"Open to mentoring: {', '.join(mentor['mentee_exp_prefs'])}\n"
        f"Note: {mentor['extra'] or '—'}"
    )


def mentee_review_card(mentee: dict, pending_count: int) -> str:
    return (
        f"🙋 Mentee — {pending_count} pending\n\n"
        f"Name: {mentee['full_name']}\n"
        f"Sphere(s): {', '.join(mentee['spheres'])}\n"
        f"Experience: {mentee['exp_level']}\n"
        f"Preferred mentor: {', '.join(mentee['mentor_exp_prefs'])}\n"
        f"Time/week: {mentee['devote_time']}\n"
        f"Note: {mentee['extra'] or '—'}"
    )


def review_complete_text(summary: dict) -> str:
    return (
        "✅ Review complete!\n\n"
        f"Mentors: {summary['mentor_approved']} approved · "
        f"{summary['mentor_denied']} denied · "
        f"{summary['mentor_pending']} pending\n"
        f"Mentees: {summary['mentee_approved']} approved · "
        f"{summary['mentee_denied']} denied · "
        f"{summary['mentee_pending']} pending\n\n"
        "Run /match when ready."
    )


# ── Main keyboard buttons ─────────────────────────────────────────────────────

BTN_MENTOR = "📋 Register as Mentor"
BTN_MENTEE = "🙋 Register as Mentee"

# ── Elysium pre-2025 ──────────────────────────────────────────────────────────

BTN_ELYSIUM = "Admissions Program Alumni Group (pre-2025)"

ELYSIUM_INTRO = (
    "<b>Join Elysium pre-2025</b>\n\n"
    "Please answer the two short questions that follow to get your access link."
)

ELYSIUM_ASK_NAME = "What's your full name?"
ELYSIUM_ASK_COHORT = (
    "What's your Admissions Program cohort?\n\n"
    "Please provide the month and year (e.g. \"July 2024\")."
)

ELYSIUM_NAME_REQUIRED = "Please type your full name to continue."
ELYSIUM_COHORT_REQUIRED = "Please type the month and year of your cohort to continue."

ELYSIUM_APPROVED = (
    "✅ Thank you! Here's your link to join Elysium:\n{invite_link}\n\n"
    "⚠️ Please note: This group is specifically for alumni from these three cohorts.\n\n"
    "✅ 2022\n✅ 2023\n✅ 2024\n\n"
    "Students from the 2025 and 2026 cohorts are already connected through their own respective groups.\n\n"
    "Tap it to request access — you'll be approved in the group shortly."
)
ELYSIUM_ALREADY_APPROVED = (
    "✅ You're already registered. Here's your link to join Elysium:\n{invite_link}\n\n"
    "⚠️ Please note: This group is specifically for alumni from these three cohorts.\n\n"
    "✅ 2022\n✅ 2023\n✅ 2024\n\n"
    "Students from the 2025 and 2026 cohorts are already connected through their own respective groups.\n\n"
    "Tap it to request access — you'll be approved in the group shortly."
)

ELYSIUM_SET_POST_SUCCESS = "✅ Elysium pre-2025 post saved."
ELYSIUM_SET_POST_USAGE = (
    "Reply to any message with /elysium_set_post to save it as the Elysium post."
)

ELYSIUM_LIST_EMPTY = "No Elysium pre-2025 submissions yet."
ELYSIUM_LIST_HEADER = "📋 Elysium pre-2025 submissions ({count}):"
ELYSIUM_LIST_ENTRY = "{idx}. {full_name} — {cohort}{username_part}"

# ── Alumni Gate ─────────────────────────────────────────────────────────────────

# ── Operations ──────────────────────────────────────────────────────────────────
# Sent to admins when a handler raises. Throttled — see bot.on_error.
ADMIN_ERROR = (
    "🔴 <b>Bot hit an error</b>{where}\n\n"
    "<pre>{detail}</pre>\n"
    "Further alerts are muted for {cooldown} min so a repeating fault can't "
    "flood you. Check the logs for the full picture."
)

BACKUP_DONE = "💾 Database snapshot saved:\n<code>{path}</code>"

BACKUP_FAILED = (
    "⚠️ Backup failed — check the logs. The bot is still running normally."
)

ID_REPORT = (
    "🆔 <b>Chat info</b>\n"
    "Title: <code>{title}</code>\n"
    "Type: <code>{chat_type}</code>\n"
    "ID: <code>{chat_id}</code>"
)

ADDED_TO_GROUP = (
    "🤖 I was just added to <b>{title}</b>\n"
    "Chat ID: <code>{chat_id}</code>\n\n"
    "To have the Alumni Gate watch this group, just <b>make me an admin</b> here — "
    "that's all, no config and no restart. Or run /gate_watch in the group.\n\n"
    "Only <code>GATE_GROUP_ID</code> (the one alumni group everyone should end up "
    "in) is set in the environment, and it's already configured."
)
