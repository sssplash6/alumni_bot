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
