"""Event registration — gated on being in BOTH the alumni group and channel.

Self-contained feature package. The host bot needs four things from it:

    import event

    await event.init_schema()              # in main(), alongside init_db()
    event.MENU_BUTTON                      # the main-menu keyboard label
    await event.start_registration(u, c)   # for the /start <payload> deep link
    event.register(app)                    # before gate.register(), which is last

Whoever is in only one of the two chats is handed a one-time link to the other
and picked up automatically when they join it; whoever is in neither is told the
event is alumni-only and given no link.

The bot must be an ADMIN in both chats, with "invite users via link" — that is
what makes membership readable, links mintable, and join events delivered.

Everything stays dormant until EVENT_LIVE is set and both EVENT_GROUP_ID and
EVENT_CHANNEL_ID are filled in — see settings.py for the environment contract.
The post itself is set at runtime with /event_set_post, since it carries an
image.
"""
from .db import init_schema
from .handlers import register, start_registration
from .messages import MENU_BUTTON
from .settings import START_PAYLOAD

__all__ = [
    "MENU_BUTTON",
    "START_PAYLOAD",
    "init_schema",
    "register",
    "start_registration",
]
