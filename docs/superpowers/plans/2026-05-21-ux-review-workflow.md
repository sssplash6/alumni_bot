# UX & Review Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add button-based navigation to `/start`, a sequential application review workflow for admins, and multi-admin support.

**Architecture:** `config.py` switches from a single `ADMIN_ID` to `ADMIN_IDS: list[int]`. The `mentors` and `mentees` tables gain a `status` column (`pending`/`approved`/`denied`) migrated automatically on startup. A new `/review` command lets admins approve or deny applications one at a time before `/match` runs on approved applicants only. `/start` shows inline buttons instead of text commands.

**Tech Stack:** Python 3.11+, python-telegram-bot 21.x, aiosqlite, pytest, pytest-asyncio

---

## File Map

| File | Change |
|---|---|
| `config.py` | `ADMIN_ID: int` → `ADMIN_IDS: list[int]` |
| `.env.example` | `ADMIN_ID` → `ADMIN_IDS` |
| `database.py` | Auto-migrate `status` column; add 8 new functions |
| `messages.py` | Start landing strings; review card formatters; review completion; update `status_text()` |
| `bot.py` | Update 4 admin guards; rewrite `start()`; add button entry points to ConversationHandlers; add `admin_review` + `review_decision`; update `admin_status` + `admin_match`; register new handlers |
| `tests/test_database.py` | 6 new tests for status migration and new DB functions |

`matcher.py` and `main.py` are unchanged.

---

## Task 1: Multi-Admin Config

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `bot.py` (4 guard updates + 1 import change)

- [ ] **Step 1: Update config.py**

Replace the entire file with:

```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_IDS: list[int] = [int(x.strip()) for x in os.environ["ADMIN_IDS"].split(",")]
DB_PATH: str = os.environ.get("DB_PATH", str(Path(__file__).parent / "alumni_bot.db"))
```

- [ ] **Step 2: Update .env.example**

Replace the entire file with:

```
BOT_TOKEN=your_token_here
ADMIN_IDS=your_telegram_user_id,other_admin_id
DB_PATH=alumni_bot.db
```

- [ ] **Step 3: Update your local .env file**

Your `.env` currently has `ADMIN_ID=...`. Rename that key to `ADMIN_IDS` (you can list one or two IDs comma-separated). The bot will fail to start until this is done.

- [ ] **Step 4: Update the import in bot.py**

Find line 17 in `bot.py`:

```python
from config import ADMIN_ID, BOT_TOKEN
```

Change to:

```python
from config import ADMIN_IDS, BOT_TOKEN
```

- [ ] **Step 5: Update all 4 admin guards in bot.py**

Find and replace every occurrence of:

```python
if update.effective_user.id != ADMIN_ID:
```

With:

```python
if update.effective_user.id not in ADMIN_IDS:
```

There are exactly 4 occurrences: in `admin_open`, `admin_close`, `admin_status`, `admin_match`.

- [ ] **Step 6: Verify the bot still imports cleanly**

```bash
python3 -c "from bot import build_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add config.py .env.example bot.py
git commit -m "feat: replace ADMIN_ID with ADMIN_IDS list for multi-admin support"
```

---

## Task 2: Database — Status Column Migration + New Functions

**Files:**
- Modify: `database.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_database.py`:

