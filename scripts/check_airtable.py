#!/usr/bin/env python3
"""Pre-flight checker for the Alumni Gate's Airtable form verification.

    python scripts/check_airtable.py <telegram_user_id>

Why this exists: the bot treats *every* Airtable problem — bad token, missing
scope, misspelled field, rate limit, outage — as "couldn't verify" and never as
"rejected". A broken setup is therefore invisible from the outside: students are
told "try again in a minute" forever and nobody is ever admitted. This script
makes those failures loud, before you go live.

It is deliberately standalone: stdlib only (python-dotenv is used if installed,
otherwise a small built-in .env reader), and it imports nothing from ``gate/``
or the bot, so it keeps working while those files are being edited. The
requests, the filterByFormula and the personalized form URL below are
hand-copies of what ``gate/formcheck.py`` does — if you change that module,
change this too.

Checks, one PASS/FAIL line each:
  1. config sanity (names, shapes, illegal characters)
  2. token authenticates          GET /v0/meta/whoami
  3. base + table readable        GET /v0/{base}/{table}?maxRecords=1
  4. tg-id field exists, + type   GET /v0/meta/bases/{base}/tables (or a probe)
  5. optional done/name fields exist
  6. the real lookup             GET ...?filterByFormula={tg}&''='<id>'
  7. the background poll's first page + its monthly API-call cost
  8. GATE_FORM_URL, and the personalized link for this student

Exit status: 0 when nothing failed, 1 when any check FAILed, 2 on bad usage.
See docs/AIRTABLE_SETUP.md.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.airtable.com/v0"
TIMEOUT = 15.0

# Airtable field types that can actually hold a prefilled Telegram id.
GOOD_TG_TYPES = {"singleLineText", "multilineText", "number"}
# Types that can never receive a form prefill (computed / system fields).
UNFILLABLE_TYPES = {
    "formula",
    "rollup",
    "count",
    "lookup",
    "multipleLookupValues",
    "autoNumber",
    "createdTime",
    "createdBy",
    "lastModifiedTime",
    "lastModifiedBy",
    "button",
    "barcode",
    "attachment",
    "multipleAttachments",
}

PASS, FAIL, WARN, INFO, SKIP = "PASS", "FAIL", "WARN", "INFO", "SKIP"
_COLOR = {
    PASS: "\033[32m",
    FAIL: "\033[31m",
    WARN: "\033[33m",
    INFO: "\033[36m",
    SKIP: "\033[90m",
}
_RESET = "\033[0m"
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_counts = {PASS: 0, FAIL: 0, WARN: 0, INFO: 0, SKIP: 0}


def report(status: str, title: str, *details: str) -> None:
    tag = f"{_COLOR[status]}{status}{_RESET}" if _USE_COLOR else status
    print(f"[{tag}] {title}")
    for line in details:
        for sub in str(line).splitlines():
            print(f"        {sub}")
    _counts[status] += 1


def section(name: str) -> None:
    print(f"\n--- {name} ---")


# ---------------------------------------------------------------------------
# environment loading
# ---------------------------------------------------------------------------


def _load_dotenv_files() -> list[str]:
    """Load .env into os.environ without clobbering real env vars.

    Uses python-dotenv when available (same library the bot uses), otherwise a
    minimal parser good enough for KEY=value files.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), ".env"),  # repo root, next to bot.py
        os.path.join(os.getcwd(), ".env"),
    ]
    seen: list[str] = []
    for path in candidates:
        if path in seen or not os.path.isfile(path):
            continue
        seen.append(path)
        try:
            from dotenv import load_dotenv  # type: ignore

            load_dotenv(path, override=False)
            continue
        except ImportError:
            pass
        try:
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :]
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError as exc:  # unreadable .env is worth knowing about
            report(WARN, f"could not read {path}", str(exc))
    return seen


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def mask(secret: str) -> str:
    if not secret:
        return "(empty)"
    if len(secret) <= 12:
        return secret[:2] + "…"
    return f"{secret[:8]}…{secret[-4:]} ({len(secret)} chars)"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Response:
    def __init__(
        self,
        status: int | None,
        body: dict | None,
        raw: str,
        transport_error: str | None,
    ) -> None:
        self.status = status
        self.body = body or {}
        self.raw = raw
        self.transport_error = transport_error

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.transport_error is None

    @property
    def error_type(self) -> str:
        err = self.body.get("error")
        if isinstance(err, dict):
            return str(err.get("type", ""))
        if isinstance(err, str):
            return err
        return ""

    @property
    def error_message(self) -> str:
        err = self.body.get("error")
        if isinstance(err, dict):
            return str(err.get("message", ""))
        return ""

    def describe(self) -> str:
        if self.transport_error:
            return f"transport error: {self.transport_error}"
        parts = [f"HTTP {self.status}"]
        if self.error_type:
            parts.append(self.error_type)
        if self.error_message:
            parts.append(self.error_message)
        if len(parts) == 1 and self.raw:
            parts.append(self.raw[:300])
        return " — ".join(parts)


