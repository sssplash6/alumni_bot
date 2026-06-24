# Mentor–Mentee Matching Bot — Design Spec
**Date:** 2026-05-20  
**Project:** `alumni_bot`  
**Platform:** Telegram (python-telegram-bot, ConversationHandler)

---

## Overview

A standalone Telegram bot that collects mentor and mentee registrations during an open application window, then runs a compatibility-scored matching algorithm on admin command. Matches are one-to-one (each mentor gets one mentee) and delivered automatically via bot DM.

---

## Architecture

```
alumni_bot/
├── main.py          # entry point, builds and runs the Application
├── bot.py           # ConversationHandlers + admin commands
├── matcher.py       # matching algorithm (isolated, pure functions)
├── database.py      # async SQLite operations
├── messages.py      # all strings, button labels, option lists
├── config.py        # env vars (BOT_TOKEN, ADMIN_ID)
├── requirements.txt
├── .env
└── .env.example
```

---

## Database Schema

### `settings`
| column | type | notes |
|---|---|---|
| key | TEXT PRIMARY KEY | e.g. `"applications_open"` |
| value | TEXT | `"1"` / `"0"` |

### `mentors`
| column | type | notes |
|---|---|---|
| chat_id | INTEGER PRIMARY KEY | Telegram user ID |
| full_name | TEXT | |
| spheres | TEXT | JSON array of selected spheres |
| exp_level | TEXT | one of the 4 year-interval options |
| devote_time | TEXT | one of the 3 time options |
| mentee_exp_prefs | TEXT | JSON array of preferred mentee levels |
| extra | TEXT | optional free text |
| registered_at | TEXT | ISO timestamp |

### `mentees`
| column | type | notes |
|---|---|---|
| chat_id | INTEGER PRIMARY KEY | Telegram user ID |
| full_name | TEXT | |
| spheres | TEXT | JSON array |
| exp_level | TEXT | one of the 4 mentee level options |
| mentor_exp_prefs | TEXT | JSON array of preferred mentor levels |
| extra | TEXT | optional free text |
| devote_time | TEXT | |
| consent | INTEGER | 1 = agreed |
| registered_at | TEXT | ISO timestamp |

### `matches`
| column | type | notes |
|---|---|---|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| mentor_chat_id | INTEGER | FK → mentors |
| mentee_chat_id | INTEGER | FK → mentees |
| score | REAL | compatibility score 0–100 |
| matched_at | TEXT | ISO timestamp |

---

## Option Lists (defined in `messages.py`)

**Spheres** (multi-select, shared):
`Technology` · `Business/Finance` · `Medicine` · `Law` · `Science/Research` · `Arts/Design` · `Education` · `Other`

**Mentor experience levels** (single select):
`0–2 yrs` · `3–5 yrs` · `6–10 yrs` · `10+ yrs`

**Mentee experience levels** (single select for mentee self, multi-select for mentor preferences):
`New to field` · `HS Graduate` · `College Student` · `University Graduate`

**Devote time** (single select, shared):
`1–2 hrs/week` · `3–5 hrs/week` · `5+ hrs/week`

---

## Conversation Flows

### `/mentor` — 7 steps

| Step | State | Input type | Description |
|---|---|---|---|
| 1 | `MENTOR_NAME` | Free text | Full name |
| 2 | `MENTOR_SPHERE` | Inline checkbox toggles + Done btn | Select spheres (1+) |
| 3 | `MENTOR_EXP` | Inline radio | Experience level |
| 4 | `MENTOR_TIME` | Inline radio | Devote time per week |
| 5 | `MENTOR_MENTEE_PREF` | Inline checkbox toggles + Done btn | Preferred mentee levels |
| 6 | `MENTOR_EXTRA` | Free text or "Skip" button | Anything extra to share |
| 7 | `MENTOR_CONFIRM` | Inline Yes/Edit | Summary + confirm |

### `/mentee` — 8 steps

