# Alumni Gate

Makes sure everyone in the community groups ends up in the **one official alumni
group**. Lives in the `gate/` package; the host bot wires it in with four calls.

## Why it's shaped like this

The Telegram Bot API **cannot list a group's member roster**. There is no call
that returns "everyone in this group" — only `getChatAdministrators` (admins
only), `getChatMemberCount` (a number), and `getChatMember(user_id)`, which needs
an ID you already have.

So the gate can't scan. It reacts to three triggers, each of which hands it a
user ID:

| Trigger | Reaches |
| --- | --- |
| Someone **joins** a monitored group | New arrivals |
| Someone **posts** in a monitored group (first time seen) | Active members |
| Someone **taps either button on the pinned announcement** | Everyone else — including people who were already in the group before the bot arrived and never post |

The third trigger is what makes the cold-start case solvable. It also has a
useful side effect: the tap means the bot has now *seen* that user, which is what
makes a `tg://user?id=…` mention resolve into a real ping.

A second constraint shapes the rest: **a bot cannot open a DM.** It can only
reply to someone who has messaged it first — which is also why every admin in
`ADMIN_IDS` must `/start` the bot before they'll receive alerts.

## What the gate is allowed to post in a group

Exactly two things:

1. **The pinned announcement.**
2. **The follow-up roundup** — one batched message naming the people who still
   haven't tapped either of its buttons.

Everything else the gate says to a person is private: a DM, or the popup answer
to their tap. Nobody is tagged in the group for not having joined, false
membership claims are corrected in private, and admin command confirmations
(`/gate_watch`, `/gate_unwatch`, `/gate_announce`, `/id`) are routed to the
admin's DM even when the command is typed in the group — see `_reply_privately`
in `gate/handlers.py`. Those confirmations fall back to a reply in place only if
the DM bounces, since silence would read as the command being broken.

The cost of that rule is real: a detection nudge can only be delivered to someone
who has messaged the bot before, so for most people it silently fails. They are
still recorded, and the announcement plus the roundup are what actually reach
them. The roundup is the one place the gate names people publicly, and it names
them as a group rather than one message each.

## The pinned announcement

The bot posts one message per monitored group with two buttons:

| Tap | What happens |
| --- | --- |
| **🎓 Join the Alumni group** | Opens the bot on their device, so onboarding starts on the same tap. |
| **✅ I'm already in it** | Recorded as a member so they're never asked again — *if the check agrees*. |

**Both buttons verify.** The second is deliberately not taken at face value:
the tap already hands us the user ID, so confirming real membership costs one API
call and no user effort. People tap "already in" to dismiss a message, and
someone wrongly skipped is skipped permanently — which is precisely the person the
gate exists to find. The usual cause is a second Telegram account, or having left
the group at some point.

A contradicted claim gets a popup, a DM if the bot can reach them, and a
`nudged` record — so the claim can't be used to quietly opt out, and the roundup
keeps naming them until they actually register. Neither outcome is posted in the
group; being corrected in front of everyone would be worse than the problem.

It re-posts every `GATE_ANNOUNCE_INTERVAL_DAYS` (default 5), deleting the previous
one so exactly one is live. A brand-new group has no recorded timestamp, so the
hourly job treats it as due and announces within the hour of the bot being
promoted — no command required.

`/gate_announce` skips that wait, and **the group's own admins can run it**, not
just the bot's. A leader who has just added the bot needs to see that it worked;
an hour of nothing looks identical to a broken setup. Requiring `ADMIN_IDS` here
would have put a Render env var edit and a restart between every new group and its
first announcement, which is exactly what auto-watch exists to avoid. Group admins
are confined to their own group — the fan-out to every watched group stays with
bot admins, since the fallback would otherwise turn one mistyped command into a
cross-group broadcast.

The re-post job ticks hourly but only acts where the stored timestamp says a group
is due, so restarting the bot can't spam a fresh announcement and the cadence
holds even if it restarts mid-cycle.

## Onboarding flow