def _ssl_context():
    """Verify TLS against certifi's bundle when available.

    The bot talks to Airtable through httpx, which always uses certifi. A bare
    python.org interpreter often has no usable system trust store, so without
    this the checker would report a fake connection failure on a setup that
    actually works.
    """
    try:
        import ssl

        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


_SSL_CONTEXT = _ssl_context()


def api_get(token: str, path: str, params: list[tuple[str, str]] | None = None) -> Response:
    url = f"{API_ROOT}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "alumnigate-check-airtable/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return Response(resp.status, json.loads(raw), raw, None)
            except ValueError:
                return Response(resp.status, None, raw, None)
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return Response(exc.code, body, raw, None)
    except urllib.error.URLError as exc:
        return Response(None, None, "", f"{type(exc).__name__}: {exc.reason}")
    except Exception as exc:  # timeouts, SSL, anything else
        return Response(None, None, "", f"{type(exc).__name__}: {exc}")


HTTP_ADVICE = {
    401: (
        "The token was rejected. Copy the WHOLE personal access token, including "
        "everything after the '.' — a token ID alone gives exactly this error. "
        "Create/inspect it at https://airtable.com/create/tokens"
    ),
    403: (
        "Authenticated but not allowed. Two causes, same error: (a) the token is "
        "missing the 'data.records:read' scope, (b) this base is not in the "
        "token's Access list. Fix both at https://airtable.com/create/tokens"
    ),
    404: (
        "Route or resource not found — usually a wrong GATE_AIRTABLE_BASE_ID "
        "(must be the app… segment of the base URL) or a table name/ID that does "
        "not exist in this base."
    ),
    422: (
        "Airtable rejected a parameter — most often an unknown field name. Check "
        "GATE_AIRTABLE_TG_FIELD / _NAME_FIELD / _DONE_FIELD against the real "
        "field names, character for character."
    ),
    429: (
        "Rate limited. 5 requests/second per base, and on a Free workspace only "
        "1,000 API calls per MONTH (Team: 100,000). If you are nowhere near "
        "5 req/s you have most likely hit the monthly cap — check Workspace "
        "settings → Usage → Public API calls, and raise "
        "GATE_POLL_INTERVAL_MINUTES or disable the poll with 0."
    ),
    503: "Airtable is temporarily unavailable (RETRIABLE_ERROR). Retry shortly.",
}


