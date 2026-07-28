# tests/test_gate_formcheck.py
"""The Airtable client's edge cases, all found in review rather than in the wild."""
from unittest.mock import patch

import pytest

from gate import formcheck as fc
from gate import settings


# ── Form URL construction ───────────────────────────────────────────────────────

def test_personalized_form_url_basic():
    with patch.multiple(settings, FORM_URL="https://airtable.com/shrABC", AIRTABLE_TG_FIELD="tg_id"):
        assert fc.personalized_form_url(555) == (
            "https://airtable.com/shrABC?prefill_tg_id=555&hide_tg_id=true"
        )


def test_personalized_form_url_appends_to_existing_query():
    with patch.multiple(settings, FORM_URL="https://airtable.com/shrABC?x=1", AIRTABLE_TG_FIELD="tg_id"):
        assert fc.personalized_form_url(555) == (
            "https://airtable.com/shrABC?x=1&prefill_tg_id=555&hide_tg_id=true"
        )


def test_personalized_form_url_keeps_params_out_of_the_fragment():
    """Params after a #fragment are swallowed by it, so the prefill would be
    silently ignored and the field would appear blank and unhidden."""
    with patch.multiple(
        settings, FORM_URL="https://airtable.com/app1/pag1/form#frag", AIRTABLE_TG_FIELD="tg_id"
    ):
        url = fc.personalized_form_url(555)
    assert url == (
        "https://airtable.com/app1/pag1/form?prefill_tg_id=555&hide_tg_id=true#frag"
    )
    assert url.index("prefill_tg_id") < url.index("#")


def test_personalized_form_url_encodes_field_names_with_spaces():
    with patch.multiple(
        settings, FORM_URL="https://airtable.com/shrABC", AIRTABLE_TG_FIELD="Telegram ID"
    ):
        assert "prefill_Telegram+ID=555" in fc.personalized_form_url(555)


def test_personalized_form_url_none_without_config():
    with patch.multiple(settings, FORM_URL=""):
        assert fc.personalized_form_url(555) is None


# ── Field-name safety ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,safe",
    [
        ("tg_id", True),
        ("Telegram ID", True),
        ("", False),
        ("tg{id", False),          # would break out of the {field} reference
        ("tg}id", False),
        ("tg'id", False),
        ('tg"id', False),
        ("fldXXXXXXXXXXXXXX", False),  # a field id matches nothing in a formula
    ],
)
def test_field_name_safe(name, safe):
    assert fc._field_name_safe(name) is safe


@pytest.mark.asyncio
async def test_lookup_refuses_unsafe_field_name():
    """Better to fail fast than 422 on every request forever."""
    with patch.multiple(
        settings,
        AIRTABLE_TOKEN="pat",
        AIRTABLE_BASE_ID="app1",
        AIRTABLE_TABLE="T",
        AIRTABLE_TG_FIELD="fldXXXXXXXXXXXXXX",
    ):
        assert await fc.lookup(555) is None


@pytest.mark.asyncio
async def test_lookup_none_when_not_configured():
    with patch.multiple(settings, AIRTABLE_TOKEN="", AIRTABLE_BASE_ID="", AIRTABLE_TABLE=""):
        assert await fc.lookup(555) is None


# ── Completeness ────────────────────────────────────────────────────────────────

def test_complete_without_a_done_field_configured():
    with patch.multiple(settings, AIRTABLE_DONE_FIELD=""):
        assert fc._is_complete({}) is True


@pytest.mark.parametrize(
    "value,complete",
    [
        ("yes", True),
        ("  ", False),
        ("", False),
        (None, False),
        (True, True),
        (False, False),
        ([], False),
        (1, True),
        (0, True),     # a numeric 0 is a real answer, not an empty one
        (0.0, True),
    ],
)
def test_is_complete_with_done_field(value, complete):
    with patch.multiple(settings, AIRTABLE_DONE_FIELD="done"):
        assert fc._is_complete({"done": value}) is complete


def test_is_complete_treats_missing_done_field_as_incomplete():
    with patch.multiple(settings, AIRTABLE_DONE_FIELD="done"):
        assert fc._is_complete({"other": "x"}) is False


# ── Legacy username fallback (for submissions predating tg_id) ───────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@Alecc_lefk", "alecc_lefk"),
        ("Alecc_lefk", "alecc_lefk"),
        ("  @zaraibrgm  ", "zaraibrgm"),
        ("@ssmndr", "ssmndr"),
        ("@Tursunboyev06", "tursunboyev06"),
        ("t.me/alice", "tmealice"),   # stray punctuation dropped
        ("@", None),
        ("", None),
        (None, None),
        ("!!!", None),
    ],
)
def test_normalize_username(raw, expected):
    assert fc._normalize_username(raw) == expected


def test_username_matching_is_case_and_prefix_insensitive():
    """The stored side was hand-typed; the bot's side comes from Telegram."""
    assert fc._username_matches("@Alecc_lefk", "alecc_lefk") is True
    assert fc._username_matches("alecc_lefk", "alecc_lefk") is True
    assert fc._username_matches("  ALECC_LEFK ", "alecc_lefk") is True
    assert fc._username_matches("@someone_else", "alecc_lefk") is False
    assert fc._username_matches(None, "alecc_lefk") is False


