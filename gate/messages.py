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
    "account than the one you use there — try again from that account.\n\n"
    "🎟 <b>Been given an invite code?</b> Send it to me here (it looks like "
    "<code>FA-XXXXX-XXXXX</code>) and I'll take it from there.\n\n"
    f"Otherwise, message {ADMIN_CONTACT} and they'll sort it out. 💬"
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

# Posted in the group a moment after someone joins one that already carries the
# announcement — the only channel that reliably reaches a brand-new member, who
# has usually never messaged the bot and so cannot be DMed. Reads as a welcome
# rather than a warning: it's their first minute in the room.
GROUP_WELCOME_TAG = (
    "👋 <b>Welcome!</b>\n\n"
    "{mentions}\n\n"
    "You're not in the <b>Freshman Alumni group</b> yet — that's the one where "
    "everything actually happens. Tap below and I'll walk you through it, takes "
    "about a minute. 🎓"
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
    "The Freshman Council"
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

# Same moment, but there's a channel to hand over too. A separate string rather
# than a conditional fragment: "both links are yours" has to be said plainly, and
# the intro instruction has to stay pinned to the *group*, or people post it into
# a channel they can't even write in.
ADMITTED_WITH_CHANNEL = (
    "🎉 <b>You're all set, {name}!</b>\n\n"
    "Two taps below, and both links are one-time and just for you:\n\n"
    "🎓 <b>The Alumni group</b> — the conversation. Once you're in, "
    "<b>post your intro there</b> so everyone can welcome you. 👋\n"
    "📣 <b>The channel</b> — announcements, opportunities and updates. Worth "
    "joining now so you don't miss anything."
)
JOIN_BUTTON = "Join the Alumni group ▸"
JOIN_CHANNEL_BUTTON = "Join the channel 📣"

# They already finished onboarding and have a link — re-hand it, don't re-mint.
# Deliberately vague about how many links: the same string is used whether or not
# there's a channel button under it.
ALREADY_REGISTERED = (
    "You've already been cleared! ✅ Here's your personal invite link again — tap "
    "below to join."
)

ALREADY_MEMBER = "✅ You're already in the Alumni group — you're all set! 🎉"

# Asked a few hours after they join. One question per person, ever — they're
# already in, so there's nothing to enforce with and nothing worth souring a
# welcome over.
INTRO_CHECK = (
    "👋 Welcome to the <b>Freshman Alumni Network</b>!\n\n"
    "Quick one: have you posted your <b>intro</b> in the group yet? It's how "
    "people know who you are, and it's what makes the network worth being in for "
    "everyone else too."
)
INTRO_YES_BUTTON = "✅ Yes, posted it"
INTRO_NO_BUTTON = "⏳ Not yet"

INTRO_THANKS = "Perfect — thanks! 🎉 Enjoy the network."

INTRO_NOT_YET = (
    "No problem! 🙌\n\n"
    "Make sure to post it soon, <b>so people know who you are and can reach out "
    "to you</b>."
)

LINK_FAILED = (
    f"⚠️ Something went wrong creating your invite link. Please message {ADMIN_CONTACT} "
    "and we'll sort it out."
)

# ── Invite tokens ───────────────────────────────────────────────────────────────
# For admitting someone who is in none of the approved groups — a guest speaker,
# a graduate who left every chat, an alumnus who predates them.

TOKEN_ACCEPTED = (
    "🎟 <b>Code accepted!</b>\n\n"
    "You don't need to be in one of the community groups — let's get you onboarded."
)

# Well-formed, but no such code. Distinct from TOKEN_MALFORMED because "check
# you typed it right" is unhelpful advice to someone who typed it right.
TOKEN_INVALID = (
    "🤔 I don't recognise that invite code.\n\n"
    "It might have been cancelled, or belong to a different bot. Message "
    f"{ADMIN_CONTACT} and they'll issue you a new one."
)

# Recognisably an attempt — starts with FA- — but doesn't parse. Almost always a
# typo, so it names the two characters people actually get wrong.
TOKEN_MALFORMED = (
    "🤔 That's <i>nearly</i> an invite code, but not quite.\n\n"
    "They look like <code>FA-XXXXX-XXXXX</code> — five characters, a dash, five "
    "more.\n\n"
    "If you're retyping it, note that codes never contain <b>I</b>, <b>L</b>, "
    "<b>O</b>, <b>0</b> or <b>1</b> — those are left out because they're so easy "
    "to mix up. Copying and pasting is safest.\n\n"
    f"Still stuck? Message {ADMIN_CONTACT}."
)

# Used and revoked share a message on purpose: telling someone "that code was "
# revoked" invites an argument, and "already used" is true of a revoked code from
# where they're standing — either way the answer is to ask for another.
TOKEN_USED = (
    "🎟 That code has already been used.\n\n"
    "Each one works once. If it should have been yours, message "
    f"{ADMIN_CONTACT} and they'll issue a new one."
)

TOKEN_EXPIRED = (
    "🎟 That code has expired.\n\n"
    f"Message {ADMIN_CONTACT} and they'll send you a fresh one — it only takes a "
    "moment."
)

# Shown to the admin once, at creation. The plaintext exists nowhere else.
TOKEN_CREATED = (
    "🎟 <b>Invite code #{token_id}</b>\n\n"
    "<code>{token}</code>\n\n"
    "For: {note}\n"
    "{ttl}\n\n"
    "Send it to the person and have them paste it to me in a private chat. It "
    "works <b>once</b>, and lets them onboard without being in any community "
    "group.\n\n"
    "⚠️ I don't store the code itself, only a fingerprint — so this message is the "
    "only copy. Lose it and issue another with <code>/gate_token</code>; cancel "
    "this one with <code>/gate_revoke {token_id}</code>."
)
TOKEN_TTL_DAYS = "Expires in <b>{days} days</b>."
TOKEN_TTL_NEVER = "Doesn't expire."

TOKEN_REDEEMED_ALERT = (
    "🎟 Invite code <b>#{token_id}</b> ({note}) was just redeemed by {who} "
    "({username}).\n\nThey're now going through onboarding."
)

TOKENS_EMPTY = (
    "No invite codes issued yet. Create one with <code>/gate_token [who it's "
    "for]</code>."
)
TOKENS_HEADER = "🎟 <b>Invite codes ({count})</b>"
TOKENS_ENTRY = "<b>#{token_id}</b> {state} — {note}"
TOKENS_FOOTER = (
    "<i>Codes aren't stored, only fingerprints, so they can't be shown again. "
    "Cancel an unused one with /gate_revoke &lt;id&gt;.</i>"
)
TOKEN_STATES = {
    "ok": "🟢 unused",
    "used": "✅ redeemed",
    "revoked": "🚫 revoked",
    "expired": "⌛ expired",
}

REVOKE_USAGE = (
    "Usage: <code>/gate_revoke &lt;id&gt;</code> — the number from "
    "<code>/gate_tokens</code>, e.g. <code>/gate_revoke 3</code>."
)
REVOKE_DONE = "🚫 Invite code <b>#{token_id}</b> is cancelled — it won't work now."
REVOKE_NOTHING = (
    "Nothing to cancel for <b>#{token_id}</b> — it's already been used, already "
    "cancelled, or never existed. Either way it can't be redeemed."
)


# ── Admin ───────────────────────────────────────────────────────────────────────
# The block above the line is a snapshot: one line per status, so it partitions
# "total" and every status must appear or the lines silently stop adding up.
# "Registered through the bot" sits below it because it is a different kind of
# number — a lifetime count that deliberately overlaps "Already members", and
# the one people actually mean when they ask how the gate is doing.
STATS = (
    "📊 <b>Alumni Gate stats</b>\n\n"
    "Already members: <b>{member}</b>\n"
    "Nudged (not started): <b>{nudged}</b>\n"
    "Onboarding — form pending: <b>{awaiting_form}</b>\n"
    "Onboarding — name pending: <b>{awaiting_name}</b>\n"
    "Onboarding — intro pending: <b>{awaiting_intro}</b>\n"
    "Invited — link not used yet: <b>{registered}</b>\n"
    "— — —\n"
    "Total people seen: <b>{total}</b>\n"
    "Registered through the bot: <b>{registered_ever}</b>\n\n"
    "<i>That last one is a lifetime count, so most of them are also in "
    "\"already members\" — joining the group moves people there.</i>"
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
    "👀 I was made an admin in <b>{title}</b>.\n"
    "<code>{chat_id}</code>\n\n"
    "⚠️ <b>It isn't switched on.</b> I'm doing nothing there — no announcement, no "
    "checks — because anyone can add me to a group and promote me, so this on its "
    "own doesn't tell me these are our people.\n\n"
    "If it's a real Freshman group, run <code>/gate_announce</code> <b>inside it</b>. "
    "That posts the join message and approves it, so being a member there counts "
    "towards joining the Alumni group.\n\n"
    "If you don't recognise it, do nothing — or <code>/gate_unwatch</code> there to "
    "forget it entirely."
)

# Confirms the approval, not just the post — this is the moment a group starts
# counting towards eligibility, so it should be impossible to do by accident and
# not notice.
ANNOUNCE_APPROVED = (
    "✅ <b>{title}</b> is now approved.\n\n"
    "The announcement is posted and pinned, and being a member here now counts "
    "towards joining the Alumni group. Undo with <code>/gate_unwatch</code> in "
    "that group."
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
GROUPS_HEADER = (
    "✅ <b>{count} approved group(s)</b>\n\nDestination: <code>{destination}</code>"
)
GROUPS_ENTRY = "• {title} — <code>{chat_id}</code>"
GROUPS_NONE_APPROVED = (
    "<i>None yet — run /gate_announce inside a group to approve it.</i>"
)
GROUPS_PENDING_HEADER = (
    "⏳ <b>{count} waiting for approval</b>\n"
    "<i>I was added and promoted here, but I'm doing nothing until an admin runs "
    "/gate_announce inside. Don't recognise one? /gate_unwatch there.</i>"
)

ANNOUNCE_DORMANT = (
    "⚙️ The gate is dormant. Set <code>GATE_LIVE=true</code> (and "
    "<code>GATE_GROUP_ID</code>) in <code>.env</code>, then restart me."
)

ANNOUNCE_DONE = "📣 Announcement posted in <b>{ok}</b> of <b>{total}</b> group(s)."