def advise(resp: Response) -> str:
    if resp.transport_error:
        if "CERTIFICATE_VERIFY" in resp.transport_error.upper():
            return (
                "This is a local TLS trust problem, NOT an Airtable problem: this "
                "Python has no usable CA bundle. Run 'pip install certifi' in the "
                "bot's virtualenv (macOS python.org builds: run "
                "'Install Certificates.command'), then re-run. The bot itself uses "
                "httpx, which bundles certifi, so it may work where this does not."
            )
        return (
            "Could not reach api.airtable.com at all. Check DNS/egress/proxy for "
            "api.airtable.com:443 — the bot needs the same access."
        )
    return HTTP_ADVICE.get(
        resp.status or 0, "Unexpected response; see the raw body above."
    )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class Config:
    def __init__(self) -> None:
        self.token = env("GATE_AIRTABLE_TOKEN")
        self.base = env("GATE_AIRTABLE_BASE_ID")
        self.table = env("GATE_AIRTABLE_TABLE")
        self.tg_field = env("GATE_AIRTABLE_TG_FIELD", "tg_id") or "tg_id"
        self.done_field = env("GATE_AIRTABLE_DONE_FIELD")
        self.name_field = env("GATE_AIRTABLE_NAME_FIELD")
        self.username_field = env("GATE_AIRTABLE_USERNAME_FIELD")
        self.form_url = env("GATE_FORM_URL")
        self.poll_minutes = env("GATE_POLL_INTERVAL_MINUTES", "3") or "3"

    @property
    def table_path(self) -> str:
        return f"/{self.base}/{urllib.parse.quote(self.table, safe='')}"

    def wanted_fields(self) -> list[str]:
        fields = [self.tg_field]
        if self.name_field:
            fields.append(self.name_field)
        if self.done_field:
            fields.append(self.done_field)
        return fields


BAD_FIELD_CHARS = "{}'\"\\"