def test_normalize_username_strips_formula_breaking_characters():
    """This normalization doubles as the injection guard for the formula."""
    assert fc._normalize_username("a'}{\"\\b") == "ab"


_AIRTABLE_ON = dict(
    AIRTABLE_TOKEN="pat",
    AIRTABLE_BASE_ID="app1",
    AIRTABLE_TABLE="T",
    AIRTABLE_TG_FIELD="tg_id",
    AIRTABLE_DONE_FIELD="",
    AIRTABLE_NAME_FIELD="Full name",
    AIRTABLE_USERNAME_FIELD="Telegram Username",
)


@pytest.mark.asyncio
async def test_lookup_prefers_tg_id_and_skips_the_fallback():
    calls = []

    async def fake_query(formula):
        calls.append(formula)
        return [{"fields": {"tg_id": "555", "Full name": "Ada"}}]

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(fc, "_query", fake_query):
        result = await fc.lookup(555, "alice")

    assert result == {"complete": True, "name": "Ada", "matched_by": "tg_id"}
    assert len(calls) == 1, "should not query the username field once tg_id matched"


@pytest.mark.asyncio
async def test_lookup_falls_back_to_username_for_legacy_rows():
    async def fake_query(formula):
        # No tg_id row; the legacy row only has the typed username.
        if "tg_id" in formula:
            return []
        return [{"fields": {"Telegram Username": "@Alecc_lefk", "Full name": "Alec"}}]

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(fc, "_query", fake_query):
        result = await fc.lookup(555, "alecc_lefk")

    assert result == {"complete": True, "name": "Alec", "matched_by": "username"}


@pytest.mark.asyncio
async def test_lookup_fallback_normalizes_into_the_formula():
    seen = []

    async def fake_query(formula):
        seen.append(formula)
        return [] if "tg_id" in formula else [{"fields": {}}]

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(fc, "_query", fake_query):
        await fc.lookup(555, "@Alecc_lefk")

    legacy = seen[-1]
    assert "LOWER(TRIM(SUBSTITUTE({Telegram Username},'@','')))='alecc_lefk'" in legacy


@pytest.mark.asyncio
async def test_lookup_no_fallback_when_username_field_unset():
    calls = []

    async def fake_query(formula):
        calls.append(formula)
        return []

    with patch.multiple(settings, **{**_AIRTABLE_ON, "AIRTABLE_USERNAME_FIELD": ""}), \
            patch.object(fc, "_query", fake_query):
        result = await fc.lookup(555, "alice")

    assert result["matched_by"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lookup_no_fallback_when_user_has_no_username():
    """Plenty of Telegram accounts have no username — that must not crash."""
    calls = []

    async def fake_query(formula):
        calls.append(formula)
        return []

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(fc, "_query", fake_query):
        result = await fc.lookup(555, None)

    assert result["matched_by"] is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lookup_returns_none_when_fallback_request_fails():
    async def fake_query(formula):
        return [] if "tg_id" in formula else None

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(fc, "_query", fake_query):
        assert await fc.lookup(555, "alice") is None


# ── The poll asks about specific people, not the whole table ─────────────────────

@pytest.mark.asyncio
async def test_poll_queries_only_the_waiting_people():
    """A full-table scan of ~1000 rows is 10 requests; at a 3-minute interval that
    exceeds even a Team workspace's monthly allowance."""
    seen = []

    async def fake_query_all(formula):
        seen.append(formula)
        return [{"fields": {"tg_id": "111", "Full name": "One"}}]

    waiting = [{"user_id": 111, "username": "one"}]
    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(
        fc, "_query_all", fake_query_all
    ):
        result = await fc.fetch_completed_for(waiting)

    assert len(seen) == 1, "one request, regardless of table size"
    assert "OR(" in seen[0]
    assert "{tg_id}&''='111'" in seen[0]
    assert result == {"by_id": {"111": "One"}, "by_username": {}}


@pytest.mark.asyncio
async def test_poll_includes_the_legacy_username_clause():
    seen = []

    async def fake_query_all(formula):
        seen.append(formula)
        return []

    waiting = [{"user_id": 111, "username": "@Alecc_lefk"}]
    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(
        fc, "_query_all", fake_query_all
    ):
        await fc.fetch_completed_for(waiting)

    assert "'alecc_lefk'" in seen[0]


@pytest.mark.asyncio
async def test_poll_chunks_large_cohorts():
    seen = []

    async def fake_query_all(formula):
        seen.append(formula)
        return []

    waiting = [{"user_id": i, "username": None} for i in range(1, 61)]
    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(
        fc, "_query_all", fake_query_all
    ):
        await fc.fetch_completed_for(waiting)

    # 60 people at 25 per request.
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_poll_returns_none_on_failure_never_empty():
    """None means 'couldn't ask'. Returning {} would look like 'nobody has
    submitted' and quietly strand everyone."""
    async def fake_query_all(formula):
        return None

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(
        fc, "_query_all", fake_query_all
    ):
        assert await fc.fetch_completed_for([{"user_id": 1, "username": None}]) is None


@pytest.mark.asyncio
async def test_poll_with_nobody_waiting_makes_no_request():
    called = []

    async def fake_query_all(formula):
        called.append(formula)
        return []

    with patch.multiple(settings, **_AIRTABLE_ON), patch.object(
        fc, "_query_all", fake_query_all
    ):
        assert await fc.fetch_completed_for([]) is None
    assert called == []
