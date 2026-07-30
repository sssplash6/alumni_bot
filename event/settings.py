"""Settings for the Event feature, read from the bot's .env.

Every name here is prefixed EVENT_ so it can't collide with another feature's
configuration as this bot grows.

Named settings.py rather than config.py so there's never any ambiguity with the
host bot's top-level config module.
"""
import os

# Importing the host config first guarantees .env has been loaded before we read
# os.environ below.
import config  # noqa: F401


def _flag(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Master switch. While False the menu button says "coming soon" and no handler
# does anything. Flip on once the two chat IDs below are filled in and the post
# has been set with /event_set_post.
LIVE: bool = _flag(os.environ.get("EVENT_LIVE", ""))

# The two chats that together make someone eligible. Registration requires being
# in BOTH: whoever is in one is handed a one-time link to the other, and whoever
# is in neither is told the event is alumni-only.
#
# The bot must be an ADMIN in both, with "invite users via link" — it needs to
# read membership and to mint the links. 0 means "not configured yet".
GROUP_ID: int = int(os.environ.get("EVENT_GROUP_ID", "0") or "0")
CHANNEL_ID: int = int(os.environ.get("EVENT_CHANNEL_ID", "0") or "0")

# Deep-link payload, so a "continue" nudge can route straight back into the flow.
START_PAYLOAD: str = "event"


def configured() -> bool:
    """True once both chats are set."""
    return GROUP_ID != 0 and CHANNEL_ID != 0


def active() -> bool:
    """True when the event flow should actually do things."""
    return LIVE and configured()