```python
def test_status_column_exists_after_migration(temp_db):
    import aiosqlite

    async def check():
        async with aiosqlite.connect(temp_db) as conn:
            async with conn.execute("PRAGMA table_info(mentors)") as cur:
                mentor_cols = {row[1] for row in await cur.fetchall()}
            async with conn.execute("PRAGMA table_info(mentees)") as cur:
                mentee_cols = {row[1] for row in await cur.fetchall()}
        return mentor_cols, mentee_cols

    mentor_cols, mentee_cols = asyncio.run(check())
    assert "status" in mentor_cols
    assert "status" in mentee_cols


def test_mentor_status_defaults_to_pending(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(
            1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None
        ))
        mentor = asyncio.run(db.get_mentor_by_chat_id(1))
        assert mentor["status"] == "pending"


def test_set_and_get_mentor_status(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(
            1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None
        ))
        asyncio.run(db.set_mentor_status(1, "approved"))
        approved = asyncio.run(db.get_approved_mentors())
        assert len(approved) == 1 and approved[0]["chat_id"] == 1
        pending = asyncio.run(db.get_pending_mentors())
        assert len(pending) == 0


def test_set_and_get_mentee_status(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentee(
            2, "Bob", ["Technology"], "College Student", ["3–5 yrs"], None, "1–2 hrs/week", True
        ))
        asyncio.run(db.set_mentee_status(2, "denied"))
        pending = asyncio.run(db.get_pending_mentees())
        assert len(pending) == 0
        approved = asyncio.run(db.get_approved_mentees())
        assert len(approved) == 0


def test_get_pending_counts(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(
            1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None
        ))
        asyncio.run(db.save_mentor(
            2, "Carol", ["Law"], "6–10 yrs", "3–5 hrs/week", ["University Graduate"], None
        ))
        asyncio.run(db.save_mentee(
            3, "Bob", ["Technology"], "College Student", ["3–5 yrs"], None, "1–2 hrs/week", True
        ))
        asyncio.run(db.set_mentor_status(1, "approved"))
        mentor_pending, mentee_pending = asyncio.run(db.get_pending_counts())
        assert mentor_pending == 1
        assert mentee_pending == 1


def test_get_review_summary(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(
            1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None
        ))
        asyncio.run(db.save_mentor(
            2, "Carol", ["Law"], "6–10 yrs", "3–5 hrs/week", ["University Graduate"], None
        ))
        asyncio.run(db.set_mentor_status(1, "approved"))
        asyncio.run(db.set_mentor_status(2, "denied"))
        asyncio.run(db.save_mentee(
            3, "Bob", ["Technology"], "College Student", ["3–5 yrs"], None, "1–2 hrs/week", True
        ))
        summary = asyncio.run(db.get_review_summary())
        assert summary["mentor_approved"] == 1
        assert summary["mentor_denied"] == 1
        assert summary["mentor_pending"] == 0
        assert summary["mentee_pending"] == 1
        assert summary["mentee_approved"] == 0
        assert summary["mentee_denied"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_database.py -v -k "status or pending or review_summary"
```

Expected: FAIL — `AttributeError` or `AssertionError`

- [ ] **Step 3: Add the status column migration to init_db()**

In `database.py`, after the `await db.commit()` that closes the `CREATE TABLE` block, add these two migration statements (before the final `await db.commit()`):

The new `init_db()` function in full:

```python
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('applications_open', '0')"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mentors (
                chat_id          INTEGER PRIMARY KEY,
                full_name        TEXT NOT NULL,
                spheres          TEXT NOT NULL,
                exp_level        TEXT NOT NULL,
                devote_time      TEXT NOT NULL,
                mentee_exp_prefs TEXT NOT NULL,
                extra            TEXT,
                registered_at    TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mentees (
                chat_id           INTEGER PRIMARY KEY,
                full_name         TEXT NOT NULL,
                spheres           TEXT NOT NULL,
                exp_level         TEXT NOT NULL,
                mentor_exp_prefs  TEXT NOT NULL,
                extra             TEXT,
                devote_time       TEXT NOT NULL,
                consent           INTEGER NOT NULL DEFAULT 0,
                registered_at     TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mentor_chat_id  INTEGER NOT NULL,
                mentee_chat_id  INTEGER NOT NULL,
                score           REAL    NOT NULL,
                matched_at      TEXT    NOT NULL
            )
        """)
        # Migrations: add status column to existing tables if not present
        try:
            await db.execute(
                "ALTER TABLE mentors ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        except Exception:
            pass  # column already exists
        try:
            await db.execute(
                "ALTER TABLE mentees ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        except Exception:
            pass  # column already exists
        await db.commit()
```

- [ ] **Step 4: Append the 8 new database functions to database.py**