def check_config(cfg: Config) -> bool:
    """Returns True when the three required settings are present."""
    section("1. configuration")
    ready = True

    missing = [
        name
        for name, value in (
            ("GATE_AIRTABLE_TOKEN", cfg.token),
            ("GATE_AIRTABLE_BASE_ID", cfg.base),
            ("GATE_AIRTABLE_TABLE", cfg.table),
        )
        if not value
    ]
    if missing:
        report(
            FAIL,
            "required settings missing: " + ", ".join(missing),
            "gate/settings.py: airtable_ready() needs all three. While any is "
            "blank the gate makes no API calls at all and silently verifies "
            "nobody — you will not even see an error in the log.",
            "See docs/AIRTABLE_SETUP.md steps 5-7.",
        )
        ready = False
    else:
        report(PASS, "token, base ID and table are all set")

    if cfg.token:
        if cfg.token.startswith("key"):
            report(
                FAIL,
                "GATE_AIRTABLE_TOKEN looks like a legacy API key ('key…')",
                "Legacy API keys are no longer accepted. Create a personal "
                "access token at https://airtable.com/create/tokens",
            )
            ready = False
        elif not cfg.token.startswith("pat"):
            report(
                WARN,
                "GATE_AIRTABLE_TOKEN does not start with 'pat'",
                f"got {mask(cfg.token)} — personal access tokens start with 'pat'.",
            )
        elif "." not in cfg.token:
            report(
                FAIL,
                "GATE_AIRTABLE_TOKEN looks truncated (no '.')",
                "A full PAT is patXXXXXXXXXXXXXX.<long secret>. Copying only the "
                "ID gives a 401 on every request.",
            )
            ready = False
        else:
            report(PASS, f"token shape looks right: {mask(cfg.token)}")

    if cfg.base:
        if not cfg.base.startswith("app"):
            report(
                FAIL,
                f"GATE_AIRTABLE_BASE_ID={cfg.base!r} is not a base ID",
                "It must be the 'app…' segment of the base URL, e.g. "
                "https://airtable.com/appR7fQ2mK9wLzXyz/tbl…",
            )
            ready = False
        else:
            report(PASS, f"base ID looks right: {cfg.base}")

    if cfg.table:
        if cfg.table.startswith("viw"):
            report(
                FAIL,
                f"GATE_AIRTABLE_TABLE={cfg.table!r} is a VIEW id",
                "Use the table name (e.g. Onboarding) or the 'tbl…' table ID.",
            )
            ready = False
        elif cfg.table.startswith(("app", "fld", "rec")):
            report(
                FAIL,
                f"GATE_AIRTABLE_TABLE={cfg.table!r} is not a table name or ID",
                "Use the table name or the 'tbl…' ID.",
            )
            ready = False
        else:
            kind = "table ID" if cfg.table.startswith("tbl") else "table name"
            report(PASS, f"table looks right ({kind}): {cfg.table}")

    # The tg field name is interpolated into filterByFormula, so its characters
    # matter more than the other two.
    if cfg.tg_field.startswith("fld"):
        report(
            FAIL,
            f"GATE_AIRTABLE_TG_FIELD={cfg.tg_field!r} is a field ID",
            "filterByFormula accepts field NAMES only ('You can only use field "
            "names'), so the student-facing button would 422 on every check. Use "
            "the field's name, e.g. tg_id.",
        )
        ready = False
    elif any(ch in cfg.tg_field for ch in BAD_FIELD_CHARS):
        report(
            FAIL,
            f"GATE_AIRTABLE_TG_FIELD={cfg.tg_field!r} contains {BAD_FIELD_CHARS!r}",
            "The formula is built by string interpolation "
            "(\"{%s}&''='%s'\"), so these characters break it and every lookup "
            "returns 422. Rename the Airtable field to plain ASCII, e.g. tg_id.",
        )
        ready = False
    elif cfg.tg_field != cfg.tg_field.strip() or " " in cfg.tg_field:
        report(
            WARN,
            f"GATE_AIRTABLE_TG_FIELD={cfg.tg_field!r} contains spaces",
            "It works (spaces are encoded as %20/+ in the prefill URL) but it is "
            "an easy thing to get subtly wrong. 'tg_id' is safer.",
        )
    else:
        report(PASS, f"tg-id field name is formula-safe: {cfg.tg_field}")

    for var, value in (
        ("GATE_AIRTABLE_DONE_FIELD", cfg.done_field),
        ("GATE_AIRTABLE_NAME_FIELD", cfg.name_field),
    ):
        if not value:
            continue
        if value.startswith("fld"):
            report(
                FAIL,
                f"{var}={value!r} is a field ID",
                "The bot reads the response by field NAME "
                "(fields.get(<name>)), so a field ID here always reads as "
                "empty. Use the field's name.",
            )
            ready = False
        else:
            report(INFO, f"{var}={value!r} (checked against the schema below)")

    if cfg.done_field:
        report(
            WARN,
            "GATE_AIRTABLE_DONE_FIELD is set",
            "An Airtable form only creates a row on submit, so 'row exists' is "
            "normally proof enough. With a done field configured, lookup() asks "
            "for maxRecords=1 and inspects only the FIRST matching row — a "
            "student who submitted twice, with the earlier row's done field "
            "empty, stays unverified forever. Leave it blank unless something "
            "other than the form writes rows to this table.",
        )

    try:
        poll = int(cfg.poll_minutes)
    except ValueError:
        poll = 3
        report(
            WARN,
            f"GATE_POLL_INTERVAL_MINUTES={cfg.poll_minutes!r} is not an integer",
            "The bot would crash on import; assuming 3 for the budget estimate.",
        )
    cfg._poll = poll  # type: ignore[attr-defined]
    return ready


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_token(cfg: Config) -> bool:
    section("2. token authentication")
    resp = api_get(cfg.token, "/meta/whoami")
    if resp.ok:
        who = resp.body.get("id", "?")
        scopes = resp.body.get("scopes")
        details = [f"authenticated as user {who}"]
        if scopes:
            details.append(f"scopes: {', '.join(scopes)}")
        report(PASS, "token authenticates (GET /v0/meta/whoami)", *details)
        return True
    details = [resp.describe(), advise(resp)]
    if resp.status is not None:
        details.append(
            "This endpoint needs no scopes, so a failure here is the token "
            "itself, not its permissions."
        )
    report(FAIL, "token does NOT authenticate (GET /v0/meta/whoami)", *details)
    return False


def check_table_read(cfg: Config) -> bool:
    section("3. base + table readable with data.records:read")
    resp = api_get(cfg.token, cfg.table_path, [("maxRecords", "1")])
    if resp.ok:
        records = resp.body.get("records", [])
        report(
            PASS,
            f"GET /v0/{cfg.base}/{cfg.table} works",
            f"table currently returns {'at least 1' if records else 'no'} record(s)"
            + ("" if records else " — that is fine for a fresh form"),
        )
        return True
    report(
        FAIL,
        f"cannot read table {cfg.table!r} in base {cfg.base}",
        resp.describe(),
        advise(resp),
        "Every student check goes through this exact request, so nothing will "
        "verify until it returns 200.",
    )
    return False


