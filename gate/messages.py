"""User-facing copy for the Alumni Gate. HTML parse mode throughout."""
from config import ADMIN_CONTACT

# The reply-keyboard button that enters the gate from the bot's main menu. The
# gate owns this label because it also uses it as its entry filter.
MENU_BUTTON = "🎓 Join the Alumni Group"

# Shown while the master switch is off.
COMING_SOON = "🎓 The Alumni group is coming soon — stay tuned! ✨"

NOT_CONFIGURED = (
    "⚙️ The Alumni group hasn't been configured yet. Please try again a little "
    f"later, or contact {ADMIN_CONTACT}."
)

# Not in any of the community groups, so not eligible. Worded as "we can't find
# you" rather than "you're rejected": the honest failure mode here is a second
# Telegram account, and telling someone they don't belong when they do is worse
# than asking them to check.
NOT_IN_ANY_GROUP = (
    "🤔 I can't find you in any of the Freshman community groups.\n\n"
    "The Alumni group is for people who are already part of one of them, so I "
    "can't start you off just yet.\n\n"
    "If you <b>are</b> in one, you may be messaging me from a different Telegram "
    "account than the one you use there — try again from that account. Otherwise, "
    f"message {ADMIN_CONTACT} and they'll sort it out. 💬"
)

# Every eligibility lookup failed, so we genuinely don't know. Never refuse on
# this — a network blip must not read as "you don't belong".
ELIGIBILITY_UNAVAILABLE = (
    "⚠️ I couldn't check your group membership just now. Please try again in a "
    f"minute — if it keeps happening, message {ADMIN_CONTACT}."
)

# ── The nudge, sent as a DM ─────────────────────────────────────────────────────
# Seen in a community group but not in the Alumni group. Sent privately, never in
# the group: being publicly tagged for not having joined something reads as being
# called out. It only lands if they've started the bot before — everyone else is
# reached by the pinned announcement and the follow-up roundup.
DM_NUDGE = (
    "👋 Hey! Looks like you're not in the official <b>Alumni group</b> yet.\n\n"
    "Tap below to register. You'll get a personal one-time link and be let in "
    "automatically. 🎓"
)
REGISTER_BUTTON = "Register for the Alumni group ▸"

# ── Group announcement (pinned, re-posted every few days) ────────────────────────
# The cold-start path: everyone already in the group taps one button. Every reply
# to a tap is private — this and the follow-up roundup are the only two things the
# gate ever posts in a group.
GROUP_ANNOUNCE = (
    "🎓 <b>The official Alumni group</b>\n\n"
    "Every Freshman Academy graduate belongs in it — that's where introductions, "
    "opportunities and announcements land.\n\n"
    "<b>Not in it yet?</b> Tap <b>Join</b> and I'll walk you through it.\n"
    "<b>Already in?</b> Tap the second button and I'll stop asking.\n\n"
    "<i>Either way it's one tap, and nobody else sees your answer.</i>"
)
GROUP_ANNOUNCE_JOIN_BUTTON = "🎓 Join the Alumni group"
GROUP_ANNOUNCE_ALREADY_BUTTON = "✅ I'm already in it"

# Callback answers — a private popup only the person who tapped can see, so the
# group never learns who tapped what.
CB_ALREADY_MEMBER = "✅ Confirmed — you're in the Alumni group. I won't ask you again."
CB_NOT_CONFIGURED = (
    f"⚙️ The Alumni group isn't set up yet. Try again later or contact {ADMIN_CONTACT}."
)

# They said they're already in, but the membership check disagrees. Said gently:
# the likeliest explanations are a second Telegram account or having left.
CB_NOT_ACTUALLY_MEMBER = (
    "Hmm — I can't find this account in the Alumni group. "
    "Tap “Join the Alumni group” above and I'll sort you out."
)

# DMed when someone claims membership they don't have. The popup above says the
# same thing but vanishes when they tap it away; this stays in their chat with the
# bot, with the button attached. The group is never told about the claim.
DM_NOT_ACTUALLY_MEMBER = (
    "🤔 I checked, and this account <b>isn't in the Alumni group</b> yet!\n\n"
    "If you're in it on another Telegram account, no problem — but this one needs "
    "registering. Tap below. 🎓"
)

