# Airtable setup for the Alumni Gate

Runbook for wiring the onboarding-form gate to a real Airtable base. Follow the
steps in order; each one ends with a value you paste into `.env`.

Every setting named here is read in [`gate/settings.py`](../gate/settings.py) —
that file is the source of truth for variable names.

---

## 0. How verification works (read this first)

The Telegram Bot API cannot tell the bot who filled in a form, so the Telegram
user ID is carried through the form itself:

1. The bot builds each student a **personal** form link with their Telegram user
   ID pre-filled into a hidden field (`tg_id` by default), using Airtable's
   `prefill_<field>=<value>&hide_<field>=true` URL parameters.
2. The student submits the form. Airtable creates one row, with their Telegram
   ID sitting in that field. ("Each time someone submits the form, Airtable adds
   one new record" — see Sources.)
3. The bot verifies completion by asking the Airtable REST API for a row whose
   `tg_id` matches, using `filterByFormula`. No usernames, no screenshots, no
   typing by the student.

Two call sites (`gate/formcheck.py`):

| Call | Trigger | Request |
| --- | --- | --- |
| `lookup(tg_id)` | student taps "I've completed the form" | `GET /v0/{base}/{table}?filterByFormula={tg_id}&''='<id>'&maxRecords=1` |
| `fetch_completed()` | background poll, every `GATE_POLL_INTERVAL_MINUTES` | `GET /v0/{base}/{table}?pageSize=100&fields[]=…` repeated until no `offset` |

> **The failure mode you must design around.** Both functions return `None` on
> *any* problem — bad token, missing scope, wrong field name, rate limit,
> Airtable outage — and the bot deliberately treats `None` as "couldn't verify",
> never as "rejected". So **a misconfiguration is indistinguishable from an
> outage from the student's side**: everyone is told "try again in a minute",
> forever, and nobody is ever admitted. Nothing in the student-facing flow will
> ever tell you the setup is wrong. That is why steps 8 and 9 below (pre-flight
> check + one real end-to-end submission) are not optional, and why the bot's
> log is the only place the real error appears (`logger.exception` in
> `gate/formcheck.py`).

### Before you start: check the workspace plan

Airtable meters API calls **per workspace, per calendar month**:

| Plan | Monthly API calls | When you blow through it |
| --- | --- | --- |
| Free | **1,000** | 30-day grace period (once, ever), then calls are **blocked** until the month resets |
| Team | **100,000** | rate drops to **2 requests/second** until the month resets |
| Business / Enterprise Scale | no monthly cap | per-base rate limit still applies |

All plans are additionally capped at **5 requests/second per base** and 50
requests/second per personal access token; exceeding that returns **429** and
you must wait **30 seconds**.

The background poll costs at least one call per pass. At the default
`GATE_POLL_INTERVAL_MINUTES=3` that is 480 calls/day ≈ **14,400 calls/month** —
14× the entire Free-plan monthly allowance. **On a Free workspace the gate will
work for about two days and then silently stop verifying anyone.** Decide now:

- Free workspace → set `GATE_POLL_INTERVAL_MINUTES=0` (button-only verification,
  which costs one call per tap) or at minimum `60` (≈720 calls/month). Watch
  *Workspace settings → Usage → Public API calls*. `scripts/check_airtable.py`
  prints the actual estimate for your settings.

A pass costs `ceil(waiting / 25)` calls — the poll asks about the students
actually mid-onboarding, not the whole table — so it is **0 when nobody is
waiting** and 1 for the usual handful. Table size does not affect it: the
alumni database can grow without the bill moving.
- Team or above → the default 3-minute poll is fine.

---

## 1. Create the base and the submissions table

1. In Airtable, create a base (or open an existing one) — e.g. **Alumni
   Onboarding**.
2. Create/rename one table to hold form submissions, e.g. **Onboarding**.

Give this table a single job. The bot's default rule is *"a row exists ⇒ the
student is done"*, so anything else that writes rows to this table (an
automation, a CSV import, a sync) would admit people who never filled in the
form. If the table must be shared, use `GATE_AIRTABLE_DONE_FIELD` (step 7).

## 2. Create the `tg_id` field

In the **Onboarding** table, add a field:

- **Name:** `tg_id` — exactly this, lowercase, no spaces.
- **Type:** **Single line text**.

Why those choices matter:

- The one string in `GATE_AIRTABLE_TG_FIELD` is used for *three* different
  things: the `prefill_…`/`hide_…` URL parameter names, the `fields[]` API
  parameter, and the `{…}` reference inside `filterByFormula`. A plain
  ASCII name with no spaces is the only value that is unambiguous in all three.
  (Spaces would have to be encoded as `%20` or `+` in the URL, and Airtable
  field *IDs* — `fldXXXXXXXXXXXXXX` — are **not** accepted in
  `filterByFormula`: "You can only use field names.")