def _field_probe(cfg: Config, field: str) -> str:
    """Existence probe that needs only data.records:read.

    Returns "ok", "missing", or "unknown".
    """
    resp = api_get(cfg.token, cfg.table_path, [("maxRecords", "1"), ("fields[]", field)])
    if resp.ok:
        return "ok"
    if resp.status == 422:
        return "missing"
    return "unknown"


def check_fields(cfg: Config) -> bool:
    section("4. fields exist on the table")
    ok = True
    schema = api_get(cfg.token, f"/meta/bases/{cfg.base}/tables")

    if not schema.ok:
        if schema.status in (401, 403, 404):
            report(
                SKIP,
                "schema not readable — falling back to a field probe",
                schema.describe(),
                "Add the 'schema.bases:read' scope to the token if you want this "
                "checker to report field TYPES. The bot itself never needs it.",
            )
        else:
            report(WARN, "schema request failed", schema.describe())

        for label, field, required in (
            ("GATE_AIRTABLE_TG_FIELD", cfg.tg_field, True),
            ("GATE_AIRTABLE_NAME_FIELD", cfg.name_field, False),
            ("GATE_AIRTABLE_DONE_FIELD", cfg.done_field, False),
        ):
            if not field:
                continue
            verdict = _field_probe(cfg, field)
            if verdict == "ok":
                report(PASS, f"{label}: field {field!r} exists (type unknown)")
            elif verdict == "missing":
                report(
                    FAIL,
                    f"{label}: field {field!r} does not exist on this table",
                    "Airtable answered 422 UNKNOWN_FIELD_NAME for fields[]="
                    f"{field}. Fix the spelling (it is case- and space-sensitive)"
                    + ("" if required else " or leave the variable blank."),
                )
                ok = False
            else:
                report(WARN, f"{label}: could not determine whether {field!r} exists")
        return ok

    tables = schema.body.get("tables", [])
    table = None
    for candidate in tables:
        if cfg.table in (candidate.get("id"), candidate.get("name")):
            table = candidate
            break
    if table is None:
        for candidate in tables:
            if str(candidate.get("name", "")).lower() == cfg.table.lower():
                table = candidate
                report(
                    FAIL,
                    f"table {cfg.table!r} only matches by case-insensitive name",
                    f"the real name is {candidate.get('name')!r}. Airtable table "
                    "names in the API path are case-sensitive.",
                )
                ok = False
                break
    if table is None:
        names = ", ".join(
            f"{t.get('name')} ({t.get('id')})" for t in tables[:20]
        ) or "(none)"
        report(
            FAIL,
            f"table {cfg.table!r} is not in base {cfg.base}",
            f"tables in this base: {names}",
        )
        return False

    report(
        INFO,
        f"table: {table.get('name')!r} ({table.get('id')}), "
        f"{len(table.get('fields', []))} fields",
    )

    by_name = {f.get("name"): f for f in table.get("fields", [])}
    for label, field, required in (
        ("GATE_AIRTABLE_TG_FIELD", cfg.tg_field, True),
        ("GATE_AIRTABLE_NAME_FIELD", cfg.name_field, False),
        ("GATE_AIRTABLE_DONE_FIELD", cfg.done_field, False),
    ):
        if not field:
            continue
        found = by_name.get(field)
        if not found:
            close = [n for n in by_name if n and n.lower() == field.lower()]
            hint = (
                f"did you mean {close[0]!r}? (names are case-sensitive)"
                if close
                else "fields on this table: " + ", ".join(sorted(map(str, by_name)))
            )
            report(
                FAIL,
                f"{label}: no field named {field!r}",
                hint,
                "The bot's requests will return 422 and every verification will "
                "read as 'couldn't verify'."
                if required
                else "The background poll sends this in fields[] and will 422 on "
                "every pass — the button would keep working, so only the "
                "students who wait get stuck. Fix it or blank the variable.",
            )
            ok = False
            continue

        ftype = found.get("type")
        extra = ""
        if ftype == "number":
            precision = (found.get("options") or {}).get("precision")
            extra = f" (precision {precision})"
        report(PASS, f"{label}: field {field!r} exists — type {ftype}{extra}")

        if label == "GATE_AIRTABLE_TG_FIELD":
            if ftype in UNFILLABLE_TYPES:
                report(
                    FAIL,
                    f"{field!r} is a {ftype} field, which a form can never prefill",
                    "Recreate it as Single line text and put it back on the form.",
                )
                ok = False
            elif ftype not in GOOD_TG_TYPES:
                report(
                    WARN,
                    f"{field!r} is a {ftype} field",
                    "Single line text is the tested choice (Number also works: "
                    "the formula casts with &'' using the stored value).",
                )
    return ok


