"""Airtable lookups for the Alumni Gate's onboarding-form verification.

A student counts as "form complete" when a row exists in the configured Airtable
table whose Telegram-ID field matches their Telegram user ID. The bot pre-fills
that field into each student's personal form link (see ``personalized_form_url``),
so the match is exact and the student never has to type or paste anything.

Two entry points:
  * ``lookup(tg_id)``     — check one student now (the "I've completed the form"
                            button). Targeted query, fast.
  * ``fetch_completed()`` — one paginated pass over the table returning every
                            completed submission, for the background poll.

Both return ``None`` when Airtable isn't configured or a request fails — callers
treat that as "couldn't verify" rather than "not complete", so setup and outage
problems never silently admit or reject people.
"""
import logging
from urllib.parse import quote, quote_plus

import httpx

from . import settings

logger = logging.getLogger(__name__)

_API = "https://api.airtable.com/v0"
_TIMEOUT = 15.0

# Characters that would break out of the `{field}` reference in a formula. The
# field name is operator-supplied, so a stray one of these would 422 every
# request forever — better to refuse loudly at the boundary.
_UNSAFE_FIELD_CHARS = ("{", "}", "'", '"', "\\")


def _field_name_safe(name: str) -> bool:
    """Whether a configured field name can be embedded in a formula.

    Also rejects raw field IDs: filterByFormula accepts field *names* only, so a
    `fld…` id silently matches nothing rather than erroring.
    """
    if not name or any(c in name for c in _UNSAFE_FIELD_CHARS):
        return False
    return not (name.startswith("fld") and len(name) == 17)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.AIRTABLE_TOKEN}"}


def _table_url() -> str:
    # The table name/id sits in the path and may contain spaces or slashes.
    return f"{_API}/{settings.AIRTABLE_BASE_ID}/{quote(settings.AIRTABLE_TABLE, safe='')}"


def _log_http_hint(resp: "httpx.Response") -> None:
    """Turn a failing status into a specific log line.

    Callers collapse every failure into "couldn't verify", which is the right
    behaviour for students but means a bad token, a missing field and a quota
    wall all look identical from the outside. The log is the only place the
    difference is recoverable, so make it say which one it was.
    """
    if resp.status_code < 400:
        return
    hints = {
        401: "GATE_AIRTABLE_TOKEN is invalid or revoked.",
        403: "Token lacks data.records:read, or this base isn't in its Access list.",
        404: "GATE_AIRTABLE_BASE_ID or GATE_AIRTABLE_TABLE is wrong.",
        422: (
            "Airtable rejected the request — usually a field name that doesn't "
            "exist (check GATE_AIRTABLE_TG_FIELD / _NAME_FIELD / _DONE_FIELD "
            "against the table, including exact capitalisation)."
        ),
        429: (
            "Rate limited or the workspace's monthly API allowance is exhausted. "
            "Until it resets, nobody can be verified."
        ),
    }
    logger.error(
        "Airtable returned HTTP %d: %s Body: %.300s",
        resp.status_code,
        hints.get(resp.status_code, "Unexpected status."),
        resp.text,
    )


def _wanted_fields() -> list[str]:
    fields = [settings.AIRTABLE_TG_FIELD]
    if settings.AIRTABLE_NAME_FIELD:
        fields.append(settings.AIRTABLE_NAME_FIELD)
    if settings.AIRTABLE_DONE_FIELD:
        fields.append(settings.AIRTABLE_DONE_FIELD)
    if settings.AIRTABLE_USERNAME_FIELD:
        fields.append(settings.AIRTABLE_USERNAME_FIELD)
    return fields


def _normalize_username(username: str | None) -> str | None:
    """Reduce a Telegram username to a comparable form, or None if unusable.

    Legacy rows were typed by hand, so they carry a mix of "@alice", "alice" and
    "Alice". Telegram usernames are 5-32 chars of [A-Za-z0-9_] and are
    case-insensitive, so lowercasing and dropping everything else is lossless for
    a real username — and it doubles as the guard that keeps this value safe to
    interpolate into a formula.
    """
    if not username:
        return None
    cleaned = "".join(c for c in username.strip().lstrip("@") if c.isalnum() or c == "_")
    return cleaned.lower() or None


def _username_matches(raw: str | None, normalized: str) -> bool:
    """Whether a cell's typed username refers to the same person."""
    return _normalize_username(raw) == normalized


def _is_complete(fields: dict) -> bool:
    """Whether a submission row counts as complete.

    With no AIRTABLE_DONE_FIELD configured, the mere existence of a row is enough
    (Airtable forms only create a row on submit). Otherwise the named field must
    be non-empty.
    """
    if not settings.AIRTABLE_DONE_FIELD:
        return True
    value = fields.get(settings.AIRTABLE_DONE_FIELD)
    if isinstance(value, str):
        return bool(value.strip())
    # A numeric 0 is a real answer, so it counts as filled in. `0 in (…, False)`
    # is True because 0 == False, hence the explicit numeric case.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return value not in (None, "", [], {}, False)


def _name_from(fields: dict) -> str | None:
    if settings.AIRTABLE_NAME_FIELD:
        value = fields.get(settings.AIRTABLE_NAME_FIELD)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _query(formula: str) -> list[dict] | None:
    """Run one filterByFormula query. None means "couldn't ask", not "no rows"."""
    params: dict[str, str] = {"filterByFormula": formula}
    # Normally one row per person, so cap the response. With a "done" field
    # configured we must look at every match: someone who submitted twice may
    # have an incomplete earlier row, and inspecting only the first would leave
    # them unverifiable forever.
    if not settings.AIRTABLE_DONE_FIELD:
        params["maxRecords"] = "1"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_table_url(), headers=_headers(), params=params)
            _log_http_hint(resp)
            resp.raise_for_status()
            return resp.json().get("records", [])
    except Exception:
        logger.exception("Airtable query failed for formula %s", formula)
        return None


