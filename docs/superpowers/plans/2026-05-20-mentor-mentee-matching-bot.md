# Mentor–Mentee Matching Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Telegram bot that collects mentor/mentee registrations via multi-step forms, then pairs them one-to-one by compatibility score on admin command.

**Architecture:** Two `ConversationHandler` flows (`/mentor`, `/mentee`) with inline checkbox/radio keyboards handle multi-step registration. A pure `matcher.py` module scores every mentor–mentee pair and resolves conflicts greedily. An admin runs `/open`, `/close`, and `/match` to control the lifecycle.

**Tech Stack:** Python 3.11+, python-telegram-bot 21.x, aiosqlite, python-dotenv, pytest, pytest-asyncio

---

## File Map

| File | Responsibility |
|---|---|
| `config.py` | Load env vars (BOT_TOKEN, ADMIN_ID, DB_PATH) |
| `database.py` | Async SQLite: schema init, CRUD for mentors/mentees/matches/settings |
| `messages.py` | All user-facing strings, option lists, summary formatters |
| `matcher.py` | Pure scoring + greedy conflict-resolution algorithm |
| `bot.py` | ConversationHandlers, keyboard helpers, admin commands, `build_app()` |
| `main.py` | Entry point: init DB, build app, start polling |
| `tests/test_matcher.py` | Unit tests for `compute_score` and `run_matching` |
| `tests/test_database.py` | Integration tests for DB operations using a temp DB |

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `.env.example`

- [ ] **Step 1: Create requirements.txt**

```
python-telegram-bot==21.10
aiosqlite==0.20.0
python-dotenv==1.0.1
pytest==8.3.5
pytest-asyncio==0.24.0
```

- [ ] **Step 2: Create config.py**

```python
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_ID: int = int(os.environ["ADMIN_ID"])
DB_PATH: str = os.environ.get("DB_PATH", "alumni_bot.db")
```

- [ ] **Step 3: Create .env.example**

```
BOT_TOKEN=your_token_here
ADMIN_ID=your_telegram_user_id
DB_PATH=alumni_bot.db
```

- [ ] **Step 4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: All packages install without errors.

- [ ] **Step 5: Commit**

```bash
git init
git add requirements.txt config.py .env.example
git commit -m "feat: project scaffold with config and deps"
```

---

## Task 2: Database Schema

**Files:**
- Create: `database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write failing test for schema init**

```python
# tests/test_database.py
import asyncio
import pytest
from unittest.mock import patch
import database as db

@pytest.fixture()
def temp_db(tmp_path):
    path = str(tmp_path / "test.db")
    with patch("database.DB_PATH", path):
        asyncio.run(db.init_db())
        yield path

def test_init_creates_tables(temp_db):
    import aiosqlite

    async def check():
        async with aiosqlite.connect(temp_db) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                tables = {row[0] for row in await cur.fetchall()}
        return tables

    tables = asyncio.run(check())
    assert {"settings", "mentors", "mentees", "matches"} <= tables

def test_applications_closed_by_default(temp_db):
    with patch("database.DB_PATH", temp_db):
        result = asyncio.run(db.is_applications_open())
    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_database.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'database'`

- [ ] **Step 3: Implement database.py schema + init**

```python
# database.py
import json
from datetime import datetime, timezone
import aiosqlite
from config import DB_PATH


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
        await db.commit()


async def is_applications_open() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'applications_open'"
        ) as cur:
            row = await cur.fetchone()
    return row is not None and row[0] == "1"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_database.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: database schema and init"
```

---

## Task 3: Database CRUD Operations

**Files:**
- Modify: `database.py` (append functions)
- Modify: `tests/test_database.py` (append tests)

- [ ] **Step 1: Write failing tests for all CRUD operations**

Append to `tests/test_database.py`:

```python
def test_open_close_applications(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.set_applications_open(True))
        assert asyncio.run(db.is_applications_open()) is True
        asyncio.run(db.set_applications_open(False))
        assert asyncio.run(db.is_applications_open()) is False


def test_save_and_retrieve_mentor(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(
            chat_id=1,
            full_name="Alice Smith",
            spheres=["Technology", "Education"],
            exp_level="3–5 yrs",
            devote_time="1–2 hrs/week",
            mentee_exp_prefs=["College Student"],
            extra="Happy to help!",
        ))
        assert asyncio.run(db.is_registered_mentor(1)) is True
        assert asyncio.run(db.is_registered_mentor(999)) is False
        mentor = asyncio.run(db.get_mentor_by_chat_id(1))
        assert mentor["full_name"] == "Alice Smith"
        assert mentor["spheres"] == ["Technology", "Education"]
        assert mentor["mentee_exp_prefs"] == ["College Student"]


def test_save_and_retrieve_mentee(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentee(
            chat_id=2,
            full_name="Bob Jones",
            spheres=["Technology"],
            exp_level="College Student",
            mentor_exp_prefs=["3–5 yrs", "6–10 yrs"],
            extra=None,
            devote_time="3–5 hrs/week",
            consent=True,
        ))
        assert asyncio.run(db.is_registered_mentee(2)) is True
        mentee = asyncio.run(db.get_mentee_by_chat_id(2))
        assert mentee["full_name"] == "Bob Jones"
        assert mentee["mentor_exp_prefs"] == ["3–5 yrs", "6–10 yrs"]
        assert mentee["consent"] == 1


