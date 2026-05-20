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
    assert len(matches) == 2
    # mentor1 scored 100.0 (exact time), mentor2 scored 95.0 (one tier off) on mentee11
    match_scores = {m[0]: m[2] for m in matches}
    assert match_scores[1] == pytest.approx(100.0)
    assert match_scores[2] == pytest.approx(30.0)


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


def test_unknown_devote_time_returns_zero_time_score():
    mentor = _mentor(devote_time="unknown")
    mentee = _mentee(devote_time="1–2 hrs/week")
    # sphere: 65, mentee_exp: 15, mentor_exp: 10, time: 0 (unknown → warning, 0pts)
    assert compute_score(mentor, mentee) == pytest.approx(90.0)
