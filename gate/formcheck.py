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


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.AIRTABLE_TOKEN}"}


def _table_url() -> str:
    # The table name/id sits in the path and may contain spaces or slashes.
    return f"{_API}/{settings.AIRTABLE_BASE_ID}/{quote(settings.AIRTABLE_TABLE, safe='')}"


def _wanted_fields() -> list[str]:
    fields = [settings.AIRTABLE_TG_FIELD]
    if settings.AIRTABLE_NAME_FIELD:
        fields.append(settings.AIRTABLE_NAME_FIELD)
    if settings.AIRTABLE_DONE_FIELD:
        fields.append(settings.AIRTABLE_DONE_FIELD)
    return fields


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
    return value not in (None, "", [], {}, False)


def _name_from(fields: dict) -> str | None:
    if settings.AIRTABLE_NAME_FIELD:
        value = fields.get(settings.AIRTABLE_NAME_FIELD)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def lookup(tg_id: int) -> dict | None:
    """Check a single student.

    Returns ``{"complete": bool, "name": str|None}``, or ``None`` if Airtable
    isn't configured or the request failed.
    """
    if not settings.airtable_ready():
        return None

    # `{field}&''` coerces the value to text so the match works whether the
    # Airtable field is a text or a number field.
    formula = "{%s}&''='%s'" % (settings.AIRTABLE_TG_FIELD, str(tg_id))
    params = {"filterByFormula": formula, "maxRecords": "1"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_table_url(), headers=_headers(), params=params)
            resp.raise_for_status()
            records = resp.json().get("records", [])
    except Exception:
        logger.exception("Airtable lookup failed for tg_id %s", tg_id)
        return None

    if not records:
        return {"complete": False, "name": None}
    fields = records[0].get("fields", {})
    return {"complete": _is_complete(fields), "name": _name_from(fields)}


async def fetch_completed() -> dict[str, str | None] | None:
    """One paginated pass over the table for the background poll.

    Returns ``{tg_id_str: full_name_or_None}`` for every completed submission, or
    ``None`` if Airtable isn't configured or a request failed.
    """
    if not settings.airtable_ready():
        return None

    completed: dict[str, str | None] = {}
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
                resp.raise_for_status()
                data = resp.json()
                for record in data.get("records", []):
                    fields = record.get("fields", {})
                    tg = fields.get(settings.AIRTABLE_TG_FIELD)
                    if tg in (None, ""):
                        continue
                    if not _is_complete(fields):
                        continue
                    completed[str(tg).strip()] = _name_from(fields)
                offset = data.get("offset")
                if not offset:
                    break
    except Exception:
        logger.exception("Airtable fetch_completed failed")
        return None
    return completed


def personalized_form_url(tg_id: int) -> str | None:
    """The form share link with this student's Telegram ID pre-filled and hidden.

    Returns ``None`` when no form URL is configured.
    """
    if not settings.FORM_URL:
        return None
    field = quote_plus(settings.AIRTABLE_TG_FIELD)
    sep = "&" if "?" in settings.FORM_URL else "?"
    return f"{settings.FORM_URL}{sep}prefill_{field}={tg_id}&hide_{field}=true"