def _is_complete(cfg: Config, fields: dict) -> bool:
    """Local copy of gate/formcheck.py::_is_complete."""
    if not cfg.done_field:
        return True
    value = fields.get(cfg.done_field)
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, "", [], {}, False)


def check_lookup(cfg: Config, tg_id: int) -> bool:
    section("5. the real lookup (what the student's button does)")
    formula = "{%s}&''='%s'" % (cfg.tg_field, str(tg_id))
    report(INFO, f"filterByFormula: {formula}")
    resp = api_get(
        cfg.token,
        cfg.table_path,
        [("filterByFormula", formula), ("maxRecords", "1")],
    )
    if not resp.ok:
        report(
            FAIL,
            "lookup request failed",
            resp.describe(),
            advise(resp),
            "422 here almost always means GATE_AIRTABLE_TG_FIELD does not match "
            "a real field name (Airtable says 'Unknown field names: …').",
        )
        return False

    records = resp.body.get("records", [])
    if not records:
        report(
            PASS,
            f"query works; no submission on file for tg_id {tg_id}",
            "The formula is valid and Airtable answered — the bot would report "
            "this student as 'not complete yet' (not as an error).",
            "If this student HAS submitted the form, the row's "
            f"{cfg.tg_field!r} cell is empty or holds something else — the "
            "classic 'field not actually on the form' bug. See "
            "docs/AIRTABLE_SETUP.md step 3.",
        )
        return True

    fields = records[0].get("fields", {})
    raw = fields.get(cfg.tg_field)
    details = [
        f"record {records[0].get('id')}",
        f"{cfg.tg_field} = {raw!r}",
        f"the bot would treat this student as complete={_is_complete(cfg, fields)}",
    ]
    if cfg.name_field:
        details.append(f"{cfg.name_field} = {fields.get(cfg.name_field)!r}")
    if cfg.done_field:
        details.append(f"{cfg.done_field} = {fields.get(cfg.done_field)!r}")
    report(PASS, f"found a matching submission for tg_id {tg_id}", *details)
    if str(raw).strip() != str(tg_id):
        report(
            WARN,
            "the stored value is not exactly the id",
            f"stored {raw!r} vs requested {tg_id} — the &'' cast matched anyway, "
            "but check the field type/formatting.",
        )
    return True


# gate/formcheck.py asks about this many waiting students per request. Keep in
# step with _POLL_CHUNK there, or the budget below stops matching reality.
POLL_CHUNK = 25