- Never put `}`, `'` or `"` in the field name. `filterByFormula` is assembled by
  string interpolation (`"{%s}&''='%s'"`), so those characters break the formula
  and every lookup returns 422. (The *value* side is safe — it is always an
  integer Telegram user ID, never student-controlled text.)
- **Single line text** is the safest type. A **Number** field also works: the
  formula concatenates with `&''`, which converts using the *stored* value and
  drops trailing zeros, so `7185151344` compares as `"7185151344"` either way.
  A **Formula**, **Autonumber**, **Created time** or **Lookup** field will
  *not* work — Airtable cannot prefill a computed field.
- Do not rename the field later. Renaming it does not break Airtable, it breaks
  the bot (422 `UNKNOWN_FIELD_NAME` / invalid formula → "couldn't verify"
  forever). Put that warning in the field's description.

Optional: add a **Full name** field if you want the roster to carry the name
from the form (`GATE_AIRTABLE_NAME_FIELD`).

## 3. Build the form

1. Create a form on that table (**Forms → + New form**, or a **Form view** on
   the table — both work).
2. Add the questions you want students to answer.
3. **Keep `tg_id` on the form.** Add it as a field on the form canvas, leave it
   **not required**, and give it a label like "Do not edit — set automatically".
   Do **not** remove it from the form and do **not** switch it off in the
   builder's field-visibility toggles.

   This is the single most common way to get this wrong. Airtable can only
   submit a value for a field the form actually contains — a field that is not
   on the form is not part of the submission, so `prefill_tg_id` would be
   ignored and every row would arrive with an empty `tg_id`. The
   `hide_tg_id=true` URL parameter is what conceals it from the student while
   still submitting the value: "the form field will not be shown to the user.
   However, the value will still be entered into the record when the form is
   submitted."

4. **Publish** the form.

## 4. Copy the form share link → `GATE_FORM_URL`

**Share form → Copy link.** You will get one of these shapes:

| Form kind | URL shape |
| --- | --- |
| Form view (current) | `https://airtable.com/appXXXXXXXXXXXXXX/shrYYYYYYYYYYYYYY` |
| Form view (legacy link) | `https://airtable.com/shrYYYYYYYYYYYYYY` |
| Interface / standalone form | `https://airtable.com/appXXXXXXXXXXXXXX/pagZZZZZZZZZZZZZZ/form` |

All three accept the same `prefill_` / `hide_` parameters — the prefill docs are
written for form-view URLs and state you "can generally follow the same steps
for standalone forms"; interface forms have supported `prefill_`/`hide_` since
July 2023. Paste the link **exactly as copied**, with no query string and no
`#fragment`: the bot appends `?prefill_…&hide_…` itself (it only switches to `&`
if the URL already contains a `?`).

If you use an interface form, verify prefill manually in step 9 before going
live — interface forms have a longer tail of prefill quirks (e.g. linked-record
fields).

## 5. Create a scoped personal access token → `GATE_AIRTABLE_TOKEN`

1. Go to **https://airtable.com/create/tokens** (Developer hub → *Personal
   access tokens*) → **Create token**.
2. **Name:** `alumnigate-read`.
3. **Scopes** → **+ Add a scope**:
   - **`data.records:read`** — *required*, and the only scope the bot needs.
     It is the documented scope for `GET /v0/{baseId}/{tableIdOrName}`
     ("See the data in records").
   - **`schema.bases:read`** — *optional, recommended*. The bot never uses it;
     `scripts/check_airtable.py` uses it to confirm the `tg_id` field exists and
     to report its type. Without it the checker falls back to a probe that can
     still detect a missing field, just without the type. Add it while setting
     up and remove it afterwards if you prefer a minimal token.
   - Do **not** add `data.records:write` or any `…:write` scope. The bot only
     reads.
4. **Access** → **+ Add a base** → pick **only** the base from step 1. Do not
   pick "all current and future bases in a workspace" — that hands a leaked
   token your whole workspace.
5. **Create token**, then copy the value **immediately** — it is shown once. It
   starts with `pat…` and contains a `.`; copy the whole string including
   everything after the dot. PATs do not expire; they live until you delete,
   regenerate or edit them.

Store it in `.env` only. Never commit it. (`.env` is already gitignored.)

## 6. Find the base ID and the table name → `GATE_AIRTABLE_BASE_ID`, `GATE_AIRTABLE_TABLE`

