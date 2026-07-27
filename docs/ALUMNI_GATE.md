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
reply to someone who has messaged it first. That's why nudges are posted publicly
in the group rather than sent privately, and why every admin in `ADMIN_IDS` must
`/start` the bot before they'll receive alerts.

## The pinned announcement

The bot posts one message per monitored group with two buttons:

| Tap | What happens |
| --- | --- |
| **🎓 Join the Alumni group** | Opens the bot on their device, so onboarding starts on the same tap. Nothing is posted publicly — they volunteered, so a tag would be noise. |
| **✅ I'm already in it** | Recorded as a member so they're never asked again — *if the check agrees*. |

**Both buttons verify.** The second is deliberately not taken at face value:
the tap already hands us the user ID, so confirming real membership costs one API
call and no user effort. People tap "already in" to dismiss a message, and
someone wrongly skipped is skipped permanently — which is precisely the person the
gate exists to find. When the claim is contradicted they're told privately and
pointed at the Join button, and **nothing is recorded**, so the next announcement
reaches them again. The usual cause is a second Telegram account, or having left
the group at some point.

Neither outcome is ever posted in the group. Being publicly corrected would be
worse than the problem.

It re-posts every `GATE_ANNOUNCE_INTERVAL_DAYS` (default 5), deleting the previous
one so exactly one is live. `/gate_announce` posts one immediately.

The re-post job ticks hourly but only acts where the stored timestamp says a group
is due, so restarting the bot can't spam a fresh announcement and the cadence
holds even if it restarts mid-cycle.

## Onboarding flow

```
Pinned announcement
  ├─ "I'm already in it" ──▶ verify ──yes──▶ recorded, never asked again
  │                             └────no──▶ told privately, nothing recorded
  └─ "Join the Alumni group" ─▶ verify ──yes──▶ private popup, nothing posted
                                    │ no
Group nudge ──tap "Register"──▶ onboarding ◀───┘
                                     │
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
- `/gate_announce` — (admins) post/re-post the pinned announcement
- `/gate_stats` — (admins) counts per status
- `/gate_list` — (admins) the roster of everyone admitted through the gate

## Configuration

Every setting is an environment variable prefixed `GATE_`, documented in
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
5. `GATE_GROUP_ID` and `GATE_MONITORED_GROUP_IDS` set, then `GATE_LIVE=true`.
6. Restart, then **tap the announcement button yourself first.**

Step 6 matters. Membership checks **fail closed**: if the bot can't read the
alumni group, everyone is treated as a non-member. That's harmless when one person
trickles in by posting; it's a mass-mistagging event when 200 people tap a pinned
message. Verify with your own tap before announcing.

## Limits worth knowing

- **A true lurker who never joins, posts, or taps stays invisible.** The
  announcement is designed to make the third option nearly frictionless, but it
  can't be forced.
- **No rate limiting.** Each non-member tap posts a separate public message to
  the same group, and Telegram caps that at roughly 20/minute per group. A burst
  of taps will hit the limit: the tag is dropped (logged, and the person isn't
  marked nudged, so a later tap retries) while the deep-link into onboarding still
  works. If you expect a large simultaneous wave, add `AIORateLimiter` to the
  application builder.
- **Invite links don't expire.** They're `member_limit=1`, but a forwarded link
  lets someone else consume the student's slot. Add `expire_date` in
  `_create_invite_link` if that matters.
- **Pinning is best-effort.** Without *Pin messages* rights the announcement still
  posts, just unpinned; clearing the previous one is likewise best-effort.