def check_poll_pass(cfg: Config) -> bool:
    section("6. the background poll's targeted query")
    # Mirror fetch_completed_for(): an OR() over the people actually mid-
    # onboarding, not a scan of the table. Two synthetic clauses stand in for one
    # waiting student — the tg_id match, plus the legacy username match when a
    # username field is configured — so this exercises the real formula shape.
    clauses = ["{%s}&''='%d'" % (cfg.tg_field, 1)]
    if cfg.username_field:
        clauses.append(
            "LOWER(TRIM(SUBSTITUTE({%s},'@','')))='%s'" % (cfg.username_field, "probe")
        )
    params: list[tuple[str, str]] = [
        ("pageSize", "100"),
        ("filterByFormula", "OR(%s)" % ",".join(clauses)),
    ]
    for field in cfg.wanted_fields():
        params.append(("fields[]", field))
    resp = api_get(cfg.token, cfg.table_path, params)
    if not resp.ok:
        details = [resp.describe(), advise(resp)]
        if resp.status == 422:
            details.append(
                "This request differs from the button's only by fields[]=… and the "
                "OR() wrapper — so a 422 here while check 5 passed means "
                "GATE_AIRTABLE_NAME_FIELD, GATE_AIRTABLE_DONE_FIELD or "
                "GATE_AIRTABLE_USERNAME_FIELD is misspelled. Symptom in "
                "production: the button verifies people, the poll never does."
            )
        report(FAIL, "the poll's request shape fails", *details)
        return False

    poll = getattr(cfg, "_poll", 3)
    details = [
        f"fields[] requested: {', '.join(cfg.wanted_fields())}",
        f"formula clauses per waiting student: {len(clauses)}"
        + ("" if cfg.username_field else " (no username fallback configured)"),
    ]
    report(PASS, "the poll's request shape works", *details)

    if poll <= 0:
        report(
            INFO,
            "GATE_POLL_INTERVAL_MINUTES=0 — poll disabled",
            "Verification is button-only: ~1 API call per tap. Cheapest option, "
            "and the right one on a Free workspace.",
        )
        return True

    # Cost scales with how many students are mid-onboarding, not with table size,
    # so there is no fixed monthly figure — only a per-waiting-student rate.
    passes = int(30 * 24 * 60 / poll)
    report(
        INFO,
        "API-call budget",
        f"poll every {poll} min = {passes:,} passes/month; each pass costs "
        f"ceil(waiting / {POLL_CHUNK}) requests, so it is 0 when nobody is "
        "waiting and 1 for a typical handful.",
        f"steady state with 1-{POLL_CHUNK} students waiting ≈ {passes:,} "
        f"calls/month; with {POLL_CHUNK * 2 + 1}-{POLL_CHUNK * 3} waiting "
        f"≈ {passes * 3:,}. Table size does not enter into it.",
    )
    if passes > 100_000:
        fits = int(30 * 24 * 60 / 90_000) + 1
        report(
            WARN,
            "even one request per pass exceeds the Team-plan allowance",
            "Team workspaces get 100,000 calls/month; Business and above have no "
            "monthly cap. Exceeding it triggers a one-time 30-day grace period, "
            "after which calls are BLOCKED until the month resets.",
            f"Raise GATE_POLL_INTERVAL_MINUTES to at least {fits}.",
        )
    elif passes > 1000:
        report(
            INFO,
            "note: this would exceed a Free workspace's allowance",
            "Fine on Team (100,000/month) or Business (uncapped). On a Free "
            "workspace (1,000/month) set GATE_POLL_INTERVAL_MINUTES=0 and rely "
            "on the button, which costs ~1 call per tap.",
        )
    return True


def check_form_url(cfg: Config, tg_id: int) -> bool:
    section("7. form URL and the personalized link")
    if not cfg.form_url:
        report(
            FAIL,
            "GATE_FORM_URL is empty",
            "personalized_form_url() returns None, so students are never given a "
            "form link at all. Paste the link from Share form → Copy link.",
        )
        return False

    ok = True
    parsed = urllib.parse.urlparse(cfg.form_url)
    if parsed.scheme != "https":
        report(FAIL, f"GATE_FORM_URL is not https: {cfg.form_url!r}")
        ok = False
    if parsed.netloc and "airtable.com" not in parsed.netloc:
        report(
            WARN,
            f"GATE_FORM_URL host is {parsed.netloc!r}, not airtable.com",
            "Fine if you front the form with a custom domain, but prefill "
            "parameters must survive the redirect — test it by hand.",
        )
    if parsed.fragment:
        report(
            FAIL,
            "GATE_FORM_URL contains a #fragment",
            "The bot appends ?prefill_…&hide_… to the END of the string, so the "
            "parameters would land inside the fragment and be ignored. Remove it.",
        )
        ok = False
    if "prefill_" in cfg.form_url or "hide_" in cfg.form_url:
        report(
            FAIL,
            "GATE_FORM_URL already contains prefill_/hide_ parameters",
            "Store the bare share link; the bot adds the per-student parameters.",
        )
        ok = False
    elif parsed.query:
        report(
            WARN,
            f"GATE_FORM_URL already has a query string ({parsed.query!r})",
            "The bot will append with '&'. Usually you want the bare share link.",
        )
    path = parsed.path or ""
    if not any(seg.startswith(("shr", "pag")) for seg in path.split("/")):
        report(
            WARN,
            "GATE_FORM_URL does not look like a form link",
            "Expected …/shrXXXXXXXXXXXXXX (form view) or "
            "…/appXXXXXXXXXXXXXX/pagXXXXXXXXXXXXXX/form (interface form).",
        )

    field = urllib.parse.quote_plus(cfg.tg_field)
    sep = "&" if "?" in cfg.form_url else "?"
    personalized = f"{cfg.form_url}{sep}prefill_{field}={tg_id}&hide_{field}=true"
    if ok:
        report(PASS, "GATE_FORM_URL looks usable")
    report(
        INFO,
        f"personalized link for tg_id {tg_id} (open it, submit, then re-run this):",
        personalized,
        "You must NOT see a "
        f"{cfg.tg_field!r} question on the form, and after submitting, the new "
        f"row's {cfg.tg_field!r} cell must contain {tg_id}. A blank cell means "
        "the field is not on the form — the failure that looks like success.",
    )
    return ok


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


