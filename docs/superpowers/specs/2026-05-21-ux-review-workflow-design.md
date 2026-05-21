# UX & Review Workflow — Design Spec
**Date:** 2026-05-21
**Project:** `alumni_bot`
**Platform:** Telegram (python-telegram-bot, ConversationHandler)

---

## Overview

Two improvements to the working mentor-mentee matching bot:

1. **Button-based navigation** — `/start` presents a landing with inline buttons so users never need to type commands.
2. **Application review workflow** — after applications close, both admins review each submission one at a time and approve or deny before matching runs. Only approved applicants enter the matching pool.

---

## Multi-Admin Support

### Config change
`ADMIN_ID: int` is replaced by `ADMIN_IDS: list[int]`, loaded from a comma-separated env var:

```
ADMIN_IDS=123456789,987654321
```

Both IDs have identical access to all admin commands (`/open`, `/close`, `/status`, `/review`, `/match`).

All existing `update.effective_user.id != ADMIN_ID` guards are updated to `update.effective_user.id not in ADMIN_IDS`.

---

## Database Changes

### `status` column on `mentors` and `mentees`

Both tables gain:

```sql
status TEXT NOT NULL DEFAULT 'pending'
```

Valid values: `pending` | `approved` | `denied`

- All new registrations default to `pending`.
- `init_db()` runs the migration automatically on startup via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — no manual DB work required.

### New database functions

| Function | Description |
|---|---|
| `get_pending_mentors() -> list[dict]` | All mentors with `status = 'pending'` |
| `get_pending_mentees() -> list[dict]` | All mentees with `status = 'pending'` |
| `set_mentor_status(chat_id, status)` | Set mentor's status |
| `set_mentee_status(chat_id, status)` | Set mentee's status |
| `get_approved_mentors() -> list[dict]` | All mentors with `status = 'approved'` |
| `get_approved_mentees() -> list[dict]` | All mentees with `status = 'approved'` |

`get_registration_counts()` signature is unchanged. A new `get_pending_counts() -> tuple[int, int]` returns `(pending_mentor_count, pending_mentee_count)`. The `/status` handler calls both and displays pending counts alongside totals:

```
Status

Applications: Open ✅
Registered mentors: 10 (3 pending review)
Registered mentees: 14 (5 pending review)
Existing matches: 0
```

---

## Button-Based Navigation

### `/start` landing

The `/start` handler checks `is_applications_open()` and replies with a context-aware message:

**When open:**
```
🎓 Welcome to the Alumni Mentorship Program!

Connect with mentors and mentees from your alumni network.

Applications are currently open ✅

[📋 Register as Mentor]  [🙋 Register as Mentee]
```

**When closed:**
```
🎓 Welcome to the Alumni Mentorship Program!

Applications are currently closed 🔒

Stay tuned for the next round!
```

### Implementation

Both buttons use `CallbackQueryHandler` entry points added to the existing `ConversationHandler` `entry_points` lists:

```python
# mentor_conv entry_points:
CommandHandler("mentor", mentor_start, filters=_private),
CallbackQueryHandler(mentor_start, pattern=r"^start:mentor$"),

# mentee_conv entry_points:
CommandHandler("mentee", mentee_start, filters=_private),
CallbackQueryHandler(mentee_start, pattern=r"^start:mentee$"),
```

Slash commands remain functional as a fallback. No changes to form flows.

---

## Review Workflow

### Lifecycle

```
/open → registrations → /close → /review (approve/deny) → /match (approved only)
```

### `/review` command

Available to both admin IDs. Works through all pending applications sequentially — mentors first, then mentees. Each card is shown one at a time in a single message that updates in-place.

**Profile card (mentor):**
```
👤 Mentor — 4 pending

Name: Alice Smith
Sphere(s): Technology, Education
Experience: 3–5 yrs
Time/week: 1–2 hrs/week
Open to mentoring: College Student, University Graduate
Note: Happy to help!

[✅ Approve]  [❌ Deny]
```

**Profile card (mentee):**
```
🙋 Mentee — 7 pending

Name: Bob Jones
Sphere(s): Technology
Experience: College Student
Preferred mentor: 3–5 yrs, 6–10 yrs
Time/week: 3–5 hrs/week
Note: —

[✅ Approve]  [❌ Deny]
```

### Callback data format

Approve/Deny buttons use a single pattern: `review:{action}:{role}:{chat_id}`, e.g.:
- `review:approve:mentor:123456789`
- `review:deny:mentee:987654321`

A single `CallbackQueryHandler` with pattern `^review:` parses the action, role, and target chat_id from the data.

### Approve / Deny behaviour

- Tapping either button immediately calls `set_mentor_status()` or `set_mentee_status()` to update the DB.
- The message edits in-place to show the next pending application.
- Both admins can review simultaneously. Since decisions are written immediately, the last tap on any contested card wins.
- `/review` can be re-run at any time to catch newly pending applications.

### Completion message

When no pending applications remain:

```
✅ Review complete!

Mentors: {approved} approved · {denied} denied · {pending} pending
Mentees: {approved} approved · {denied} denied · {pending} pending

Run /match when ready.
```

If there are no pending applications at all when `/review` is called:

```
No pending applications to review.
```

### `/match` change

`admin_match` calls `get_approved_mentors()` and `get_approved_mentees()` instead of `get_all_mentors()` / `get_all_mentees()`. Denied and pending applicants are excluded from the matching pool.

Denied applicants receive `NO_MATCH_MENTOR` / `NO_MATCH_MENTEE` at the end of `/match` — same as unmatched approved applicants. They are not told they were denied.

The `/match` idempotency guard (`MATCH_ALREADY_RAN`) remains in place.

---

## Files Changed

| File | Change |
|---|---|
| `config.py` | `ADMIN_ID` → `ADMIN_IDS: list[int]` |
| `database.py` | Add `status` column migration; add 6 new functions; update `get_registration_counts()` |
| `messages.py` | New `START_OPEN` / `START_CLOSED` strings; `mentor_review_card()` and `mentee_review_card()` formatters; review completion strings |
| `bot.py` | Update all admin guards; rewrite `start()`; add `start:mentor` / `start:mentee` callback entry points; add `admin_review`, `review_approve_mentor`, `review_deny_mentor`, `review_approve_mentee`, `review_deny_mentee` handlers; update `admin_match` to use approved lists |
| `tests/test_database.py` | Tests for new status functions and migration |

`matcher.py` and `main.py` are unchanged.

---

## Error Handling

- `/review` called while applications are open → blocked: "Close applications first before reviewing."
- `/match` called with zero approved mentors or mentees → blocked with `MATCH_BLOCKED_EMPTY`.
- DB failure during approve/deny → logged, admin sees a generic error message; card is not advanced so the admin can retry.
