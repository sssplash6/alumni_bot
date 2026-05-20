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
