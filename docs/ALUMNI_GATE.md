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

Exactly three things:

1. **The pinned announcement.**
2. **The welcome tag** — newcomers who join a group that already carries the
   announcement, and who aren't in the alumni group, are named there shortly after
   arriving.
3. **The follow-up roundup** — one batched message naming the people who still
   haven't tapped either of its buttons.

Everything else the gate says to a person is private: a DM, or the popup answer
to their tap. False membership claims are corrected in private, and admin command
confirmations (`/gate_watch`, `/gate_unwatch`, `/gate_announce`, `/id`) are routed
to the admin's DM even when the command is typed in the group — see
`_reply_privately` in `gate/handlers.py`. Those confirmations fall back to a reply
in place only if the DM bounces, since silence would read as the command being
broken.

All three public posts exist for the same reason: a detection nudge can only be
delivered to someone who has messaged the bot before, so for most people — and for
a brand-new member almost always — it silently fails. They're still recorded, and
the group is the only channel that actually reaches them. Both the welcome tag and
the roundup name people **as a group rather than one message each**, which is what
keeps them inside Telegram's rate limit and out of the way of the conversation.

What is still never posted: anything about someone's *intro* (see the intro
reminder below), and any correction of a false membership claim.

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
one so exactly one is live. It only ever posts in **approved** groups.

`/gate_announce` skips the wait, and is **admins-only, because running it inside a
group is what approves that group** — see below.

The re-post job ticks hourly but only acts where the stored timestamp says a group
is due, so restarting the bot can't spam a fresh announcement and the cadence
holds even if it restarts mid-cycle.

## Welcoming a newcomer

Someone joining a watched group **after** it has been announced in is checked
against the alumni group, and if they're missing from it they're named in that
group with the Register button attached. Controlled by `GATE_WELCOME_TAG`
(default on).

This is the one channel that reliably reaches a new arrival. The DM nudge is
attempted first and almost always fails for them — they've never messaged the bot
— and the pinned announcement only works if they scroll back to it.

Four things narrow it, and each is load-bearing:

- **Only after the announcement.** The tag points at the bot; the pinned message
  is the context that makes that make sense. A group that's approved but not yet
  announced in gets nothing, and the newcomer waits for the roundup instead.
- **Only on a join.** First-time *posters* are deliberately not tagged — they're
  mid-conversation, and the roundup reaches them soon enough. `_process_user`
  returns whether it nudged and the join path decides; the check itself never
  posts publicly.
- **Coalesced, not one message each.** Arrivals are buffered for
  `_WELCOME_TAG_DELAY` (45s) and flushed as one batched mention, so an admin
  adding a dozen people produces one message rather than a dozen into the same
  rate limit the roundup batches to dodge. Whoever arrives first schedules the
  flush; everyone inside the window rides along on it.
- **Status is re-read at flush time.** That delay is exactly long enough for a
  keen newcomer to have already tapped through, and tagging them then would be
  wrong twice — a public call-out for something they've done, and a re-stamp to
  `nudged` that would discard their onboarding progress (`_upsert` writes `status`
  unconditionally).

A delivered tag re-stamps `nudged_at`, so the roundup's clock counts from the tag
rather than from the join. A tag that fails to send leaves the stamp alone and the
roundup picks them up as normal.

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
                        (+ one for the channel, if configured)
                                     │
                        DM the personal link ──tap──▶ joins instantly
                                     │
                        they re-post their intro in the alumni group