Open the table in the browser. The URL reads:

```
https://airtable.com/appR7fQ2mK9wLzXyz/tblqA3n8ZP1vKdW6c/viwE3o43HKcqz6hFE
                     ^ base ID (app…)  ^ table ID (tbl…)  ^ view ID (viw…)
```

- `GATE_AIRTABLE_BASE_ID` = the `app…` segment.
- `GATE_AIRTABLE_TABLE` = either the table **name** (`Onboarding`) or the table
  **ID** (`tblqA3n8ZP1vKdW6c`). The table ID is the more robust choice — it
  survives renaming the table. Never paste the `viw…` view ID here.

The base ID is also printed at the top of the base's API docs (help menu → API
documentation).

## 7. Fill in `.env`

```dotenv
# Read-only PAT, scoped to this one base (step 5)
GATE_AIRTABLE_TOKEN=patXXXXXXXXXXXXXX.<64-hex-characters-from-step-5>
GATE_AIRTABLE_BASE_ID=appR7fQ2mK9wLzXyz
GATE_AIRTABLE_TABLE=tblqA3n8ZP1vKdW6c
GATE_AIRTABLE_TG_FIELD=tg_id
GATE_AIRTABLE_DONE_FIELD=
GATE_AIRTABLE_NAME_FIELD=
GATE_FORM_URL=https://airtable.com/appR7fQ2mK9wLzXyz/shrK4pQ7nT2vBxLmE
GATE_VALUES_DOC_URL=https://example.com/alumni-values
GATE_POLL_INTERVAL_MINUTES=3
```

| Variable | Required | Notes |
| --- | --- | --- |
| `GATE_AIRTABLE_TOKEN` | yes | Step 5. Blank ⇒ `airtable_ready()` is False ⇒ the gate never verifies anyone. |
| `GATE_AIRTABLE_BASE_ID` | yes | `app…`, step 6. |
| `GATE_AIRTABLE_TABLE` | yes | Table name or `tbl…` ID, step 6. |
| `GATE_AIRTABLE_TG_FIELD` | no (default `tg_id`) | Must match the Airtable field **name** character for character. Not a `fld…` ID. |
| `GATE_AIRTABLE_DONE_FIELD` | no | Leave **blank** normally: an Airtable form row only exists after a submit, so existence *is* completion. Set it only if other things write rows to this table. See the caveat in "Notes" — it interacts badly with duplicate submissions. |
| `GATE_AIRTABLE_NAME_FIELD` | no | A text field with the student's full name. A typo here breaks the background poll only (see troubleshooting). |
| `GATE_FORM_URL` | yes | Step 4, no query string. |
| `GATE_VALUES_DOC_URL` | no | Values doc shown to students. |
| `GATE_POLL_INTERVAL_MINUTES` | no (default 3) | See the API-call budget above. `0` disables the poll. |

Leave `GATE_LIVE` off until step 10.

### Worked example — a finished personalized form URL

For Telegram user `7185151344`, with `GATE_FORM_URL` and
`GATE_AIRTABLE_TG_FIELD=tg_id` as above, the bot sends:

```
https://airtable.com/appR7fQ2mK9wLzXyz/shrK4pQ7nT2vBxLmE?prefill_tg_id=7185151344&hide_tg_id=true
```

Interface-form equivalent:

```
https://airtable.com/appR7fQ2mK9wLzXyz/pagX8sD1qN4tYzVbH/form?prefill_tg_id=7185151344&hide_tg_id=true
```

And the lookup the bot then makes (same request, as curl):

```bash
curl -s -G "https://api.airtable.com/v0/appR7fQ2mK9wLzXyz/tblqA3n8ZP1vKdW6c" \
  -H "Authorization: Bearer $GATE_AIRTABLE_TOKEN" \
  --data-urlencode "filterByFormula={tg_id}&''='7185151344'" \
  --data-urlencode "maxRecords=1"
```

A verified student looks like `{"records":[{"id":"rec…","fields":{"tg_id":"7185151344", …}}]}`.
An unverified student looks like `{"records":[]}`. Anything else — an
`{"error":…}` body or a non-200 status — is the "couldn't verify" case.

If a field name did contain a space (`Telegram ID`), the URL parameters would be
`prefill_Telegram%20ID=…&hide_Telegram%20ID=true` (`+` also works) and the
formula would be `{Telegram ID}&''='…'`. Avoid needing this.

## 8. Run the pre-flight checker

```bash
python scripts/check_airtable.py 7185151344
```