def test_get_all_mentors_and_mentees(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_mentor(1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None))
        asyncio.run(db.save_mentee(2, "Bob", ["Technology"], "College Student", ["3–5 yrs"], None, "1–2 hrs/week", True))
        mentors = asyncio.run(db.get_all_mentors())
        mentees = asyncio.run(db.get_all_mentees())
        assert len(mentors) == 1
        assert len(mentees) == 1
        assert mentors[0]["chat_id"] == 1
        assert mentees[0]["chat_id"] == 2


def test_registration_counts(temp_db):
    with patch("database.DB_PATH", temp_db):
        mentor_count, mentee_count, match_count = asyncio.run(db.get_registration_counts())
        assert mentor_count == 0
        assert mentee_count == 0
        assert match_count == 0
        asyncio.run(db.save_mentor(1, "Alice", ["Technology"], "3–5 yrs", "1–2 hrs/week", ["College Student"], None))
        mentor_count, _, _ = asyncio.run(db.get_registration_counts())
        assert mentor_count == 1


def test_save_matches(temp_db):
    with patch("database.DB_PATH", temp_db):
        asyncio.run(db.save_matches([(1, 2, 87.5), (3, 4, 65.0)]))
        _, _, match_count = asyncio.run(db.get_registration_counts())
        assert match_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_database.py -v
```

Expected: FAIL — functions not defined yet

- [ ] **Step 3: Implement all CRUD functions in database.py**

Append to `database.py`:

```python
async def set_applications_open(open_: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('applications_open', ?)",
            ("1" if open_ else "0",),
        )
        await db.commit()


