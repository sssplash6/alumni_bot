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

# ── General ──────────────────────────────────────────────────────────────────

START_TEXT = (
    "Welcome! This bot connects mentors with mentees.\n\n"
    "Commands:\n"
    "/mentor — register as a mentor\n"
    "/mentee — register as a mentee\n"
    "/cancel — cancel current registration"
)

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

APPS_OPENED = (
    "Applications are now open.\n"
    "Mentors: /mentor | Mentees: /mentee"
)
APPS_CLOSED_ADMIN = "Applications are now closed. Run /match to pair mentors with mentees."
MATCH_BLOCKED_OPEN = "Close applications first with /close before running /match."
MATCH_BLOCKED_EMPTY = "Cannot run matching: need at least one mentor and one mentee."
MATCH_DONE = (
    "Matching complete!\n\n"
    "Matched pairs: {matched}\n"
    "Unmatched mentors: {unmatched_mentors}\n"
    "Unmatched mentees: {unmatched_mentees}"
)


def status_text(mentors: int, mentees: int, matches: int, is_open: bool) -> str:
    return (
        "Status\n\n"
        f"Applications: {'Open ✅' if is_open else 'Closed 🔒'}\n"
        f"Registered mentors: {mentors}\n"
        f"Registered mentees: {mentees}\n"
        f"Existing matches: {matches}"
    )
