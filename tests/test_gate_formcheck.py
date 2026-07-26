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