USAGE = """usage: python scripts/check_airtable.py <telegram_user_id>

Pre-flight check for the Alumni Gate's Airtable integration. Reads
GATE_AIRTABLE_TOKEN, GATE_AIRTABLE_BASE_ID, GATE_AIRTABLE_TABLE,
GATE_AIRTABLE_TG_FIELD, GATE_AIRTABLE_DONE_FIELD, GATE_AIRTABLE_NAME_FIELD,
GATE_FORM_URL and GATE_POLL_INTERVAL_MINUTES from the environment (and from
.env, without overriding real environment variables).

<telegram_user_id> is any numeric Telegram user id — use your own. It is only
read, never written. Details: docs/AIRTABLE_SETUP.md"""


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a not in ("-h", "--help")]
    if len(argv) > 1 and len(args) != len(argv) - 1:
        print(USAGE)
        return 0
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    if not args[0].lstrip("-").isdigit() or args[0].startswith("-"):
        print(f"error: {args[0]!r} is not a numeric Telegram user id\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    tg_id = int(args[0])

    print("Alumni Gate — Airtable pre-flight check")
    loaded = _load_dotenv_files()
    print(f".env files loaded: {', '.join(loaded) if loaded else '(none found)'}")

    cfg = Config()
    print(
        "config: base={base} table={table} tg_field={tg} done={done} name={name} "
        "token={token}".format(
            base=cfg.base or "(empty)",
            table=cfg.table or "(empty)",
            tg=cfg.tg_field,
            done=cfg.done_field or "-",
            name=cfg.name_field or "-",
            token=mask(cfg.token),
        )
    )

    have_config = check_config(cfg)

    if not have_config:
        section("2-6. Airtable API")
        report(
            SKIP,
            "skipping all API checks — configuration is incomplete or invalid",
            "Fix the FAILs above, then re-run.",
        )
    else:
        if check_token(cfg) and check_table_read(cfg):
            check_fields(cfg)
            check_lookup(cfg, tg_id)
            check_poll_pass(cfg)
        else:
            report(
                SKIP,
                "skipping field/lookup/poll checks — the table is not readable yet",
            )

    check_form_url(cfg, tg_id)

    section("summary")
    print(
        f"{_counts[PASS]} passed, {_counts[FAIL]} failed, {_counts[WARN]} warnings, "
        f"{_counts[SKIP]} skipped"
    )
    if _counts[FAIL]:
        print(
            "\nDo NOT set GATE_LIVE=true yet. With a FAIL above, the bot cannot "
            "verify anyone — and because it treats every Airtable failure as "
            "'couldn't verify', students will simply be told to try again in a "
            "minute, forever, with nothing visible in the chat to tell you why."
        )
        return 1
    print(
        "\nAll checks passed. Finish with a real submission: open the "
        "personalized link above, submit the form, confirm the "
        f"{cfg.tg_field!r} cell holds your id, then re-run this script and see "
        "check 5 find the record."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
