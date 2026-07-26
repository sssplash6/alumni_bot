# tests/test_gate_helpers.py
"""Pure-function helpers in the gate: mentions, URLs, and join detection."""
from types import SimpleNamespace

import pytest

from gate import handlers as gh


def _member(status):
    return SimpleNamespace(status=status)


def test_mention_escapes_html():
    """A display name is attacker-controlled and goes into an HTML message, so
    it must be escaped or a user could inject markup into the public nudge."""
    out = gh._mention(555, "A<b>&C")
    assert out == '<a href="tg://user?id=555">A&lt;b&gt;&amp;C</a>'


def test_mention_falls_back_when_no_name():
    assert gh._mention(555, None) == '<a href="tg://user?id=555">there</a>'


def test_register_url():
    assert gh._register_url("MyBot") == "https://t.me/MyBot?start=alumni"


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("left", "member", True),
        ("kicked", "member", True),
        ("left", "administrator", True),
        ("member", "administrator", False),  # promotion, not a join
        ("member", "left", False),           # leaving
        ("administrator", "member", False),  # demotion
    ],
)
def test_just_joined(old, new, expected):
    result = SimpleNamespace(
        old_chat_member=_member(old), new_chat_member=_member(new)
    )
    assert gh._just_joined(result) is expected


@pytest.mark.parametrize(
    "raw,expected_none",
    [
        (None, True),
        ("", True),
        ("not a timestamp", True),
        ("2026-07-27T04:00:00+00:00", False),
        ("2026-07-27T04:00:00", False),  # naive is tolerated as UTC
    ],
)
def test_parse_ts(raw, expected_none):
    result = gh._parse_ts(raw)
    assert (result is None) is expected_none
    if result is not None:
        # Always tz-aware, so arithmetic against utcnow() can't raise.
        assert result.tzinfo is not None
