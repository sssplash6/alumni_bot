"""Alumni Gate — nudge people in the monitored groups into the one alumni group.

Self-contained feature package. The host bot needs only four things from it:

    import gate

    await gate.init_schema()              # in main(), alongside init_db()
    await gate.load_monitored()           # prime the watched-group list
    gate.MENU_BUTTON                      # the main-menu keyboard label
    await gate.start_onboarding(u, c)     # for the /start <payload> deep link
    gate.register(app)                    # LAST in build_app()

``register`` must come last because the gate's private-message handlers match
broadly; registering them after every ConversationHandler means an in-progress
conversation always wins.

Everything stays dormant until GATE_LIVE is set and GATE_GROUP_ID points at the
alumni group — see settings.py for the full environment contract.
"""
from .db import init_schema
from .handlers import load_monitored, register, start_onboarding
from .messages import MENU_BUTTON
from .settings import START_PAYLOAD

__all__ = [
    "MENU_BUTTON",
    "START_PAYLOAD",
    "init_schema",
    "load_monitored",
    "register",
    "start_onboarding",
]