```python
async def get_pending_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_pending_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE status = 'pending'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def set_mentor_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mentors SET status = ? WHERE chat_id = ?", (status, chat_id)
        )
        await db.commit()


async def set_mentee_status(chat_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE mentees SET status = ? WHERE chat_id = ?", (status, chat_id)
        )
        await db.commit()


async def get_approved_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE status = 'approved'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_approved_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE status = 'approved'") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def get_pending_counts() -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM mentors WHERE status = 'pending'"
        ) as cur:
            (mentor_pending,) = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM mentees WHERE status = 'pending'"
        ) as cur:
            (mentee_pending,) = await cur.fetchone()
    return mentor_pending, mentee_pending


async def get_review_summary() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM mentors GROUP BY status"
        ) as cur:
            mentor_counts = dict(await cur.fetchall())
        async with db.execute(
            "SELECT status, COUNT(*) FROM mentees GROUP BY status"
        ) as cur:
            mentee_counts = dict(await cur.fetchall())
    return {
        "mentor_approved": mentor_counts.get("approved", 0),
        "mentor_denied":   mentor_counts.get("denied", 0),
        "mentor_pending":  mentor_counts.get("pending", 0),
        "mentee_approved": mentee_counts.get("approved", 0),
        "mentee_denied":   mentee_counts.get("denied", 0),
        "mentee_pending":  mentee_counts.get("pending", 0),
    }
```

- [ ] **Step 5: Run the new tests**

```bash
python3 -m pytest tests/test_database.py -v
```

Expected: All 26 tests PASS (20 existing + 6 new)

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add status column migration and review DB functions"
```

---

## Task 3: Messages — Start Landing + Review Strings

**Files:**
- Modify: `messages.py`

- [ ] **Step 1: Add start landing strings**

After the `DEVOTE_TIME_OPTIONS` line and before `# ── General ──`, insert:

```python
# ── Start landing ─────────────────────────────────────────────────────────────

START_OPEN = (
    "🎓 Welcome to the Alumni Mentorship Program!\n\n"
    "Connect with mentors and mentees from your alumni network.\n\n"
    "Applications are currently open ✅"
)
START_CLOSED = (
    "🎓 Welcome to the Alumni Mentorship Program!\n\n"
    "Applications are currently closed 🔒\n\n"
    "Stay tuned for the next round!"
)
```

- [ ] **Step 2: Add review strings**

After `SAVE_ERROR = ...` and before `# ── Mentor form ──`, insert:

```python
# ── Review ────────────────────────────────────────────────────────────────────

REVIEW_BLOCKED_OPEN = "Close applications first with /close before reviewing."
REVIEW_NO_PENDING = "No pending applications to review."
```

- [ ] **Step 3: Add review card formatters and completion text**

Append after the existing `status_text()` function at the bottom of the file:

```python
def mentor_review_card(mentor: dict, pending_count: int) -> str:
    return (
        f"👤 Mentor — {pending_count} pending\n\n"
        f"Name: {mentor['full_name']}\n"
        f"Sphere(s): {', '.join(mentor['spheres'])}\n"
        f"Experience: {mentor['exp_level']}\n"
        f"Time/week: {mentor['devote_time']}\n"
        f"Open to mentoring: {', '.join(mentor['mentee_exp_prefs'])}\n"
        f"Note: {mentor['extra'] or '—'}"
    )


def mentee_review_card(mentee: dict, pending_count: int) -> str:
    return (
        f"🙋 Mentee — {pending_count} pending\n\n"
        f"Name: {mentee['full_name']}\n"
        f"Sphere(s): {', '.join(mentee['spheres'])}\n"
        f"Experience: {mentee['exp_level']}\n"
        f"Preferred mentor: {', '.join(mentee['mentor_exp_prefs'])}\n"
        f"Time/week: {mentee['devote_time']}\n"
        f"Note: {mentee['extra'] or '—'}"
    )


def review_complete_text(summary: dict) -> str:
    return (
        "✅ Review complete!\n\n"
        f"Mentors: {summary['mentor_approved']} approved · "
        f"{summary['mentor_denied']} denied · "
        f"{summary['mentor_pending']} pending\n"
        f"Mentees: {summary['mentee_approved']} approved · "
        f"{summary['mentee_denied']} denied · "
        f"{summary['mentee_pending']} pending\n\n"
        "Run /match when ready."
    )
```

- [ ] **Step 4: Update status_text() to show pending counts**

Replace the existing `status_text()` function with:

```python
def status_text(
    mentors: int,
    mentees: int,
    matches: int,
    is_open: bool,
    mentor_pending: int = 0,
    mentee_pending: int = 0,
) -> str:
    mentor_str = str(mentors)
    if mentor_pending:
        mentor_str += f" ({mentor_pending} pending review)"
    mentee_str = str(mentees)
    if mentee_pending:
        mentee_str += f" ({mentee_pending} pending review)"
    return (
        "Status\n\n"
        f"Applications: {'Open ✅' if is_open else 'Closed 🔒'}\n"
        f"Registered mentors: {mentor_str}\n"
        f"Registered mentees: {mentee_str}\n"
        f"Existing matches: {matches}"
    )
```