# The follow-up sweep, a cycle after the announcement: one message per batch of
# people who still haven't engaged with either button.
GROUP_FOLLOWUP = (
    "⏰ <b>Still missing from the Alumni group</b>\n\n"
    "{mentions}\n\n"
    "You haven't joined yet — it takes about a minute. Tap below and I'll walk you "
    "through it. 🎓"
)

# ── Private DM: onboarding ──────────────────────────────────────────────────────
# The welcome + onboarding brief. The form and doc links, plus the "I've completed
# the form" check, are buttons below it.
WELCOME_ONBOARDING = (
    "Hey,\n\n"
    "<b>Hazratbek</b> here — Treasurer of the Freshman Council.\n\n"
    "I'm thrilled to welcome you to the <b>Freshman Alumni Network</b>! You'll join a "
    "group of talented students studying at Ivy League universities, Stanford, "
    "UC Berkeley, NYUAD and more. But before you join, I need to onboard you. "
    "Onboarding has a few steps.\n\n"
    "You need to:\n\n"
    "<b>1.</b> Complete the form (button below).\n\n"
    "<b>2.</b> Prepare a short message (50–100 words) to introduce yourself. Indicate "
    "your full name, place of origin, the Freshman Academy program you've graduated "
    "from / are studying, your impressions of the program, your educational goals for "
    "the upcoming year, and any details you think are important.\n\n"
    "<b>3.</b> Read through the doc (button below) to better accustom yourself with our "
    "values.\n\n"
    "Complete the steps ASAP. Once you've <b>submitted the form</b>, tap "
    "“<b>I've completed the form ✅</b>” below — I'll verify it, then ask you for your "
    "intro, and send you your personal invite link automatically.\n\n"
    "Sincerely,\n"
    "Hazratbek — Freshman Council"
)
FORM_BUTTON = "📝 Complete the form"
DOC_BUTTON = "📖 Read the values doc"
CHECK_BUTTON = "I've completed the form ✅"

# Shown when the check button / poll can't find a completed submission yet.
FORM_NOT_VERIFIED = (
    "🔎 I can't see your completed form yet.\n\n"
    "Open the form with the button above (it carries your personal ID), make sure "
    "you <b>submitted</b> it, then tap “I've completed the form ✅” again. Sync can "
    "take a few seconds."
)

# Airtable unreachable / not configured — we couldn't verify, so we don't reject.
CHECK_UNAVAILABLE = (
    "⚠️ I couldn't reach the form records just now. Please try again in a minute — "
    f"if it keeps happening, message {ADMIN_CONTACT}."
)

# Not found by tg_id or by the stored username. Asked instead of refused: a
# submission that predates tg_id keeps whatever handle they had at the time, and
# people change handles, so both keys can miss someone who really did submit.
ASK_FULL_NAME = (
    "🔎 I can't find your form by your Telegram account.\n\n"
    "If you filled it in a while back, your username may have changed since — so "
    "let's try your name instead.\n\n"
    "Please send me the <b>full name exactly as you wrote it on the form</b>."
)

FULL_NAME_REQUIRED = (
    "Please send your <b>full name</b> — first and last, as written on the form."
)

FULL_NAME_NOT_FOUND = (
    "🔎 I still can't find a submission under <b>{name}</b>.\n\n"
    "Try it exactly as you typed it on the form — or the other way round "
    "(surname first). If you haven't actually submitted the form yet, use the "
    "button in the message above.\n\n"
    f"Still stuck? Message {ADMIN_CONTACT} and they'll sort it out. 💬"
)

# Form verified → ask for the intro (also sent by the background poll).
ASK_INTRO = (
    "✅ <b>Form verified — nice!</b>\n\n"
    "Last step: send me your <b>intro</b> right here (50–100 words) — your full name, "
    "where you're from, the Freshman Academy program you graduated from / are "
    "studying, your impressions, your goals for the year ahead, and anything else "
    "worth sharing.\n\n"
    "As soon as you send it I'll hand you your personal invite link. You'll re-post "
    "this same intro in the Alumni group so everyone can meet you. 🎓"
)

# They sent something that isn't text (voice note, photo, sticker, forward…)
# while we're waiting on their intro.
INTRO_NEEDS_TEXT = (
    "I can only read your intro as a <b>text message</b>. 📝\n\n"
    "Please type it out and send it here (50–100 words) — voice notes, photos and "
    "forwards won't reach me."
)