```

**"DM nudge" above stands for any of the three chases**, which all carry the same
Register button and all land in the same place: the private nudge, the welcome tag
on joining, and the roundup mention five days later.

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

The check is **membership in at least one APPROVED group**, asked once at the
single door into onboarding (`start_onboarding`). That is the same question the
gate exists to answer: it moves people from the community groups into the alumni
group, so being in one of those groups is what makes someone a candidate. Later
steps aren't re-checked because the statuses they read can only be reached
through this one.

### Approved, not merely watched

This distinction is the security model, and collapsing the two is a full bypass.

Auto-watch enrols any group whose owner promotes the bot. That is a fine signal
for *"this group exists and I can work in it"* and a worthless one for *"the
people here are Freshman people"* — *anyone* can create a group, add this bot and
promote it. If eligibility read the watched set, the attack is four steps with no
human anywhere in it:

1. make a group, add the bot, promote it → auto-watch enrols it;
2. open the bot: eligibility finds them in "a watched group" → passes;
3. fill in the form — it's public, and submitting it proves nothing;
4. send fifty words → a single-use invite link to the alumni group, and to the
   channel with it.

So the watched set is split in two. `approved` is set only by a **bot admin
acting inside a group** — `/gate_announce` there, `/gate_watch` there, or the
`GATE_MONITORED_GROUP_IDS` seed, which requires editing the deployment's
environment. Auto-watch never sets it.

**`ADMIN_IDS` is therefore the trust root, and it's meant to be widened.** Group
leaders are expected to be on it, so that setting up a group stays self-service —
add the bot, promote it, `/gate_announce` — with the check being "we chose this
person" rather than "this person owns a group". Adding a leader is one-off; the
alternative was them queueing behind whoever holds the list, every time.

Worth knowing what that grants: `ADMIN_IDS` is a single list, so anyone on it also
gets `/broadcast` (messages every user of the bot), `/backup` (dumps the DB),
`/gate_list` (the full roster) and the mentor/mentee admin commands. If that's too
much for a group leader, split it — a separate `GATE_APPROVER_IDS` consulted only
by the approval paths is a small change, and this is the place to make it.

**Approval gates everything, not just eligibility.** An unapproved group gets no
announcement, no detection, no roundup. Announcing in one would invite its members
to tap Join and then refuse them as ineligible, which is worse than silence — and
in a group a stranger set up, it would be the bot advertising itself somewhere
nobody asked for it. Watching an unapproved group buys exactly one thing: it
appears in `/gate_groups` so an admin can approve it without hunting for a chat ID.

**Why approval rides on `/gate_announce` rather than its own command.** There is
then no separate step to forget: the command an admin was already going to run to
switch a group on is the one that approves it, so an announced-but-unapproved
group and an approved-but-unannounced one are both unrepresentable. Approval
happens *before* the post, because the announcement invites people to tap Join and
eligibility reads the approved set — the other order leaves a window where the
fastest tapper is refused by the group they're standing in.

A DM `/gate_announce` approves nothing: approving means vouching for a specific
group, and a DM carries none.

### Invite codes — the deliberate way round the check

Not everyone who belongs is in a community group: a guest speaker, a graduate who
left every chat years ago, someone from before the groups existed. Without a way
in for them, the only options are turning `GATE_REQUIRE_WATCHED_GROUP` off (which
opens the door to everyone) or adding them to a group they have no reason to be
in — so `/gate_token` mints a one-off code instead.

    admin: /gate_token Dilnoza — guest speaker, Nov panel
    bot:   🎟 Invite code #4 …  FA-K7M2Q-XR94T

The person pastes it into a DM with the bot and lands in onboarding with the
approved-group check skipped. Everything after that is unchanged: they still fill
in the form, still send an intro, still get a personal single-use link.

Properties worth knowing, in rough order of how much they matter:

- **Only a hash is stored.** The plaintext appears once, in the reply to
  `/gate_token`. `gate_invite_tokens` holds `sha256(code)`, so a leaked database
  backup — and these are taken on a schedule — yields no usable codes. The cost is
  that a lost code can't be re-read; issue another and revoke the first.
- **Single use, enforced in SQL.** `redeem_invite_token` guards on
  `redeemed_by IS NULL` inside the `UPDATE`, not in a read-then-write, so two
  people racing the same code can't both get in.
- **The exemption outlives the code.** Redeeming sets `gate_users.exempt`, because
  eligibility is re-asked *every* time someone enters onboarding — a one-shot
  bypass would strand anyone who abandoned halfway and came back, and they'd need
  a second code to finish something they already paid for with the first.
- **They expire.** `GATE_TOKEN_TTL_DAYS`, default 3, 0 to disable. `/gate_revoke`
  handles the codes you remember issuing; the TTL handles the rest.
- **Redemptions are announced to every admin**, so a code being used is visible
  without anyone running a report.
- **Near-misses get answered.** Anything starting `FA-` and under 24 characters is
  treated as an attempt and told what's wrong, rather than ignored — a dropped
  character otherwise looks like a broken bot. The length bound is what stops a
  fifty-word intro that opens with `FA-` being read as a malformed code; the
  alphabet omits `I`, `L`, `O`, `0` and `1` so there's less to mistype.

`/gate_token`, `/gate_tokens` and `/gate_revoke` are **DM-only**, with no group
variant: the reply to the first is a working key to the alumni group.

Note that anyone in `ADMIN_IDS` can mint one. If that list has been widened to
include group leaders, they can admit individuals as well as approve groups —
which is the smaller of the two powers, but worth knowing you granted it.

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

## The intro check

Re-posting the intro in the group is the last thing onboarding asks for and the
only step with nothing behind it: by then they already hold the invite link, so
there is no lever left, and none is wanted.

`GATE_INTRO_REMINDER_HOURS` (default 3) after someone **joins**, the bot DMs them
a question with two buttons — *Yes, posted it* / *Not yet*. Yes closes it out; not
yet gets a short "here's how" and closes it out anyway. One question per person,
ever.

**Asked, not detected.** The bot could watch the alumni group and work it out, and
that was the first cut, but it's the worse design: it makes the feature silently
depend on privacy mode staying off, and it calls anyone who posted while the bot
was restarting a no-show — `main.py` polls with `drop_pending_updates=True`, so
messages sent during a deploy are gone. Asking costs one tap, is right every time,
and the answer is worth more than the inference. The bot therefore listens for
nothing at all in the alumni group.

- **The clock starts at the join event, not at the invite link.** You can't post
  in a group you haven't walked into. `mark_joined_group` is called only from
  `on_chat_member`, since that's the one moment that knows *when* — `mark_member`
  fires whenever we merely discover somebody is in there, which can be months late.
- **"Yes" is taken at face value**, unlike the announcement's *I'm already in it*.
  That one is verified because a wrong answer excludes someone from the group
  permanently; this one costs a reminder nobody needed, and calling a member a
  liar over it is a strange way to welcome them.
- **People who predate the gate are never asked.** No join event was ever seen for
  them, so `joined_group_at` is null and they never enter the query.
- **A failed DM still closes the row out.** Someone added to the group by hand may
  never have started the bot, and that won't change; retrying would mean this row
  coming back every tick forever.
- **Nothing is posted in the group.** An unanswered public "where's your intro?"
  would be the worst possible welcome — and it would break the two-messages rule
  above.

## Commands

- `/alumni` or the **🎓 Join the Alumni Group** menu button — begin onboarding
- `/gate_watch` — (admins, in a group) watch **and approve** that group. Only
  needed if auto-watch is off, the bot was already an admin before this shipped,
  or you're re-enabling a group after `/gate_unwatch`
- `/gate_unwatch` — (admins, in a group) stop watching it, and drop its approval
- `/gate_groups` — (admins) approved groups, then any waiting for approval
- `/gate_announce` — **(admins only)** post/re-post the pinned announcement. Run
  inside a group it also **approves** that group; run in a DM it re-posts to every
  already-approved group and approves nothing. See `_announce_targets`
- `/gate_stats` — (admins) counts per status
- `/gate_list` — (admins) the roster of everyone admitted through the gate
- `/gate_token [note]` — (admins, **DM only**) mint a one-off invite code for
  someone in none of the approved groups. Shown once; the note is what makes the
  list auditable later
- `/gate_tokens` — (admins, DM only) every code issued, with its state and note.
  Values are never shown — only hashes are stored
- `/gate_revoke <id>` — (admins, DM only) kill an unredeemed code by its id

## Configuration

Three different things, the first two easily confused:

- **`GATE_GROUP_ID` — the destination.** The one alumni group everyone should end
  up in. Stable; set once. The bot must be an admin here.
- **The watched groups — the sources.** The groups checked for people missing from
  the destination. These change often, so they are **not** configuration: they
  live in the database and are managed live. No restart, no config edit.
- **`GATE_CHANNEL_ID` — the second destination.** Optional. The announcements
  channel handed over with the group link. The bot must be an admin here too. Not
  a source: membership there is never read, and nobody is nudged about it.

  **Making the bot an admin in a group starts watching it — and nothing more.**
  The gate can't work without admin rights anyway (they're what deliver join
  events and, with privacy mode off, messages), so granting them is a fine signal
  that the bot *may* work there. It is not a signal about the people in the group,
  because the person granting them can be anyone at all. So auto-watch leaves the
  group **pending**: it is inert until an admin runs `/gate_announce` inside it.
  Admins get a DM naming the group with that instruction, and `/gate_unwatch`
  forgets an unrecognised one. Being removed from a group stops watching it. Set
  `GATE_AUTO_WATCH=false` to require an explicit `/gate_watch` everywhere instead.

`GATE_MONITORED_GROUP_IDS` still exists as a one-time bootstrap — anything listed
there is copied into the database at startup, **approved**, since editing a
deployment's environment takes the same authority the approval commands check
for. Removing an id from it does not unwatch the group; use `/gate_unwatch`.

Watching the destination group is refused: it would nudge people about a group
they are already in.

### The announcements channel

With `GATE_CHANNEL_ID` set, clearing the gate hands over **two** single-use links
instead of one — the group and the channel — as two buttons under one message.
Both are `member_limit=1` and per person, because a reusable channel link is
forwardable to exactly the people the gate exists to keep out.

The channel is deliberately subordinate to the group:

- **It can never cost someone their admission.** If the channel link fails to mint
  — unset, or the bot has lost admin rights there — that's logged and dropped, and
  they're admitted with the group button alone. Only a failed *group* link fails an
  admission (`_issue_invite`).
- **The intro instruction stays pinned to the group.** A channel is broadcast-only,
  so "post your intro" next to a channel button would send people somewhere they
  can't type. Hence a separate `ADMITTED_WITH_CHANNEL` string rather than a
  bolted-on sentence.
- **Membership there is never read.** No nudges, no roundup, no eligibility check
  touches the channel; being in it means nothing to the gate.
- **It's backfilled, not backdated.** Anyone registered before the channel was
  configured has `channel_invite_link` null. The next time they ask for their link,
  one is minted and stored (`_existing_invite_markup`), so early alumni aren't
  locked out of the channel forever. The backfill never overwrites a link already
  issued, so nobody ends up holding two.

Unset it and the feature is inert: no channel is mentioned anywhere, and the
admission message is the original single-button one.

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
5. `GATE_GROUP_ID` set and `GATE_LIVE=true`. If you're handing over a channel, set
   `GATE_CHANNEL_ID` and make the bot an **admin there with *Invite via link***,
   otherwise every channel link silently fails to mint and people are admitted to
   the group only.
6. Restart, then **run `/gate_announce` inside each real community group.** This
   is what approves them; until then the gate does nothing anywhere and every
   applicant is refused. Check nothing unexpected is queued with `/gate_groups`.
7. **Tap the announcement button yourself first.**

Step 7 matters. Membership checks **fail closed**: if the bot can't read the
alumni group, everyone is treated as a non-member. That's harmless when one person
trickles in by posting; when 200 people tap a pinned message it marks all of them
`nudged`, and the next roundup names them in the group. Verify with your own tap
before announcing.

## Limits worth knowing

- **A true lurker who never joins, posts, or taps stays invisible.** The
  announcement is designed to make the third option nearly frictionless, but it
  can't be forced.
- **A detection nudge usually can't be delivered.** It's a DM, and Telegram only
  lets a bot DM someone who has messaged it first — which a newcomer never has.
  The person is recorded either way, and the welcome tag and roundup are what
  reach them.
- **The welcome tag and the roundup are the group-wide rate-limit risks.** Both
  batch eight mentions per message and pause between batches, because Telegram
  caps a group at roughly 20 messages/minute — that's why a bulk add produces one
  message rather than one per arrival. If you expect a large simultaneous wave,
  add `AIORateLimiter` to the application builder.
- **A welcome tag is lost if the bot restarts inside its window.** The buffer of
  newcomers is in memory on purpose; a persisted queue would replay stale
  welcomes after a redeploy, and the roundup names them anyway.
- **A channel link is only as good as the bot's rights there.** Losing admin in the
  channel doesn't break admissions — it just silently stops the second button
  appearing. `grep "Failed to create Channel invite link"` in the logs is how you
  find out.
- **Invite links don't expire.** They're `member_limit=1`, but a forwarded link
  lets someone else consume the student's slot. Add `expire_date` in
  `_create_invite_link` if that matters.
- **Pinning is best-effort.** Without *Pin messages* rights the announcement still
  posts, just unpinned; clearing the previous one is likewise best-effort.