- [ ] **Step 5: Verify import**

```bash
python3 -c "import messages; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add messages.py
git commit -m "feat: start landing strings, review card formatters, updated status_text"
```

---

## Task 4: Bot — Button-Based Start + Admin Status Update

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Rewrite the start() handler**

Find `start()` in `bot.py` (around line 87):

```python
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(msg.START_TEXT)
```

Replace with:

```python
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_open = await db.is_applications_open()
    if is_open:
        await update.message.reply_text(
            msg.START_OPEN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Register as Mentor", callback_data="start:mentor"),
                InlineKeyboardButton("🙋 Register as Mentee", callback_data="start:mentee"),
            ]]),
        )
    else:
        await update.message.reply_text(msg.START_CLOSED)
```

- [ ] **Step 2: Update admin_status() to show pending counts**

Find `admin_status()` in `bot.py` and replace it with:

```python
async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    mentor_count, mentee_count, match_count = await db.get_registration_counts()
    mentor_pending, mentee_pending = await db.get_pending_counts()
    is_open = await db.is_applications_open()
    await update.message.reply_text(
        msg.status_text(mentor_count, mentee_count, match_count, is_open, mentor_pending, mentee_pending)
    )
```

- [ ] **Step 3: Add start:mentor and start:mentee entry points to build_app()**

In `build_app()`, find the `mentor_conv` definition. Change:

```python
    mentor_conv = ConversationHandler(
        entry_points=[CommandHandler("mentor", mentor_start, filters=_private)],
```

To:

```python
    mentor_conv = ConversationHandler(
        entry_points=[
            CommandHandler("mentor", mentor_start, filters=_private),
            CallbackQueryHandler(mentor_start, pattern=r"^start:mentor$"),
        ],
```

Then find the `mentee_conv` definition. Change:

```python
    mentee_conv = ConversationHandler(
        entry_points=[CommandHandler("mentee", mentee_start, filters=_private)],
```

To:

```python
    mentee_conv = ConversationHandler(
        entry_points=[
            CommandHandler("mentee", mentee_start, filters=_private),
            CallbackQueryHandler(mentee_start, pattern=r"^start:mentee$"),
        ],
```

- [ ] **Step 4: Verify import**

```bash
python3 -c "from bot import build_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: button-based start landing and pending counts in /status"
```

---

## Task 5: Bot — Review Command + Updated admin_match

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add _review_kb() helper and admin_review() handler**

Find the `# ── Admin commands ──` section in `bot.py`. Directly before `async def admin_open(...)`, insert:

```python
# ── Review ────────────────────────────────────────────────────────────────────

def _review_kb(role: str, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"review:approve:{role}:{chat_id}"),
        InlineKeyboardButton("❌ Deny", callback_data=f"review:deny:{role}:{chat_id}"),
    ]])


async def admin_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    if await db.is_applications_open():
        await update.message.reply_text(msg.REVIEW_BLOCKED_OPEN)
        return
    pending_mentors = await db.get_pending_mentors()
    pending_mentees = await db.get_pending_mentees()
    if not pending_mentors and not pending_mentees:
        await update.message.reply_text(msg.REVIEW_NO_PENDING)
        return
    if pending_mentors:
        mentor = pending_mentors[0]
        await update.message.reply_text(
            msg.mentor_review_card(mentor, len(pending_mentors)),
            reply_markup=_review_kb("mentor", mentor["chat_id"]),
        )
    else:
        mentee = pending_mentees[0]
        await update.message.reply_text(
            msg.mentee_review_card(mentee, len(pending_mentees)),
            reply_markup=_review_kb("mentee", mentee["chat_id"]),
        )


async def review_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return
    _, action, role, chat_id_str = query.data.split(":")
    chat_id = int(chat_id_str)
    status = "approved" if action == "approve" else "denied"
    try:
        if role == "mentor":
            await db.set_mentor_status(chat_id, status)
        else:
            await db.set_mentee_status(chat_id, status)
    except Exception:
        logger.exception("Failed to set %s status for %s %d", status, role, chat_id)
        await query.edit_message_text("Something went wrong. Please try /review again.")
        return
    pending_mentors = await db.get_pending_mentors()
    pending_mentees = await db.get_pending_mentees()
    if pending_mentors:
        mentor = pending_mentors[0]
        await query.edit_message_text(
            msg.mentor_review_card(mentor, len(pending_mentors)),
            reply_markup=_review_kb("mentor", mentor["chat_id"]),
        )
    elif pending_mentees:
        mentee = pending_mentees[0]
        await query.edit_message_text(
            msg.mentee_review_card(mentee, len(pending_mentees)),
            reply_markup=_review_kb("mentee", mentee["chat_id"]),
        )
    else:
        summary = await db.get_review_summary()
        await query.edit_message_text(msg.review_complete_text(summary))
```

