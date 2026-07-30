"""User-facing copy for the Event feature. HTML parse mode throughout.

English, to match the rest of the bot's copy (the Elysium and gate flows speak
English to this same audience). The event post itself is whatever an admin sets
with /event_set_post, so its language is theirs to choose.
"""

# The reply-keyboard button that enters the flow. This module owns the label
# because it doubles as the entry filter.
MENU_BUTTON = "📅 Register for the event"

# Shown while the master switch is off.
COMING_SOON = "📅 Event registration is coming soon — stay tuned! ✨"

NOT_CONFIGURED = (
    "⚙️ The event hasn't been set up yet. Please try again a little later, or "
    "contact an admin."
)

# In neither chat. No link is offered: handing an outsider a way in would defeat
# the point of the event being alumni-only.
NOT_ALUMNI = (
    "🔒 <b>This event is for Freshman Academy alumni only.</b>\n\n"
    "I can't find you in the alumni group or the alumni channel, so I can't "
    "register you.\n\n"
    "If you <b>are</b> an alum, you may be messaging me from a different "
    "Telegram account than the one you use there — try again from that account. "
    "Otherwise, message an admin. 💬"
)

# In exactly one of the two. {what} is "group" or "channel".
JOIN_THE_OTHER = (
    "You're almost there! 🎓\n\n"
    "Registration needs you in <b>both</b> the alumni group and the alumni "
    "channel — right now I can only see you in one of them.\n\n"
    "Here's your personal one-time link to the {what} you're missing. Tap it to "
    "join, and I'll message you the moment you're in:"
)
JOIN_BUTTON = "Join the alumni {what} ▸"

# They joined the missing chat and are now eligible.
JOIN_DETECTED = "✅ Great — you're in both now! Let's finish your registration."

CONTINUE_BUTTON = "Continue registration ▸"

# Couldn't mint the link.
LINK_FAILED = (
    "⚠️ Something went wrong creating your invite link. Please try again in a "
    "minute — if it keeps happening, message an admin."
)

# Every membership lookup failed, so we genuinely don't know. Never refuse on
# this — a network blip must not read as "you're not an alum".
CHECK_UNAVAILABLE = (
    "⚠️ I couldn't check your membership just now. Please try again in a minute — "
    "if it keeps happening, message an admin."
)

ASK_NAME = (
    "Last step — what's your <b>full name</b>?\n\n"
    "This is what we'll put on the attendee list."
)
NAME_REQUIRED = "Please type your full name to continue."

REGISTERED = (
    "🎉 <b>You're registered, {name}!</b>\n\n"
    "See you at the event. If your plans change, just message an admin."
)

ALREADY_REGISTERED = (
    "✅ You're already registered for this event, {name}. See you there! 🎉"
)

# ── Admin ───────────────────────────────────────────────────────────────────────

SET_POST_USAGE = (
    "Reply to the event post (image and all) with /event_set_post to save it as "
    "the post people see when they register."
)
SET_POST_SAVED = (
    "✅ Event post saved. Registrants will now be shown that message.\n\n"
    "Set EVENT_LIVE=true to open registration."
)
POST_MISSING = (
    "⚠️ No event post has been set yet — reply to it with /event_set_post."
)

LIST_EMPTY = "No event registrations yet."
LIST_HEADER = "📋 Registered for the event ({count}):"
LIST_ENTRY = "{idx}. {full_name}{username_part}"

STATS = (
    "📊 <b>Event</b>\n\n"
    "Registered: {registered}\n"
    "Waiting to join the other chat: {awaiting_join}\n"
    "Asked for their name, not yet answered: {awaiting_name}"
)