# Intro received but too short to be the intro we asked for.
INTRO_TOO_SHORT = (
    "Almost! ✍️ That's a bit short for an intro.\n\n"
    "I counted <b>{count}</b> word{plural} — I need at least <b>{minimum}</b>. Please "
    "include your <b>full name</b>, where you're from, the Freshman Academy program "
    "you graduated from / are studying, your impressions of it, and your goals for "
    "the year ahead — then send it again."
)

# Intro received → hand over the one-time invite link.
ADMITTED = (
    "🎉 <b>You're all set, {name}!</b>\n\n"
    "Tap below to join the Alumni group — this link is one-time and just for you, "
    "and you'll be admitted automatically.\n\n"
    "Once you're in, <b>post your intro</b> in the group so everyone can welcome "
    "you. 👋"
)
JOIN_BUTTON = "Join the Alumni group ▸"

# They already finished onboarding and have a link — re-hand it, don't re-mint.
ALREADY_REGISTERED = (
    "You've already been cleared! ✅ Here's your personal invite link again — tap to "
    "join the Alumni group."
)

ALREADY_MEMBER = "✅ You're already in the Alumni group — you're all set! 🎉"

LINK_FAILED = (
    f"⚠️ Something went wrong creating your invite link. Please message {ADMIN_CONTACT} "
    "and we'll sort it out."
)

# ── Admin ───────────────────────────────────────────────────────────────────────
STATS = (
    "📊 <b>Alumni Gate stats</b>\n\n"
    "Already members: <b>{member}</b>\n"
    "Nudged (not started): <b>{nudged}</b>\n"
    "Onboarding — form pending: <b>{awaiting_form}</b>\n"
    "Onboarding — intro pending: <b>{awaiting_intro}</b>\n"
    "Registered via bot: <b>{registered}</b>\n"
    "— — —\n"
    "Total people seen: <b>{total}</b>"
)

LIST_EMPTY = "No alumni registrations through the gate yet."
LIST_HEADER = "🎓 Alumni-gate registrations ({count}):"
LIST_ENTRY = "{idx}. {full_name}{username_part}"

ANNOUNCE_NO_TARGETS = (
    "⚙️ I'm not watching any groups yet. Add me to a group as an admin, then run "
    "<code>/gate_watch</code> in it."
)

# ── Managing the watched groups ─────────────────────────────────────────────────
WATCH_ADDED = (
    "👀 Now watching <b>{title}</b>\n"
    "<code>{chat_id}</code>\n\n"
    "I'll check people here against the Alumni group. Run "
    "<code>/gate_announce</code> to post the join message."
)
AUTO_WATCHED = (
    "👀 I was made an admin in <b>{title}</b>, so I've started watching it.\n"
    "<code>{chat_id}</code>\n\n"
    "People here will be checked against the Alumni group. Run "
    "<code>/gate_announce</code> in it to post the join message, or "
    "<code>/gate_unwatch</code> there if this wasn't intended."
)

WATCH_ALREADY = "👀 Already watching <b>{title}</b> — nothing to change."
WATCH_NOT_A_GROUP = "This only works inside a group. Run it in the group you want watched."
WATCH_IS_ALUMNI_GROUP = (
    "🚫 This is the Alumni group itself — the destination, not a group to watch. "
    "Watching it would nudge people about a group they're already in."
)
UNWATCH_DONE = "🚫 Stopped watching <b>{title}</b>. Existing records are kept."
UNWATCH_NOT_WATCHED = "I wasn't watching this group."

GROUPS_EMPTY = (
    "Not watching any groups yet. Add me to one as an admin and run "
    "<code>/gate_watch</code> there."
)
GROUPS_HEADER = "👀 <b>Watching {count} group(s)</b>\n\nDestination: <code>{destination}</code>"
GROUPS_ENTRY = "• {title} — <code>{chat_id}</code>"

ANNOUNCE_DORMANT = (
    "⚙️ The gate is dormant. Set <code>GATE_LIVE=true</code> (and "
    "<code>GATE_GROUP_ID</code>) in <code>.env</code>, then restart me."
)

ANNOUNCE_DONE = "📣 Announcement posted in <b>{ok}</b> of <b>{total}</b> group(s)."
