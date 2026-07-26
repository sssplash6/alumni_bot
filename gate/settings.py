"""Settings for the Alumni Gate feature, read from the bot's .env.

Every name here is prefixed GATE_ in the environment so it can't collide with
another feature's configuration as this bot grows.

Named settings.py rather than config.py so there's never any ambiguity with the
host bot's top-level config module.
"""
import os

# Importing the host config first guarantees .env has been loaded before we read
# os.environ below.
import config  # noqa: F401


def _parse_id_list(raw: str, name: str) -> list[int]:
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as e:
        raise ValueError(f"{name} must be comma-separated integers, got: {raw!r}") from e


def _flag(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Master switch. While False the menu button says "coming soon", background
# detection stays dormant and no jobs are scheduled. Flip on once the group IDs
# and Airtable block below are filled in.
LIVE: bool = _flag(os.environ.get("GATE_LIVE", ""))

# The one alumni group everyone should end up in. The bot must be an ADMIN here
# to read membership and mint invite links. 0 means "not configured yet".
GROUP_ID: int = int(os.environ.get("GATE_GROUP_ID", "0") or "0")

# The groups watched for non-members. The bot must be an admin in each to receive
# join events and (with privacy mode off) see messages.
MONITORED_GROUP_IDS: list[int] = _parse_id_list(
    os.environ.get("GATE_MONITORED_GROUP_IDS", ""), "GATE_MONITORED_GROUP_IDS"
)

# Deep-link payload: group nudges open the bot with /start <this>.
START_PAYLOAD: str = "alumni"

# The onboarding brief asks for 50-100 words, so anything much shorter isn't the
# intro being asked for — without a floor a single "ok" would open the gate.
INTRO_MIN_WORDS: int = int(os.environ.get("GATE_INTRO_MIN_WORDS", "50") or "50")

# How often (days) to re-post the pinned "check if I'm in the alumni group"
# announcement — the only way to reach people who were already in a monitored
# group before the bot arrived and who never post. 0 disables the re-post.
ANNOUNCE_INTERVAL_DAYS: int = int(
    os.environ.get("GATE_ANNOUNCE_INTERVAL_DAYS", "5") or "5"
)

# How often (minutes) to re-check students still waiting on their form. 0
# disables the poll and relies solely on the button.
POLL_INTERVAL_MINUTES: int = int(
    os.environ.get("GATE_POLL_INTERVAL_MINUTES", "3") or "3"
)

# ── Airtable form verification ──────────────────────────────────────────────────
# A student is only admitted once their onboarding-form submission shows up in
# Airtable. The submission is matched to the person by Telegram user ID: the bot
# builds each student a personal form link with that ID pre-filled and hidden.
AIRTABLE_TOKEN: str = os.environ.get("GATE_AIRTABLE_TOKEN", "").strip()
AIRTABLE_BASE_ID: str = os.environ.get("GATE_AIRTABLE_BASE_ID", "").strip()
# Table name (e.g. "Onboarding") or table id (e.g. "tblXXXXXXXXXXXXXX").
AIRTABLE_TABLE: str = os.environ.get("GATE_AIRTABLE_TABLE", "").strip()
# The form field (create it as single-line text, hidden on the form) holding the
# student's Telegram user ID. Must match the prefill link's name exactly.
AIRTABLE_TG_FIELD: str = os.environ.get("GATE_AIRTABLE_TG_FIELD", "tg_id").strip()
# Optional: a field that must be non-empty for the form to count as complete.
AIRTABLE_DONE_FIELD: str = os.environ.get("GATE_AIRTABLE_DONE_FIELD", "").strip()
# Optional: a field holding the student's full name, kept on the roster.
AIRTABLE_NAME_FIELD: str = os.environ.get("GATE_AIRTABLE_NAME_FIELD", "").strip()

# Optional: a field holding a student-typed Telegram username (e.g. "@alice"),
# used ONLY as a fallback for rows submitted before tg_id existed.
#
# Username is a poor primary key — it's optional on Telegram, changeable at any
# time, and self-reported — which is exactly why tg_id is the real key. But for a
# backlog of submissions that predate tg_id, it's the only link available, and
# the bot compares against the username Telegram reports for whoever is talking
# to it rather than anything the student types now. Matching is done on a
# normalized value (no leading @, case-insensitive, trimmed).
#
# Leave blank to disable the fallback and require a tg_id match.
AIRTABLE_USERNAME_FIELD: str = os.environ.get(
    "GATE_AIRTABLE_USERNAME_FIELD", ""
).strip()

# The public Airtable form share link. The bot appends the personal prefill.
FORM_URL: str = os.environ.get("GATE_FORM_URL", "").strip()

# The values document students read before joining.
VALUES_DOC_URL: str = os.environ.get("GATE_VALUES_DOC_URL", "").strip()


def airtable_ready() -> bool:
    """True once the minimum Airtable settings are present."""
    return bool(AIRTABLE_TOKEN and AIRTABLE_BASE_ID and AIRTABLE_TABLE)


def active() -> bool:
    """True when the gate should actually do things."""
    return LIVE and GROUP_ID != 0