It reads the `GATE_AIRTABLE_*` / `GATE_FORM_URL` variables from the environment
(and from `.env`), then checks, one PASS/FAIL line each: config sanity, token
authentication, base+table reachability with `data.records:read`, that the
`tg_id` field exists (and its type), that the optional done/name fields exist,
the real `filterByFormula` lookup for the ID you passed, the targeted `OR()`
query the poll performs plus its monthly API-call cost, and the exact
personalized form URL. Exit status is non-zero if any check fails.

Every FAIL prints what to change. Do not go live with a FAIL — a FAIL here is
the "students are told to try again forever" state.

## 9. End-to-end test with your own Telegram ID

The checker cannot prove that Airtable actually *stores* the prefilled ID; only
a real submission can.

1. Run `python scripts/check_airtable.py <your telegram id>` and copy the
   personalized URL it prints.
2. Open it. Confirm you **cannot see** a `tg_id` question on the form.
3. Submit the form.
4. Open the Airtable table. Confirm the new row's `tg_id` cell contains your
   numeric Telegram ID — **not blank**. A blank cell means step 3 (field not on
   the form) or a field-name mismatch, and it is the failure that looks most
   like success: submissions arrive, but no one is ever verified.
5. Re-run `python scripts/check_airtable.py <your telegram id>`. The lookup
   check must now report 1 matching record.
6. Delete the test row.

## 10. Go live

Set `GATE_LIVE=true` (with `GATE_GROUP_ID` set), restart the bot, and watch the
log for `Airtable lookup failed` / `Airtable fetch_completed failed` — those two
lines are your only warning that verification is broken.

---

## Troubleshooting: misconfiguration → what you actually observe

Remember: the student-facing symptom of *every* row below is identical
("couldn't verify, try again in a minute"). Distinguish them by the bot log and
by `scripts/check_airtable.py`.

| Misconfiguration | Observed symptom | Log / checker signature | Fix |
| --- | --- | --- | --- |
| `GATE_AIRTABLE_TOKEN`/`BASE_ID`/`TABLE` blank | Nobody is ever verified; **no** Airtable errors in the log at all (the code returns before making a request) | checker: config FAIL | Fill all three; `airtable_ready()` needs all of them |
| Token truncated (ID only, missing the part after the `.`) | Nobody verified | `401` `AUTHENTICATION_REQUIRED` / "unauthorized, invalid authentication token" | Re-copy the whole token; regenerate if lost |
| Token missing `data.records:read` | Nobody verified | `403` `INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND` | Add the scope at airtable.com/create/tokens |
| Base not in the token's **Access** list | Nobody verified — looks exactly like "base doesn't exist" | `403` `INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND` | Add the base under Access |
| Wrong `GATE_AIRTABLE_BASE_ID` (e.g. a `tbl…` or workspace ID) | Nobody verified | `404 NOT_FOUND` or `403` | Re-read the `app…` segment from the URL |
| Wrong table name/ID, or a `viw…` view ID pasted in | Nobody verified | `404 NOT_FOUND` / `FAILED_STATE_CHECK` | Use the table name or `tbl…` ID |
| `GATE_AIRTABLE_TG_FIELD` misspelled, or a `fld…` ID | Nobody verified, even for students whose row exists | `422` `INVALID_FILTER_BY_FORMULA` — "Unknown field names: …" | Use the exact field **name** |
| Field renamed in Airtable after setup | Verification worked, then stopped for everyone at once | same `422` as above | Rename back, or update `.env` and restart |
| `}`, `'` or `"` in the field name | Nobody verified | `422 INVALID_FILTER_BY_FORMULA` | Rename the field to plain ASCII |
| `GATE_AIRTABLE_NAME_FIELD`/`DONE_FIELD`/`USERNAME_FIELD` misspelled | **Button works, background poll never verifies anyone.** Students who tap the button get in; students who wait never do | `422` `UNKNOWN_FIELD_NAME` from `fetch_completed_for` only (`lookup` sends no `fields[]`) | Fix the name or blank the variable |
| `tg_id` not on the form (removed / toggled off in the builder) | Submissions **do** arrive in Airtable, students are still never verified | no API error at all; checker's lookup finds 0 records; `tg_id` cells are blank | Put the field back on the form; hide it with `hide_tg_id=true` only |
| `GATE_FORM_URL` already contains `?…` or a `#fragment` | Students see the `tg_id` question, or the prefill is ignored | rows arrive with blank/edited `tg_id` | Paste the bare share link from **Share form → Copy link** |
| `tg_id` created as Formula/Autonumber/Created-time | Cells populate with something that is not the Telegram ID; nobody verified | lookup finds 0 records | Recreate as Single line text (a new field gets a new ID — update `.env` if you used IDs anywhere) |
| Free-plan monthly cap (1,000 calls) exhausted by the poll | Worked for ~2 days after go-live, then nobody is verified — including the button | `429` on every call; Workspace settings → Usage shows the cap hit | `GATE_POLL_INTERVAL_MINUTES=0` or `60`, or upgrade the workspace |
| >125 students waiting at once (6+ chunks), or several bot instances | Intermittent: some polls verify people, some don't | sporadic `429 RATE_LIMIT_REACHED` (5 req/s per base, 30s lockout) | Longer poll interval; run one instance |
| Airtable outage / deploy | Intermittent "try again in a minute", self-heals | `503 RETRIABLE_ERROR` | Wait; the poll retries |
| Bot's own network/DNS/proxy blocked | Nobody verified | `httpx.ConnectError` / timeout after 15s | Fix egress to `api.airtable.com:443` |

