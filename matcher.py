# matcher.py
import logging

_TIME_OPTIONS = ["1–2 hrs/week", "3–5 hrs/week", "5+ hrs/week"]

Match = tuple[int, int, float]  # (mentor_chat_id, mentee_chat_id, score)

logger = logging.getLogger(__name__)


def _time_score(mentor_time: str, mentee_time: str) -> float:
    try:
        mi = _TIME_OPTIONS.index(mentor_time)
        ti = _TIME_OPTIONS.index(mentee_time)
    except ValueError:
        logger.warning("Unknown devote_time value: mentor=%r mentee=%r", mentor_time, mentee_time)
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