| Step | State | Input type | Description |
|---|---|---|---|
| 1 | `MENTEE_NAME` | Free text | Full name |
| 2 | `MENTEE_SPHERE` | Inline checkbox toggles + Done btn | Select spheres (1+) |
| 3 | `MENTEE_EXP` | Inline radio | Own experience level |
| 4 | `MENTEE_MENTOR_PREF` | Inline checkbox toggles + Done btn | Preferred mentor levels |
| 5 | `MENTEE_EXTRA` | Free text or "Skip" button | Anything for mentor to know |
| 6 | `MENTEE_TIME` | Inline radio | Devote time per week |
| 6b | `MENTEE_CONSENT` | Inline single checkbox | Consent: "I agree to be matched and contacted by my mentor" |
| 7 | `MENTEE_CONFIRM` | Inline Yes/Edit | Summary + confirm (consent already given — confirm saves to DB) |

**Application gate:** Both flows check `applications_open = "1"` at step 1. If closed, user gets a polite message and the conversation does not start.

**Re-registration:** If a user already has a record, they are told they're already registered. (No editing after submission — admin can clear via DB if needed.)

---

## Admin Commands

All admin commands are restricted to `ADMIN_ID` from config.

| Command | Description |
|---|---|
| `/open` | Sets `applications_open = "1"`. Mentors and mentees can now register. |
| `/close` | Sets `applications_open = "0"`. New registrations are blocked. |
| `/status` | Shows counts: registered mentors, registered mentees, existing matches. |
| `/match` | Runs the matching algorithm and sends DM notifications to all matched pairs. Can only run when applications are closed. |

---

## Matching Algorithm (`matcher.py`)

### Scoring Formula (100 points total)

| Factor | Max pts | Formula |
|---|---|---|
| **Sphere overlap** | 65 | `(len(mentor_spheres ∩ mentee_spheres) / len(mentor_spheres)) * 65` (0 if mentor has no spheres) |
| **Mentee exp match** | 15 | `15` if mentee's exp_level ∈ mentor's mentee_exp_prefs, else `0` |
| **Mentor exp match** | 10 | `10` if mentor's exp_level ∈ mentee's mentor_exp_prefs, else `0` |
| **Time alignment** | 10 | `10` exact match · `5` one tier apart · `0` two+ tiers apart |

### Conflict Resolution (greedy)

```
scores = compute_score_matrix(mentors, mentees)   # dict[(mentor_id, mentee_id)] → float
assignments = {}                                   # mentor_id → mentee_id
available_mentees = set(all mentee ids)

while unassigned mentors remain and available_mentees not empty:
    proposals = {}   # mentee_id → [(score, mentor_id)]
    for mentor in unassigned_mentors:
        best = argmax over available_mentees by scores[(mentor, mentee)]
        proposals[best].append((score, mentor))

    for mentee_id, contenders in proposals.items():
        winner = contender with max score
        assignments[winner.mentor_id] = mentee_id
        available_mentees.remove(mentee_id)
        # losing mentors stay unassigned → next iteration picks their next best
```

Mentors left without a match (when mentees < mentors) receive a "no suitable match found" notification.

### Match Notifications

**To mentor:**
```
You've been matched with a mentee!

Name: {full_name}
Sphere(s): {spheres}
Experience: {exp_level}
Time available: {devote_time}
Message: {extra or "—"}
```
Inline button: `[Message your mentee]` → URL `tg://user?id={mentee_chat_id}`

**To mentee:**
```
Great news — you've been matched with a mentor!

Name: {full_name}
Sphere(s): {spheres}
Experience: {exp_level}
Time available: {devote_time}
Message: {extra or "—"}
```
Inline button: `[Message your mentor]` → URL `tg://user?id={mentor_chat_id}`

---

## Error Handling

- Non-text input during a free-text step → re-prompt with instructions
- User sends `/mentor` or `/mentee` mid-conversation → ignored (existing conversation continues)
- `/match` run while applications are open → blocked with warning
- `/match` run with zero mentors or zero mentees → blocked with informative message
- Telegram send failure during notification → logged, admin notified of failed chat IDs

---

## Testing

- `matcher.py` is pure functions (no DB, no Telegram) → unit-testable directly
- Happy path: equal mentor/mentee counts, clear sphere match
- Conflict case: 2 mentors both score highest on same mentee
- Edge cases: no mentees, no mentors, mentor with no sphere overlap with any mentee