Two things worth building on top, once you have seen this list: the log lines are
the only signal, so consider alerting on `Airtable lookup failed`, and consider
having the bot tell admins (not students) when N consecutive verifications
return `None`.

---

## Notes and caveats

- **`hide_` is not a security measure.** Airtable says so explicitly: "The
  prefilled value will be visible in the URL that is generated." A student can
  read their own Telegram ID (harmless) or edit the URL to submit someone
  else's ID, which would verify *that* person. Acceptable here; know that it
  exists.
- **Duplicate submissions.** Airtable adds one row per submit, so a student who
  submits twice gets two rows. With `GATE_AIRTABLE_DONE_FIELD` blank this is
  harmless. If you *do* set a done field, note that `lookup()` asks for
  `maxRecords=1`: it inspects only the first matching row, so if the earlier row
  has an empty done field the student stays unverified even though a later,
  complete row exists. Another reason to leave the done field blank.
- **Prefill cannot edit an existing row.** Airtable prefill "cannot be used to
  edit the information that already exists in your base" — every submission is a
  new row.
- **Prefilled links are capped at 8,000 characters** (and 1,000 characters for
  clickable links inside emails). Irrelevant at this size, but do not build
  giant prefills.
- **The `&''` cast in the formula is sound.** `{tg_id}&''='7185151344'` forces
  the cell to text before comparing, so the same formula matches whether the
  field is text or a number; Airtable's `&` concatenation uses the stored value,
  not the display formatting. Keep it.
- **Field IDs are only half-supported.** `prefill_fldXXXXXXXXXXXXXX=` and
  `fields[]=fldXXXXXXXXXXXXXX` both work, but `filterByFormula` accepts names
  only — so a `fld…` value in `GATE_AIRTABLE_TG_FIELD` breaks the button. Use
  the name.
- **Rotating the token** requires nothing but editing `.env` and restarting; the
  bot reads it once at import.

---

## Sources

Verified July 2026.

- Prefilling a form via encoded URL — <https://support.airtable.com/docs/prefilling-a-form-via-encoded-url>
- Hide form field by URL parameter (announcement) — <https://community.airtable.com/announcements-6/new-feature-hide-form-field-by-url-parameters-1492>
- Prefill/hide in Interface Designer forms — <https://community.airtable.com/other-questions-13/prefilling-and-hiding-fields-within-a-form-in-interface-designer-18129>
- Building and sharing forms — <https://support.airtable.com/docs/building-and-sharing-forms-in-airtable>
- Scopes — <https://airtable.com/developers/web/api/scopes>
- List records (`data.records:read`, params, 16,000-character URL limit) — <https://airtable.com/developers/web/api/list-records>
- `filterByFormula` uses field names only — <https://support.airtable.com/docs/airtable-web-api-using-filterbyformula-or-sort-parameters>
- Creating personal access tokens (scopes + per-base access) — <https://support.airtable.com/docs/creating-personal-access-tokens>
- Finding Airtable IDs — <https://support.airtable.com/docs/finding-airtable-ids>
- Rate limits (5 req/s per base, 429, 30s) — <https://airtable.com/developers/web/api/rate-limits>
- Managing API call limits (1,000/month Free, 100,000/month Team) — <https://support.airtable.com/docs/managing-api-call-limits-in-airtable>
- API errors — <https://airtable.com/developers/web/api/errors>
- API common troubleshooting — <https://support.airtable.com/docs/airtable-api-common-troubleshooting>
- Base schema endpoint (`schema.bases:read`) — <https://airtable.com/developers/web/api/get-base-schema>
- `GET /v0/meta/whoami` (no scopes required) — <https://airtable.com/developers/web/api/get-user-id-scopes>
- Number→text conversion in formulas — <https://support.airtable.com/docs/converting-numbers-and-text-in-a-formula-field>