```
Pinned announcement
  ├─ "I'm already in it" ──▶ verify ──yes──▶ recorded, never asked again
  │                             └────no──▶ popup + DM, recorded as nudged
  └─ "Join the Alumni group" ─▶ verify ──yes──▶ private popup, nothing posted
                                    │ no
DM nudge ──tap "Register"──▶ onboarding ◀──────┘
                                     │
                   in at least one watched group?
                                     │
              no ──▶ "I can't find you in any of the groups"
                                     │ yes
                        send the onboarding brief with buttons:
                        [Complete the form] [Read the doc] [I've completed the form ✅]
                                     │
                   tap "I've completed the form ✅"  (or the background poll)
                                     │
                        check Airtable for their submission
                                     │
                   found? ──no──▶ "I can't see your form yet…"
                                     │ yes
                        ask for their intro (50–100 words)
                                     │
                        intro of at least GATE_INTRO_MIN_WORDS words
                                     │
                        mint member_limit=1 invite link
                                     │
                        DM the personal link ──tap──▶ joins instantly
                                     │
                        they re-post their intro in the alumni group
```

**How the form is matched to the person.** The Bot API can't read who filled out
a form, so each student gets a *personalized* form link with their Telegram user
ID pre-filled into a hidden field (`prefill_tg_id=<id>&hide_tg_id=true`). Airtable
stores that ID on the submission row; the bot verifies completion by querying for
a row whose ID field matches — no usernames, no screenshots, no typing.

See `docs/AIRTABLE_SETUP.md` for the setup runbook and
`scripts/check_airtable.py` for a pre-flight check.

**Existing members never reach any of this.** Every entry point checks alumni
membership *before* consulting Airtable, so anyone already in the group is
classified silently and is never asked for a form or an intro — which is what
makes an existing cohort of several hundred people zero-friction. A test pins
this property down, because it's easy to break by reordering a handler.

## Who is allowed to onboard

The bot's username is public: anyone can open a DM with it and ask for the form.
Nothing about submitting a form proves anything — the form is public too, nobody
reviews it, and with no `GATE_AIRTABLE_DONE_FIELD` set, a row existing *is* the
pass. So without an eligibility check a stranger reaches a working invite link
with no human involved at any point.

The check is **membership in at least one watched group**, asked once at the
single door into onboarding (`start_onboarding`). That is the same question the
gate exists to answer: it moves people from the community groups into the alumni
group, so being in one of those groups is what makes someone a candidate. Later
steps aren't re-checked because the statuses they read can only be reached
through this one.

Being *detected* is deliberately not required — someone can legitimately open the
bot before a join, post or tap has surfaced them.

It short-circuits on the first group that has them, so the usual cost is one
`getChatMember` call, and none at all for someone already in the alumni group.

Three outcomes, kept distinct on purpose:

| Outcome | Response |
| --- | --- |
| In at least one watched group | Proceeds to the form |
| Every group answered, none had them | "I can't find you in any of the groups" |
| Nothing could be asked | "Couldn't check, try again in a minute" |

The third case matters because Telegram reports "not a participant" by *raising*
`BadRequest`, so a bare `except` would make an outage look identical to a
stranger. Only `BadRequest` counts as an answer; `Forbidden` (the bot is no
longer admin in that group) and network errors do not, since both are our problem
and refusing a real member over a hiccup is the one outcome worse than asking
them to retry.

**This makes the watched-group list load-bearing.** With no groups watched, the
eligibility check has nothing to ask and nobody can onboard — it fails closed and
logs why, rather than quietly admitting everyone. `GATE_REQUIRE_WATCHED_GROUP=false`
turns the whole check off if the alumni group is meant to be open to anyone.

Note what this does *not* do: it doesn't verify anyone is genuinely an alumnus,
only that they're already inside the community. If you need a human veto on top,
set `GATE_AIRTABLE_DONE_FIELD` to an approval column — `_is_complete()` then
requires it to be non-empty, so nobody is admitted until someone ticks it.

**Legacy submissions.** For rows that predate `tg_id`, setting
`GATE_AIRTABLE_USERNAME_FIELD` to a student-typed username column enables a
fallback: if no `tg_id` row matches, the bot tries that column instead. Both
sides are normalized (leading `@` dropped, lowercased, trimmed), so `@Alice`,
`alice` and ` ALICE ` all match. The bot's side is the username **Telegram
reports** for whoever is talking to it, not something the student retypes.

This is a deliberate weakening, scoped to legacy rows only: usernames are
optional on Telegram, changeable, and self-reported, so someone could rename
their account to squat a username in the historical list. New submissions always
match on `tg_id`, where a match is proof. Leave the setting blank to disable the
fallback entirely and require `tg_id`.