- [ ] **Step 2: Update admin_match() to use approved applicants only**

Find `admin_match()` and replace the lines that fetch mentors/mentees and build the no-match loops. The full updated `admin_match()`:

```python
async def admin_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    if await db.is_applications_open():
        await update.message.reply_text(msg.MATCH_BLOCKED_OPEN)
        return

    _, _, existing_matches = await db.get_registration_counts()
    if existing_matches > 0:
        await update.message.reply_text(msg.MATCH_ALREADY_RAN)
        return

    approved_mentors = await db.get_approved_mentors()
    approved_mentees = await db.get_approved_mentees()
    if not approved_mentors or not approved_mentees:
        await update.message.reply_text(msg.MATCH_BLOCKED_EMPTY)
        return

    matches = run_matching(approved_mentors, approved_mentees)
    await db.save_matches(matches)

    mentor_by_id = {m["chat_id"]: m for m in approved_mentors}
    mentee_by_id = {m["chat_id"]: m for m in approved_mentees}
    matched_mentor_ids = {m[0] for m in matches}
    matched_mentee_ids = {m[1] for m in matches}

    for mentor_id, mentee_id, _ in matches:
        mentee = mentee_by_id[mentee_id]
        mentor = mentor_by_id[mentor_id]
        try:
            await context.bot.send_message(
                chat_id=mentor_id,
                text=msg.mentor_match_text(mentee),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Message your mentee",
                        url=f"tg://user?id={mentee_id}",
                    )
                ]]),
            )
        except Exception:
            logger.exception("Failed to notify mentor %d", mentor_id)
        try:
            await context.bot.send_message(
                chat_id=mentee_id,
                text=msg.mentee_match_text(mentor),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Message your mentor",
                        url=f"tg://user?id={mentor_id}",
                    )
                ]]),
            )
        except Exception:
            logger.exception("Failed to notify mentee %d", mentee_id)

    # Notify unmatched: approved-but-unmatched + denied (not pending)
    all_mentors = await db.get_all_mentors()
    all_mentees = await db.get_all_mentees()

    for mentor in all_mentors:
        if mentor["status"] == "pending":
            continue
        if mentor["chat_id"] not in matched_mentor_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentor["chat_id"], text=msg.NO_MATCH_MENTOR
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentor %d", mentor["chat_id"])

    for mentee in all_mentees:
        if mentee["status"] == "pending":
            continue
        if mentee["chat_id"] not in matched_mentee_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentee["chat_id"], text=msg.NO_MATCH_MENTEE
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentee %d", mentee["chat_id"])

    await update.message.reply_text(
        msg.MATCH_DONE.format(
            matched=len(matches),
            unmatched_mentors=len(approved_mentors) - len(matched_mentor_ids),
            unmatched_mentees=len(approved_mentees) - len(matched_mentee_ids),
        )
    )
```

- [ ] **Step 3: Register the review handlers in build_app()**

In `build_app()`, after the existing `app.add_handler(CommandHandler("match", admin_match, filters=_private))` line, add:

```python
    app.add_handler(CommandHandler("review", admin_review, filters=_private))
    app.add_handler(CallbackQueryHandler(review_decision, pattern=r"^review:"))
```

- [ ] **Step 4: Verify import**

```bash
python3 -c "from bot import build_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run the full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: All 26 tests PASS (20 original + 6 new database tests)

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: /review command with approve/deny queue, admin_match uses approved applicants only"
```