def _result_from(records: list[dict]) -> dict:
    """Any complete submission counts; fall back to the first row for the name."""
    for record in records:
        fields = record.get("fields", {})
        if _is_complete(fields):
            return {"complete": True, "name": _name_from(fields)}
    return {"complete": False, "name": _name_from(records[0].get("fields", {}))}


def _legacy_lookup_field() -> str | None:
    """The username field to fall back on, if it's configured and usable."""
    field = settings.AIRTABLE_USERNAME_FIELD
    if not field:
        return None
    if not _field_name_safe(field):
        logger.error(
            "GATE_AIRTABLE_USERNAME_FIELD=%r can't be used in a formula — it must "
            "be the field's NAME and must not contain { } ' \" or \\",
            field,
        )
        return None
    return field


async def lookup(tg_id: int, username: str | None = None) -> dict | None:
    """Check a single student.

    Returns ``{"complete": bool, "name": str|None, "matched_by": str|None}``, or
    ``None`` if Airtable isn't configured or a request failed.

    Two keys are tried in order of trustworthiness:

    1. ``tg_id`` — prefilled by the bot, so an exact match is proof.
    2. the username field — only for rows submitted before tg_id existed. The
       username compared here is the one Telegram reports for the person talking
       to the bot, not anything they type, so only the stored side is unreliable.
    """
    if not settings.airtable_ready():
        return None
    if not _field_name_safe(settings.AIRTABLE_TG_FIELD):
        logger.error(
            "GATE_AIRTABLE_TG_FIELD=%r can't be used in a formula — it must be the "
            "field's NAME (not a fld… id) and must not contain { } ' \" or \\",
            settings.AIRTABLE_TG_FIELD,
        )
        return None

    # `{field}&''` coerces the stored value to text so the match works whether the
    # Airtable field is a text or a number field. int() keeps the interpolation
    # safe even if a caller passes something looser than the annotation promises.
    records = await _query("{%s}&''='%d'" % (settings.AIRTABLE_TG_FIELD, int(tg_id)))
    if records is None:
        return None
    if records:
        return {**_result_from(records), "matched_by": "tg_id"}

    # No tg_id row — fall back to the legacy username column.
    normalized = _normalize_username(username)
    legacy_field = _legacy_lookup_field()
    if normalized and legacy_field:
        # Normalize inside the formula too: stored values are hand-typed, so they
        # carry stray @ prefixes, casing and whitespace.
        formula = "LOWER(TRIM(SUBSTITUTE({%s},'@','')))='%s'" % (
            legacy_field, normalized,
        )
        records = await _query(formula)
        if records is None:
            return None
        if records:
            logger.info(
                "Matched user %d to a pre-tg_id submission by username @%s",
                tg_id, normalized,
            )
            return {**_result_from(records), "matched_by": "username"}

    return {"complete": False, "name": None, "matched_by": None}


async def fetch_completed() -> dict[str, dict[str, str | None]] | None:
    """One paginated pass over the table for the background poll.

    Returns ``{"by_id": {tg_id_str: name}, "by_username": {normalized: name}}``
    covering every completed submission, or ``None`` if Airtable isn't configured
    or a request failed.

    Both maps are built in the same pass so the poll can advance people whose row
    predates tg_id without spending extra API calls.
    """
    if not settings.airtable_ready():
        return None

    by_id: dict[str, str | None] = {}
    by_username: dict[str, str | None] = {}
    offset: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            while True:
                params: list[tuple[str, str]] = [("pageSize", "100")]
                for field in _wanted_fields():
                    params.append(("fields[]", field))
                if offset:
                    params.append(("offset", offset))
                resp = await client.get(_table_url(), headers=_headers(), params=params)
                # Note the asymmetry worth knowing when debugging: this pass
                # names fields explicitly, so a typo in the name/done field 422s
                # every poll while lookup() (which sends no fields[]) keeps
                # working — button users get in, waiters never do.
                _log_http_hint(resp)
                resp.raise_for_status()
                data = resp.json()
                for record in data.get("records", []):
                    fields = record.get("fields", {})
                    if not _is_complete(fields):
                        continue
                    name = _name_from(fields)

                    tg = fields.get(settings.AIRTABLE_TG_FIELD)
                    if tg not in (None, ""):
                        by_id[str(tg).strip()] = name

                    if settings.AIRTABLE_USERNAME_FIELD:
                        handle = _normalize_username(
                            fields.get(settings.AIRTABLE_USERNAME_FIELD)
                        )
                        # Don't let an earlier row shadow a later one that has a
                        # name, but otherwise first write wins.
                        if handle and (handle not in by_username or name):
                            by_username[handle] = name
                offset = data.get("offset")
                if not offset:
                    break
    except Exception:
        logger.exception("Airtable fetch_completed failed")
        return None
    return {"by_id": by_id, "by_username": by_username}


def personalized_form_url(tg_id: int) -> str | None:
    """The form share link with this student's Telegram ID pre-filled and hidden.

    Returns ``None`` when no form URL is configured.
    """
    if not settings.FORM_URL:
        return None
    field = quote_plus(settings.AIRTABLE_TG_FIELD)

    # Params must go before any #fragment, or they land inside it and Airtable
    # never sees them — the field would show up blank and unhidden on the form.
    base, _, fragment = settings.FORM_URL.partition("#")
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}prefill_{field}={int(tg_id)}&hide_{field}=true"
    return f"{url}#{fragment}" if fragment else url