async def save_mentor(
    chat_id: int,
    full_name: str,
    spheres: list[str],
    exp_level: str,
    devote_time: str,
    mentee_exp_prefs: list[str],
    extra: str | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO mentors
               (chat_id, full_name, spheres, exp_level, devote_time, mentee_exp_prefs, extra, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id, full_name, json.dumps(spheres), exp_level,
                devote_time, json.dumps(mentee_exp_prefs), extra,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def save_mentee(
    chat_id: int,
    full_name: str,
    spheres: list[str],
    exp_level: str,
    mentor_exp_prefs: list[str],
    extra: str | None,
    devote_time: str,
    consent: bool,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO mentees
               (chat_id, full_name, spheres, exp_level, mentor_exp_prefs, extra, devote_time, consent, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id, full_name, json.dumps(spheres), exp_level,
                json.dumps(mentor_exp_prefs), extra, devote_time,
                int(consent), datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def is_registered_mentor(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM mentors WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None


async def is_registered_mentee(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM mentees WHERE chat_id = ?", (chat_id,)) as cur:
            return await cur.fetchone() is not None


def _parse_mentor_row(row: dict) -> dict:
    row["spheres"] = json.loads(row["spheres"])
    row["mentee_exp_prefs"] = json.loads(row["mentee_exp_prefs"])
    return row


def _parse_mentee_row(row: dict) -> dict:
    row["spheres"] = json.loads(row["spheres"])
    row["mentor_exp_prefs"] = json.loads(row["mentor_exp_prefs"])
    return row


async def get_mentor_by_chat_id(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return _parse_mentor_row(dict(row)) if row else None


async def get_mentee_by_chat_id(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
    return _parse_mentee_row(dict(row)) if row else None


async def get_all_mentors() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentors") as cur:
            rows = await cur.fetchall()
    return [_parse_mentor_row(dict(r)) for r in rows]


async def get_all_mentees() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM mentees") as cur:
            rows = await cur.fetchall()
    return [_parse_mentee_row(dict(r)) for r in rows]


async def save_matches(matches: list[tuple[int, int, float]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO matches (mentor_chat_id, mentee_chat_id, score, matched_at) VALUES (?, ?, ?, ?)",
            [(m_id, t_id, score, now) for m_id, t_id, score in matches],
        )
        await db.commit()


async def get_registration_counts() -> tuple[int, int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM mentors") as cur:
            (mentors,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM mentees") as cur:
            (mentees,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM matches") as cur:
            (matches,) = await cur.fetchone()
    return mentors, mentees, matches
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_database.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: database CRUD operations for mentors, mentees, matches"
```

---

## Task 4: Message Strings

**Files:**
- Create: `messages.py`

- [ ] **Step 1: Create messages.py**

```python
# messages.py

SPHERES = [
    "Technology",
    "Business/Finance",
    "Medicine",
    "Law",
    "Science/Research",
    "Arts/Design",
    "Education",
    "Other",
]

MENTOR_EXP_LEVELS = ["0–2 yrs", "3–5 yrs", "6–10 yrs", "10+ yrs"]

MENTEE_EXP_LEVELS = [
    "New to field",
    "HS Graduate",
    "College Student",
    "University Graduate",
]

DEVOTE_TIME_OPTIONS = ["1–2 hrs/week", "3–5 hrs/week", "5+ hrs/week"]

# ── General ──────────────────────────────────────────────────────────────────

START_TEXT = (
    "Welcome! This bot connects mentors with mentees.\n\n"
    "Commands:\n"
    "/mentor — register as a mentor\n"
    "/mentee — register as a mentee\n"
    "/cancel — cancel current registration"
)

APPS_CLOSED = "Applications are currently closed. Stay tuned for the next round!"
ALREADY_REGISTERED = (
    "You're already registered. If you need to make changes, please contact the admin."
)
REGISTRATION_CANCELLED = (
    "Registration cancelled. You can start again with /mentor or /mentee."
)
REGISTRATION_SAVED = (
    "You're registered! We'll notify you when matches are announced. Good luck!"
)
CONSENT_REQUIRED = "You must agree to the terms to complete registration."

# ── Mentor form ───────────────────────────────────────────────────────────────

WELCOME_MENTOR = (
    "Welcome to mentor registration! This takes about 2 minutes.\n\n"
    "First — what's your full name?"
)
ASK_SPHERE = "Which sphere(s) are you in? Select all that apply, then tap Done ✓"
ASK_MENTOR_EXP = "What is your experience level?"
ASK_DEVOTE_TIME = "How much time can you devote per week?"
ASK_MENTEE_PREFS = (
    "What experience level(s) are you open to mentoring?\n"
    "Select all that apply, then tap Done ✓"
)
ASK_EXTRA_MENTOR = (
    "Anything extra you'd like your mentee to know? "
    "(Optional — tap Skip to skip)"
)

# ── Mentee form ───────────────────────────────────────────────────────────────

WELCOME_MENTEE = (
    "Welcome to mentee registration! This takes about 2 minutes.\n\n"
    "First — what's your full name?"
)
ASK_MENTEE_EXP = "What is your current experience level?"
ASK_MENTOR_PREFS = (
    "What experience level(s) would you prefer in a mentor?\n"
    "Select all that apply, then tap Done ✓"
)
ASK_EXTRA_MENTEE = (
    "Anything you'd like your mentor to know ahead of time? "
    "(Optional — tap Skip to skip)"
)
ASK_CONSENT = (
    "Last step: please confirm you agree to be matched with a mentor "
    "and contacted through Telegram."
)

# ── Summaries ─────────────────────────────────────────────────────────────────

def mentor_summary(data: dict) -> str:
    return (
        "Here's your mentor profile:\n\n"
        f"Name: {data['full_name']}\n"
        f"Sphere(s): {', '.join(data['spheres'])}\n"
        f"Experience: {data['exp_level']}\n"
        f"Time per week: {data['devote_time']}\n"
        f"Open to mentoring: {', '.join(data['mentee_exp_prefs'])}\n"
        f"Extra: {data['extra'] or '—'}\n\n"
        "Does this look correct?"
    )


def mentee_summary(data: dict) -> str:
    return (
        "Here's your mentee profile:\n\n"
        f"Name: {data['full_name']}\n"
        f"Sphere(s): {', '.join(data['spheres'])}\n"
        f"Experience: {data['exp_level']}\n"
        f"Preferred mentor level: {', '.join(data['mentor_exp_prefs'])}\n"
        f"Time per week: {data['devote_time']}\n"
        f"Extra: {data['extra'] or '—'}\n\n"
        "Does this look correct?"
    )

# ── Match notifications ───────────────────────────────────────────────────────

def mentor_match_text(mentee: dict) -> str:
    return (
        "You've been matched with a mentee!\n\n"
        f"Name: {mentee['full_name']}\n"
        f"Sphere(s): {', '.join(mentee['spheres'])}\n"
        f"Experience: {mentee['exp_level']}\n"
        f"Time available: {mentee['devote_time']}\n"
        f"Message: {mentee['extra'] or '—'}"
    )


def mentee_match_text(mentor: dict) -> str:
    return (
        "Great news — you've been matched with a mentor!\n\n"
        f"Name: {mentor['full_name']}\n"
        f"Sphere(s): {', '.join(mentor['spheres'])}\n"
        f"Experience: {mentor['exp_level']}\n"
        f"Time available: {mentor['devote_time']}\n"
        f"Message: {mentor['extra'] or '—'}"
    )


NO_MATCH_MENTOR = (
    "Unfortunately, no suitable mentee was available for you this round. "
    "We hope to see you in the next one!"
)
NO_MATCH_MENTEE = (
    "Unfortunately, no mentor slot was available for you this round. "
    "We hope to see you in the next one!"
)

# ── Admin ─────────────────────────────────────────────────────────────────────

APPS_OPENED = (
    "Applications are now open.\n"
    "Mentors: /mentor | Mentees: /mentee"
)
APPS_CLOSED_ADMIN = "Applications are now closed. Run /match to pair mentors with mentees."
MATCH_BLOCKED_OPEN = "Close applications first with /close before running /match."
MATCH_BLOCKED_EMPTY = "Cannot run matching: need at least one mentor and one mentee."
MATCH_DONE = (
    "Matching complete!\n\n"
    "Matched pairs: {matched}\n"
    "Unmatched mentors: {unmatched_mentors}\n"
    "Unmatched mentees: {unmatched_mentees}"
)


def status_text(mentors: int, mentees: int, matches: int, is_open: bool) -> str:
    return (
        "Status\n\n"
        f"Applications: {'Open ✅' if is_open else 'Closed 🔒'}\n"
        f"Registered mentors: {mentors}\n"
        f"Registered mentees: {mentees}\n"
        f"Existing matches: {matches}"
    )
```

- [ ] **Step 2: Commit**

```bash
git add messages.py
git commit -m "feat: message strings and option lists"
```

---

## Task 5: Matching Algorithm (TDD)

**Files:**
- Create: `matcher.py`
- Create: `tests/test_matcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_matcher.py
import pytest
from matcher import compute_score, run_matching


def _mentor(**kw) -> dict:
    return {
        "chat_id": kw.get("chat_id", 1),
        "spheres": kw.get("spheres", ["Technology"]),
        "exp_level": kw.get("exp_level", "3–5 yrs"),
        "devote_time": kw.get("devote_time", "1–2 hrs/week"),
        "mentee_exp_prefs": kw.get("mentee_exp_prefs", ["College Student"]),
    }


def _mentee(**kw) -> dict:
    return {
        "chat_id": kw.get("chat_id", 2),
        "spheres": kw.get("spheres", ["Technology"]),
        "exp_level": kw.get("exp_level", "College Student"),
        "mentor_exp_prefs": kw.get("mentor_exp_prefs", ["3–5 yrs"]),
        "devote_time": kw.get("devote_time", "1–2 hrs/week"),
    }


def test_perfect_match():
    assert compute_score(_mentor(), _mentee()) == 100.0


def test_sphere_overlap_partial():
    mentor = _mentor(spheres=["Technology", "Business/Finance"])
    mentee = _mentee(spheres=["Technology"])
    # sphere: 1/2 * 65 = 32.5, mentee_exp: 15, mentor_exp: 10, time: 10
    assert compute_score(mentor, mentee) == pytest.approx(67.5)


def test_no_sphere_overlap():
    mentor = _mentor(spheres=["Technology"])
    mentee = _mentee(spheres=["Law"])
    # sphere: 0, mentee_exp: 15, mentor_exp: 10, time: 10
    assert compute_score(mentor, mentee) == pytest.approx(35.0)


def test_time_one_tier_apart():
    mentor = _mentor(devote_time="1–2 hrs/week")
    mentee = _mentee(devote_time="3–5 hrs/week")
    # sphere: 65, mentee_exp: 15, mentor_exp: 10, time: 5
    assert compute_score(mentor, mentee) == pytest.approx(95.0)


def test_time_two_tiers_apart():
    mentor = _mentor(devote_time="1–2 hrs/week")
    mentee = _mentee(devote_time="5+ hrs/week")
    # sphere: 65, mentee_exp: 15, mentor_exp: 10, time: 0
    assert compute_score(mentor, mentee) == pytest.approx(90.0)


def test_mentee_exp_not_in_mentor_prefs():
    mentor = _mentor(mentee_exp_prefs=["University Graduate"])
    mentee = _mentee(exp_level="College Student")
    # sphere: 65, mentee_exp: 0, mentor_exp: 10, time: 10
    assert compute_score(mentor, mentee) == pytest.approx(85.0)


def test_mentor_exp_not_in_mentee_prefs():
    mentor = _mentor(exp_level="10+ yrs")
    mentee = _mentee(mentor_exp_prefs=["3–5 yrs"])
    # sphere: 65, mentee_exp: 15, mentor_exp: 0, time: 10
    assert compute_score(mentor, mentee) == pytest.approx(90.0)


def test_run_matching_simple():
    mentor = _mentor(chat_id=1)
    mentee = _mentee(chat_id=2)
    matches = run_matching([mentor], [mentee])
    assert matches == [(1, 2, 100.0)]


def test_run_matching_conflict_resolved_by_score():
    # Both mentors prefer mentee10 (Technology sphere). mentor1 has exact time → 100,
    # mentor2 is one tier off → 95. mentor1 wins; mentor2 falls back to mentee11 (Law).
    mentor1 = _mentor(chat_id=1, devote_time="1–2 hrs/week")
    mentor2 = _mentor(chat_id=2, devote_time="3–5 hrs/week")
    mentee10 = _mentee(chat_id=10, devote_time="1–2 hrs/week", spheres=["Technology"])
    mentee11 = _mentee(chat_id=11, devote_time="1–2 hrs/week", spheres=["Law"])

    matches = run_matching([mentor1, mentor2], [mentee10, mentee11])
    match_map = {m[0]: m[1] for m in matches}
    # mentor1 scores 100 vs mentee10, mentor2 scores 95 — mentor1 wins conflict
    assert match_map[1] == 10
    assert match_map[2] == 11


def test_run_matching_empty_inputs():
    assert run_matching([], []) == []
    assert run_matching([_mentor()], []) == []
    assert run_matching([], [_mentee()]) == []


def test_run_matching_more_mentors_than_mentees():
    mentors = [_mentor(chat_id=i) for i in range(3)]
    mentees = [_mentee(chat_id=10)]
    matches = run_matching(mentors, mentees)
    assert len(matches) == 1
    assert matches[0][1] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_matcher.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'matcher'`

- [ ] **Step 3: Implement matcher.py**

```python
# matcher.py
_TIME_OPTIONS = ["1–2 hrs/week", "3–5 hrs/week", "5+ hrs/week"]

Match = tuple[int, int, float]  # (mentor_chat_id, mentee_chat_id, score)


def _time_score(mentor_time: str, mentee_time: str) -> float:
    try:
        mi = _TIME_OPTIONS.index(mentor_time)
        ti = _TIME_OPTIONS.index(mentee_time)
    except ValueError:
        return 0.0
    diff = abs(mi - ti)
    return 10.0 if diff == 0 else 5.0 if diff == 1 else 0.0


def compute_score(mentor: dict, mentee: dict) -> float:
    mentor_spheres = set(mentor["spheres"])
    mentee_spheres = set(mentee["spheres"])

    sphere_score = (
        len(mentor_spheres & mentee_spheres) / len(mentor_spheres) * 65.0
        if mentor_spheres
        else 0.0
    )
    mentee_exp_score = 15.0 if mentee["exp_level"] in mentor["mentee_exp_prefs"] else 0.0
    mentor_exp_score = 10.0 if mentor["exp_level"] in mentee["mentor_exp_prefs"] else 0.0
    time_score = _time_score(mentor["devote_time"], mentee["devote_time"])

    return sphere_score + mentee_exp_score + mentor_exp_score + time_score


def run_matching(mentors: list[dict], mentees: list[dict]) -> list[Match]:
    if not mentors or not mentees:
        return []

    scores: dict[tuple[int, int], float] = {
        (mentor["chat_id"], mentee["chat_id"]): compute_score(mentor, mentee)
        for mentor in mentors
        for mentee in mentees
    }

    assignments: list[Match] = []
    unassigned = list(mentors)
    available: set[int] = {m["chat_id"] for m in mentees}

    while unassigned and available:
        proposals: dict[int, list[tuple[float, dict]]] = {}
        for mentor in unassigned:
            best_id = max(available, key=lambda mid: scores[(mentor["chat_id"], mid)])
            proposals.setdefault(best_id, []).append(
                (scores[(mentor["chat_id"], best_id)], mentor)
            )

        next_unassigned: list[dict] = []
        for mentee_id, contenders in proposals.items():
            winner_score, winner = max(contenders, key=lambda x: x[0])
            assignments.append((winner["chat_id"], mentee_id, winner_score))
            available.discard(mentee_id)
            for score, mentor in contenders:
                if mentor["chat_id"] != winner["chat_id"]:
                    next_unassigned.append(mentor)

        unassigned = next_unassigned

    return assignments
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_matcher.py -v
```

Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add matcher.py tests/test_matcher.py
git commit -m "feat: matching algorithm with greedy conflict resolution"
```

---

## Task 6: Bot Keyboard Helpers and State Constants

**Files:**
- Create: `bot.py` (initial skeleton)

- [ ] **Step 1: Create bot.py with imports, state constants, and keyboard helpers**

```python
# bot.py
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import messages as msg
from config import ADMIN_ID, BOT_TOKEN
from matcher import run_matching

logger = logging.getLogger(__name__)

# ── State constants ────────────────────────────────────────────────────────────

(
    MENTOR_NAME,
    MENTOR_SPHERE,
    MENTOR_EXP,
    MENTOR_TIME,
    MENTOR_MENTEE_PREF,
    MENTOR_EXTRA,
    MENTOR_CONFIRM,
) = range(7)

(
    MENTEE_NAME,
    MENTEE_SPHERE,
    MENTEE_EXP,
    MENTEE_MENTOR_PREF,
    MENTEE_EXTRA,
    MENTEE_TIME,
    MENTEE_CONSENT,
    MENTEE_CONFIRM,
) = range(7, 15)


# ── Keyboard helpers ──────────────────────────────────────────────────────────

def _checkbox_kb(options: list[str], selected: set, prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if opt in selected else '☑️'} {opt}",
            callback_data=f"toggle:{prefix}:{opt}",
        )]
        for opt in options
    ]
    rows.append([InlineKeyboardButton("Done ✓", callback_data=f"done:{prefix}")])
    return InlineKeyboardMarkup(rows)


def _radio_kb(options: list[str], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(opt, callback_data=f"select:{prefix}:{opt}")]
        for opt in options
    ])


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm:yes"),
        InlineKeyboardButton("✏️ Start over", callback_data="confirm:no"),
    ]])


def _consent_kb(agreed: bool) -> InlineKeyboardMarkup:
    mark = "✅" if agreed else "☑️"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{mark} I agree to be matched and contacted by my mentor",
            callback_data="consent:toggle",
        )],
        [InlineKeyboardButton("Continue →", callback_data="consent:done")],
    ])
```

- [ ] **Step 2: Verify import works**

```bash
python -c "import bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: bot skeleton with state constants and keyboard helpers"
```

---

## Task 7: Mentor ConversationHandler

**Files:**
- Modify: `bot.py` (append mentor handlers + ConversationHandler builder stubs)

- [ ] **Step 1: Append the mentor flow handlers to bot.py**

```python
# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(msg.START_TEXT)


# ── Mentor flow ───────────────────────────────────────────────────────────────

async def mentor_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentor(chat_id):
        await update.message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(msg.WELCOME_MENTOR)
    return MENTOR_NAME


async def mentor_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = update.message.text.strip()
    context.user_data["spheres"] = set()
    await update.message.reply_text(
        msg.ASK_SPHERE,
        reply_markup=_checkbox_kb(msg.SPHERES, set(), "msphere"),
    )
    return MENTOR_SPHERE


async def mentor_sphere_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sphere = query.data.split(":", 2)[2]
    selected: set = context.user_data.setdefault("spheres", set())
    selected.symmetric_difference_update({sphere})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.SPHERES, selected, "msphere")
    )
    return MENTOR_SPHERE


async def mentor_sphere_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("spheres"):
        await query.answer("Please select at least one sphere.", show_alert=True)
        return MENTOR_SPHERE
    await query.answer()
    await query.edit_message_text(
        msg.ASK_MENTOR_EXP,
        reply_markup=_radio_kb(msg.MENTOR_EXP_LEVELS, "mexp"),
    )
    return MENTOR_EXP


async def mentor_got_exp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["exp_level"] = query.data.split(":", 2)[2]
    await query.edit_message_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "mtime"),
    )
    return MENTOR_TIME


async def mentor_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["devote_time"] = query.data.split(":", 2)[2]
    context.user_data["mentee_exp_prefs"] = set()
    await query.edit_message_text(
        msg.ASK_MENTEE_PREFS,
        reply_markup=_checkbox_kb(msg.MENTEE_EXP_LEVELS, set(), "mmenteeexp"),
    )
    return MENTOR_MENTEE_PREF


async def mentor_mentee_pref_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    level = query.data.split(":", 2)[2]
    selected: set = context.user_data.setdefault("mentee_exp_prefs", set())
    selected.symmetric_difference_update({level})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.MENTEE_EXP_LEVELS, selected, "mmenteeexp")
    )
    return MENTOR_MENTEE_PREF


async def mentor_mentee_pref_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("mentee_exp_prefs"):
        await query.answer("Please select at least one preference.", show_alert=True)
        return MENTOR_MENTEE_PREF
    await query.answer()
    await query.edit_message_text(
        msg.ASK_EXTRA_MENTOR,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Skip", callback_data="extra:skip")]]
        ),
    )
    return MENTOR_EXTRA


async def mentor_got_extra_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["extra"] = update.message.text.strip()
    data = _serialize_sets(context.user_data)
    await update.message.reply_text(msg.mentor_summary(data), reply_markup=_confirm_kb())
    return MENTOR_CONFIRM


async def mentor_extra_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["extra"] = None
    data = _serialize_sets(context.user_data)
    await query.edit_message_text(msg.mentor_summary(data), reply_markup=_confirm_kb())
    return MENTOR_CONFIRM


async def mentor_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text(msg.REGISTRATION_CANCELLED)
        return ConversationHandler.END
    data = context.user_data
    await db.save_mentor(
        chat_id=update.effective_chat.id,
        full_name=data["full_name"],
        spheres=list(data["spheres"]),
        exp_level=data["exp_level"],
        devote_time=data["devote_time"],
        mentee_exp_prefs=list(data["mentee_exp_prefs"]),
        extra=data.get("extra"),
    )
    await query.edit_message_text(msg.REGISTRATION_SAVED)
    return ConversationHandler.END


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _serialize_sets(data: dict) -> dict:
    """Convert any set values to sorted lists so summary formatters can join them."""
    return {
        k: sorted(v) if isinstance(v, set) else v
        for k, v in data.items()
    }


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(msg.REGISTRATION_CANCELLED)
    return ConversationHandler.END
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python -c "import bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: mentor ConversationHandler flow"
```

---

## Task 8: Mentee ConversationHandler

**Files:**
- Modify: `bot.py` (append mentee handlers)

- [ ] **Step 1: Append the mentee flow handlers to bot.py**

```python
# ── Mentee flow ───────────────────────────────────────────────────────────────

async def mentee_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if not await db.is_applications_open():
        await update.message.reply_text(msg.APPS_CLOSED)
        return ConversationHandler.END
    if await db.is_registered_mentee(chat_id):
        await update.message.reply_text(msg.ALREADY_REGISTERED)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(msg.WELCOME_MENTEE)
    return MENTEE_NAME


async def mentee_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = update.message.text.strip()
    context.user_data["spheres"] = set()
    await update.message.reply_text(
        msg.ASK_SPHERE,
        reply_markup=_checkbox_kb(msg.SPHERES, set(), "tsphere"),
    )
    return MENTEE_SPHERE


async def mentee_sphere_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sphere = query.data.split(":", 2)[2]
    selected: set = context.user_data.setdefault("spheres", set())
    selected.symmetric_difference_update({sphere})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.SPHERES, selected, "tsphere")
    )
    return MENTEE_SPHERE


async def mentee_sphere_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("spheres"):
        await query.answer("Please select at least one sphere.", show_alert=True)
        return MENTEE_SPHERE
    await query.answer()
    await query.edit_message_text(
        msg.ASK_MENTEE_EXP,
        reply_markup=_radio_kb(msg.MENTEE_EXP_LEVELS, "texp"),
    )
    return MENTEE_EXP


async def mentee_got_exp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["exp_level"] = query.data.split(":", 2)[2]
    context.user_data["mentor_exp_prefs"] = set()
    await query.edit_message_text(
        msg.ASK_MENTOR_PREFS,
        reply_markup=_checkbox_kb(msg.MENTOR_EXP_LEVELS, set(), "tmentorexp"),
    )
    return MENTEE_MENTOR_PREF


async def mentee_mentor_pref_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    level = query.data.split(":", 2)[2]
    selected: set = context.user_data.setdefault("mentor_exp_prefs", set())
    selected.symmetric_difference_update({level})
    await query.edit_message_reply_markup(
        reply_markup=_checkbox_kb(msg.MENTOR_EXP_LEVELS, selected, "tmentorexp")
    )
    return MENTEE_MENTOR_PREF


async def mentee_mentor_pref_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("mentor_exp_prefs"):
        await query.answer("Please select at least one preference.", show_alert=True)
        return MENTEE_MENTOR_PREF
    await query.answer()
    await query.edit_message_text(
        msg.ASK_EXTRA_MENTEE,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Skip", callback_data="extra:skip")]]
        ),
    )
    return MENTEE_EXTRA


async def mentee_got_extra_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["extra"] = update.message.text.strip()
    await update.message.reply_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "ttime"),
    )
    return MENTEE_TIME


async def mentee_extra_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["extra"] = None
    await query.edit_message_text(
        msg.ASK_DEVOTE_TIME,
        reply_markup=_radio_kb(msg.DEVOTE_TIME_OPTIONS, "ttime"),
    )
    return MENTEE_TIME


async def mentee_got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["devote_time"] = query.data.split(":", 2)[2]
    context.user_data["consent"] = False
    await query.edit_message_text(
        msg.ASK_CONSENT,
        reply_markup=_consent_kb(False),
    )
    return MENTEE_CONSENT


async def mentee_consent_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["consent"] = not context.user_data.get("consent", False)
    await query.edit_message_reply_markup(
        reply_markup=_consent_kb(context.user_data["consent"])
    )
    return MENTEE_CONSENT


async def mentee_consent_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not context.user_data.get("consent"):
        await query.answer(msg.CONSENT_REQUIRED, show_alert=True)
        return MENTEE_CONSENT
    await query.answer()
    data = _serialize_sets(context.user_data)
    await query.edit_message_text(msg.mentee_summary(data), reply_markup=_confirm_kb())
    return MENTEE_CONFIRM


async def mentee_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        await query.edit_message_text(msg.REGISTRATION_CANCELLED)
        return ConversationHandler.END
    data = context.user_data
    await db.save_mentee(
        chat_id=update.effective_chat.id,
        full_name=data["full_name"],
        spheres=list(data["spheres"]),
        exp_level=data["exp_level"],
        mentor_exp_prefs=list(data["mentor_exp_prefs"]),
        extra=data.get("extra"),
        devote_time=data["devote_time"],
        consent=data["consent"],
    )
    await query.edit_message_text(msg.REGISTRATION_SAVED)
    return ConversationHandler.END
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python -c "import bot; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: mentee ConversationHandler flow"
```

---

## Task 9: Admin Commands + build_app()

**Files:**
- Modify: `bot.py` (append admin handlers and build_app)

- [ ] **Step 1: Append admin command handlers and build_app to bot.py**

```python
# ── Admin commands ─────────────────────────────────────────────────────────────

async def admin_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await db.set_applications_open(True)
    await update.message.reply_text(msg.APPS_OPENED)


async def admin_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await db.set_applications_open(False)
    await update.message.reply_text(msg.APPS_CLOSED_ADMIN)


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    mentor_count, mentee_count, match_count = await db.get_registration_counts()
    is_open = await db.is_applications_open()
    await update.message.reply_text(
        msg.status_text(mentor_count, mentee_count, match_count, is_open)
    )


async def admin_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    if await db.is_applications_open():
        await update.message.reply_text(msg.MATCH_BLOCKED_OPEN)
        return

    mentors = await db.get_all_mentors()
    mentees = await db.get_all_mentees()
    if not mentors or not mentees:
        await update.message.reply_text(msg.MATCH_BLOCKED_EMPTY)
        return

    matches = run_matching(mentors, mentees)
    await db.save_matches(matches)

    mentor_by_id = {m["chat_id"]: m for m in mentors}
    mentee_by_id = {m["chat_id"]: m for m in mentees}
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

    for mentor in mentors:
        if mentor["chat_id"] not in matched_mentor_ids:
            try:
                await context.bot.send_message(
                    chat_id=mentor["chat_id"], text=msg.NO_MATCH_MENTOR
                )
            except Exception:
                logger.exception("Failed to notify unmatched mentor %d", mentor["chat_id"])

    for mentee in mentees:
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
            unmatched_mentors=len(mentors) - len(matched_mentor_ids),
            unmatched_mentees=len(mentees) - len(matched_mentee_ids),
        )
    )


# ── App builder ────────────────────────────────────────────────────────────────

def build_app() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    _private = filters.ChatType.PRIVATE

    mentor_conv = ConversationHandler(
        entry_points=[CommandHandler("mentor", mentor_start, filters=_private)],
        states={
            MENTOR_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentor_got_name)],
            MENTOR_SPHERE: [
                CallbackQueryHandler(mentor_sphere_toggle, pattern=r"^toggle:msphere:"),
                CallbackQueryHandler(mentor_sphere_done, pattern=r"^done:msphere$"),
            ],
            MENTOR_EXP: [CallbackQueryHandler(mentor_got_exp, pattern=r"^select:mexp:")],
            MENTOR_TIME: [CallbackQueryHandler(mentor_got_time, pattern=r"^select:mtime:")],
            MENTOR_MENTEE_PREF: [
                CallbackQueryHandler(mentor_mentee_pref_toggle, pattern=r"^toggle:mmenteeexp:"),
                CallbackQueryHandler(mentor_mentee_pref_done, pattern=r"^done:mmenteeexp$"),
            ],
            MENTOR_EXTRA: [
                MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentor_got_extra_text),
                CallbackQueryHandler(mentor_extra_skip, pattern=r"^extra:skip$"),
            ],
            MENTOR_CONFIRM: [CallbackQueryHandler(mentor_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    mentee_conv = ConversationHandler(
        entry_points=[CommandHandler("mentee", mentee_start, filters=_private)],
        states={
            MENTEE_NAME: [MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentee_got_name)],
            MENTEE_SPHERE: [
                CallbackQueryHandler(mentee_sphere_toggle, pattern=r"^toggle:tsphere:"),
                CallbackQueryHandler(mentee_sphere_done, pattern=r"^done:tsphere$"),
            ],
            MENTEE_EXP: [CallbackQueryHandler(mentee_got_exp, pattern=r"^select:texp:")],
            MENTEE_MENTOR_PREF: [
                CallbackQueryHandler(mentee_mentor_pref_toggle, pattern=r"^toggle:tmentorexp:"),
                CallbackQueryHandler(mentee_mentor_pref_done, pattern=r"^done:tmentorexp$"),
            ],
            MENTEE_EXTRA: [
                MessageHandler(_private & filters.TEXT & ~filters.COMMAND, mentee_got_extra_text),
                CallbackQueryHandler(mentee_extra_skip, pattern=r"^extra:skip$"),
            ],
            MENTEE_TIME: [CallbackQueryHandler(mentee_got_time, pattern=r"^select:ttime:")],
            MENTEE_CONSENT: [
                CallbackQueryHandler(mentee_consent_toggle, pattern=r"^consent:toggle$"),
                CallbackQueryHandler(mentee_consent_done, pattern=r"^consent:done$"),
            ],
            MENTEE_CONFIRM: [CallbackQueryHandler(mentee_confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=_private)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start, filters=_private))
    app.add_handler(mentor_conv)
    app.add_handler(mentee_conv)
    app.add_handler(CommandHandler("open", admin_open, filters=_private))
    app.add_handler(CommandHandler("close", admin_close, filters=_private))
    app.add_handler(CommandHandler("status", admin_status, filters=_private))
    app.add_handler(CommandHandler("match", admin_match, filters=_private))

    return app
```

- [ ] **Step 2: Verify no syntax errors**

```bash
python -c "from bot import build_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: admin commands and build_app wiring"
```

---

## Task 10: Entry Point + Full Test Run

**Files:**
- Create: `main.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create tests/__init__.py**

```python
```

(empty file — makes `tests/` a package so imports resolve correctly)

- [ ] **Step 2: Create main.py**

```python
# main.py
import asyncio
import logging

from bot import build_app
from database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def main() -> None:
    await init_db()
    app = build_app()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: All tests PASS (matcher + database tests, ~19 total)

- [ ] **Step 4: Smoke test — verify bot starts (requires .env with real token)**

```bash
python main.py
```

Expected: Bot starts polling, no exceptions. Send `/start` in Telegram — bot replies with the welcome message. Send `/mentor` — bot asks for name. Press Ctrl-C to stop.

- [ ] **Step 5: Final commit**

```bash
git add main.py tests/__init__.py
git commit -m "feat: entry point and complete mentor-mentee matching bot"
```