**The intro is a gate, not a record.** The bot requires an intro of at least
`GATE_INTRO_MIN_WORDS` words before issuing the link, but doesn't keep or forward
it — the student re-posts the real thing in the alumni group once they're in. Any
full name captured from the Airtable submission is kept on the roster.

## Commands

- `/alumni` or the **🎓 Join the Alumni Group** menu button — begin onboarding
- `/gate_watch` — (admins, in a group) start watching that group. Only needed if
  auto-watch is off, or the bot was already an admin before this shipped
- `/gate_unwatch` — (admins, in a group) stop watching it
- `/gate_groups` — (admins) list the watched groups and the destination
- `/gate_announce` — post/re-post the pinned announcement. **Any admin of the
  group it's run in** may use it, in that group only; bot admins may also run it
  in a DM to hit every watched group at once. See `_announce_targets`
- `/gate_stats` — (admins) counts per status
- `/gate_list` — (admins) the roster of everyone admitted through the gate

## Configuration

Two different things, easily confused:

- **`GATE_GROUP_ID` — the destination.** The one alumni group everyone should end
  up in. Stable; set once. The bot must be an admin here.
- **The watched groups — the sources.** The groups checked for people missing from
  the destination. These change often, so they are **not** configuration: they
  live in the database and are managed live. No restart, no config edit.

  **Making the bot an admin in a group is enough to start watching it.** The gate
  can't work without admin rights anyway — they're what deliver join events and,
  with privacy mode off, messages — so granting them is already a deliberate act
  by someone who runs that group, which makes a good opt-in on its own. Admins get
  a DM naming the group, so an unintended one can be undone with `/gate_unwatch`.
  Being removed from a group stops watching it. Set `GATE_AUTO_WATCH=false` to
  require an explicit `/gate_watch` everywhere instead.

`GATE_MONITORED_GROUP_IDS` still exists as a one-time bootstrap — anything listed
there is copied into the database at startup. Removing an id from it does not
unwatch the group; use `/gate_unwatch`.

Watching the destination group is refused: it would nudge people about a group
they are already in.

Everything else is an environment variable prefixed `GATE_`, documented in
`.env.example` and defined in `gate/settings.py`. The gate stays **dormant** until
`GATE_LIVE=true` *and* `GATE_GROUP_ID` is set: the menu button says "coming soon",
detection does nothing, and no jobs are scheduled.

## Going live — checklist

1. Bot is an **admin in the alumni group** with *Add users / invite via link*.
2. Bot is an **admin in every monitored group** (this is also what makes Telegram
   deliver join events at all).
3. **Privacy mode off** via BotFather `/setprivacy` → Disable, so the bot can see
   ordinary messages for the "first time they post" trigger.
4. Airtable block filled in and verified with `scripts/check_airtable.py`.
5. `GATE_GROUP_ID` set and `GATE_LIVE=true`. Groups the bot is already an admin
   in need one `/gate_watch` each; from then on, promoting it in a new group is
   enough.
6. Restart, then **tap the announcement button yourself first.**

Step 6 matters. Membership checks **fail closed**: if the bot can't read the
alumni group, everyone is treated as a non-member. That's harmless when one person
trickles in by posting; when 200 people tap a pinned message it marks all of them
`nudged`, and the next roundup names them in the group. Verify with your own tap
before announcing.

## Limits worth knowing

- **A true lurker who never joins, posts, or taps stays invisible.** The
  announcement is designed to make the third option nearly frictionless, but it
  can't be forced.
- **A detection nudge usually can't be delivered.** It's a DM, and Telegram only
  lets a bot DM someone who has messaged it first. The person is recorded either
  way; the roundup is what reaches them. This is the price of never tagging
  anyone in the group, and it's deliberate.
- **The roundup is the only group-wide rate-limit risk.** It batches eight
  mentions per message and pauses between batches, because Telegram caps a group
  at roughly 20 messages/minute. If you expect a large simultaneous wave, add
  `AIORateLimiter` to the application builder.
- **Invite links don't expire.** They're `member_limit=1`, but a forwarded link
  lets someone else consume the student's slot. Add `expire_date` in
  `_create_invite_link` if that matters.
- **Pinning is best-effort.** Without *Pin messages* rights the announcement still
  posts, just unpinned; clearing the previous one is likewise best-effort.
