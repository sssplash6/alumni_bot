# Deploying the Alumni Bot to Render

Every platform fact below was checked against Render's live docs and pricing page
on **2026-07-27**. Sources are linked inline; re-check anything plan- or
price-dependent before you spend money.

The blueprint that implements all of this is [`render.yaml`](../render.yaml) at
the repo root, and it validates cleanly against Render's official schema
(<https://render.com/schema/render.yaml.json>).

---

## TL;DR

| Question | Answer |
| --- | --- |
| Service type | **Background worker** (`type: worker`) |
| Free tier? | **No.** Workers start at Starter, **$7/month** |
| Persistent SQLite? | **Yes**, via a 1 GB persistent disk mounted at `/var/data` (+$0.25/month) |
| Total | **~$7.25/month** on a $0 Hobby workspace |
| Deploy downtime | A few seconds per deploy, **by design** — and required |
| Instances | Exactly **1**, forever |

---

## 1. Why a background worker (and not a web service)

`main.py` calls `app.updater.start_polling(...)` and then blocks on
`asyncio.Event().wait()`. It opens an outbound long-poll to Telegram and **never
binds a port**.

Render background workers "run continuously without receiving incoming network
traffic" and "do *not* expose a URL or internal hostname" — an exact description
of this process ([service types](https://render.com/docs/service-types),
[background workers](https://render.com/docs/background-workers)).

Deploying it as a **web service** would be a mistake: Render expects a web
service to listen on `$PORT`, health checks would never pass, and the deploy
would be cancelled or the instance cycled indefinitely.

A **cron job** is also wrong — the bot must run continuously, and cron jobs
cannot have a persistent disk at all
([disks](https://render.com/docs/disks)).

### The cost of that choice: no free tier

Render's free tier covers "Web services, Render Postgres databases, Render Key
Value instances" — and static sites. Background workers, cron jobs and private
services are **not** on the list ([Deploy for
Free](https://render.com/docs/free)).

Two independent confirmations:

- [Instance types](https://render.com/docs/compute-plans): the *Web service* tab
  lists a Free row (512 MB / 0.1 CPU); the *Private service / Background worker*
  tab starts at **Starter**.
- [Blueprint spec](https://render.com/docs/blueprint-spec): `plan: free` is
  "*not available for private services, background workers, or cron jobs*".

There is no trick around this. A worker costs money from day one.

---

## 2. Persistence: the ephemeral-filesystem trap

> "By default, Render services have an **ephemeral filesystem**. This means that
> without a persistent disk, any changes you make to a service's local files are
> **lost** every time the service redeploys or restarts."
> — [Persistent Disks](https://render.com/docs/disks)

For this repo that is not a cosmetic problem. `DB_PATH` defaults to
`alumni_bot.db` **next to `config.py`**, i.e. inside the checked-out repo, which
Render rebuilds from scratch on every deploy. Left alone, every deploy would
destroy every mentor/mentee application, every fair submission and all Alumni
Gate state — none of which is recoverable from Telegram. Backups written to the
default `BACKUP_DIR` would be wiped in the same instant.

**The answer: attach a persistent disk.** Disks are supported on paid web
services, private services and background workers. Only paths under the mount
path survive; the rest of the filesystem stays ephemeral.

The blueprint therefore does two things that must stay in sync:

```yaml
disk:
  name: alumni-bot-data
  mountPath: /var/data
  sizeGB: 1
envVars:
  - key: DB_PATH
    value: /var/data/alumni_bot.db     # NOT the repo directory
  - key: BACKUP_DIR
    value: /var/data/backups
```

Details that matter:

- `DB_PATH` sits at the **mount root**, not in a subdirectory. `init_db()`
  connects straight to the file and SQLite will not create missing parent
  directories, so `/var/data/db/alumni_bot.db` would fail on first boot with
  `unable to open database file`. `BACKUP_DIR` *may* nest, because
  `database.backup()` calls `mkdir(parents=True, exist_ok=True)`.
- `/var/data` is Render's own suggested "standalone directory" for this pattern.
  You may not mount at `/`, `/opt`, `/opt/render`, `/opt/render/project`,
  `/opt/render/project/src`, `/home`, `/home/render`, `/etc` or `/etc/secrets`
  (subdirectories of those are fine).
- **1 GB is the minimum** and is wildly generous here — the DB is tens of KB and
  `BACKUP_KEEP` defaults to 14 snapshots. You can *increase* a disk later
  (no downtime) but you can **never shrink** it, so start at 1 GB.
- Render snapshots the disk automatically **every 24 h**, retained for **at least
  7 days**. Restores are whole-disk only. Note Render's own warning not to use
  disk snapshots to recover a database — use the bot's `/backup` snapshots
  (SQLite's online backup API) for that, and treat disk snapshots as
  infrastructure insurance.
- The disk is invisible to the **build command**, the **pre-deploy command**, and
  **one-off jobs** — all three run on separate compute. Do not try to seed or
  migrate the DB from `buildCommand`.
- No other service can read this disk.

Because persistence *is* achievable on the Starter plan, **no Postgres migration
is required.** See §10 for what that path would cost if you ever want it.

---

## 3. Zero-overlap deploys, and why the disk is load-bearing twice

Telegram permits exactly one `getUpdates` poller per bot token. A second one
produces `Conflict: terminated by other getUpdates request` and updates get split
unpredictably between the two processes.

Render's default deploy sequence would cause precisely that. From
[How deploys work](https://render.com/docs/deploys), the zero-downtime sequence
"applies to web services, private services, **background workers**, and cron
jobs": Render spins up the new instance while the old one keeps running, and only
"**after 60 seconds**" sends `SIGTERM` to the original. That is a guaranteed
~60-second window with two live pollers on every single deploy.

Attaching a disk removes the overlap, and Render is explicit about why:

> "Adding a disk to a service prevents zero-downtime deploys. This is because:
> when you redeploy your service, Render stops the existing instance **before**
> bringing up the new instance. […] This is a necessary safeguard to prevent data
> corruption that can occur when different versions of an app read and write to
> the same disk simultaneously."

So the disk you need for SQLite is also the mechanism that gives you
stop-then-start deploys. The price is a few seconds of downtime per deploy —
correct for this workload.

Guardrails that follow from this:

- **`numInstances` stays 1.** "You can't scale a service to multiple instances if
  it has a disk attached" — and horizontal scaling would break the bot anyway.
  Never touch the Scaling tab.
- **Preview environments are off** (`previews.generation: 'off'`). A preview env
  boots a second copy of the bot.
- **Never run a local copy against the production `BOT_TOKEN`** while the Render
  worker is live. Use a separate BotFather bot for local work.
- Only one deploy runs at a time per service, so you cannot accidentally stack
  two deploys into an overlap.

### What a deploy costs you beyond the seconds of downtime

Two repo-specific behaviours make deploys more disruptive than the clock suggests:

1. `start_polling(drop_pending_updates=True)` — everything Telegram queued while
   the bot was down is **discarded**, not replayed. Messages sent during the
   restart window are silently lost.
2. The `ConversationHandler`s are built without persistence, so their state lives
   in process memory. A restart drops every half-finished mentor / mentee / fair
   application; the user's next button press hits nothing.

That is why `render.yaml` sets **`autoDeployTrigger: 'off'`** — deploy
deliberately, when nobody is mid-flow, rather than on every push. Change it to
`commit` if you would rather have push-to-deploy.

Also note that `main.py` installs no `SIGTERM` handler (it blocks on
`asyncio.Event().wait()`), so on shutdown the process dies immediately and the
`finally: app.updater.stop()` block never runs. That is *not* a data risk here —
every DB call opens, commits and closes its own `aiosqlite` connection, so there
is no open transaction to lose — which is why `maxShutdownDelaySeconds` is set to
10 rather than the default 30: there is nothing to wait for.

---

## 4. Prerequisites

- A Render account and a workspace. **Hobby ($0/month)** is enough: it allows up
  to 25 services and includes 5 GB of egress. You are not forced onto a paid
  workspace plan just to run a paid worker.
- The GitHub repo connected to Render (`git@github.com:sssplash6/alumni_bot.git`,
  branch `main`), with the Render GitHub app authorised for it.
- `BOT_TOKEN` from @BotFather and your admin Telegram user IDs.
- Optional but recommended: the [Render CLI](https://render.com/docs/cli)
  (`brew install render-oss/render/render`, then `render login`).
- Optional: an SSH key registered with Render (Account Settings → SSH Public
  Keys). You need this to pull DB backups off the box — see §9.

Commit `render.yaml` to `main` before you start; the Blueprint flow reads it from
the repo.

---

## 5. Create the service

### Option A — Blueprint (recommended, reproducible)

1. Render Dashboard → **New** → **Blueprint**.
2. Pick the `alumni_bot` repo and branch `main`. Render finds `render.yaml`.
3. Render prompts you for every variable declared `sync: false`. Fill in:

   | Variable | Value | Notes |
   | --- | --- | --- |
   | `BOT_TOKEN` | from @BotFather | secret |
   | `ADMIN_IDS` | `123456789,987654321` | comma-separated Telegram user IDs; **at least one required** or the process refuses to start |
   | `GATE_LIVE` | *leave blank* | keeps the Alumni Gate dormant for the first deploy |
   | `GATE_GROUP_ID` | *blank for now* | the one alumni group; bot must be an admin there |
   | `GATE_MONITORED_GROUP_IDS` | *blank for now* | comma-separated chat IDs |
   | `GATE_AIRTABLE_TOKEN` | Airtable PAT | secret |
   | `GATE_AIRTABLE_BASE_ID` | `appXXXXXXXXXXXXXX` | |
   | `GATE_AIRTABLE_TABLE` | table name or `tblXXXXXXXXXXXXXX` | |
   | `GATE_FORM_URL` | public Airtable form share link | |
   | `GATE_VALUES_DOC_URL` | values doc link | |

   Blank is safe for every `GATE_*` value — `gate/settings.py` parses `""` into a
   zero/empty default, and the gate only activates when `GATE_LIVE` is truthy
   **and** `GATE_GROUP_ID` is non-zero. `BOT_TOKEN` and `ADMIN_IDS` are the only
   two that must be real.

4. **Apply**. Render creates the worker *and* the 1 GB disk from the blueprint —
   no separate disk step needed.

> **Blueprint quirk to remember:** Render prompts for `sync: false` variables
> **only during initial creation**. On later syncs of `render.yaml` it *ignores*
> them. Add or change those values by hand in the Dashboard afterwards.
> ([spec](https://render.com/docs/blueprint-spec))

### Option B — Manual dashboard creation

If you would rather click through it: **New → Background Worker**, pick the repo,
then set Runtime `Python 3`, Instance Type `Starter`, Build Command
`pip install -r requirements.txt`, Start Command `python main.py`. Click
**Advanced** at the bottom of the creation form to add the disk
(mount path `/var/data`, size 1 GB) *before* the first deploy, and add every
environment variable from §5.A plus:

```
PYTHON_VERSION=3.11.15
PYTHONUNBUFFERED=1
DB_PATH=/var/data/alumni_bot.db
BACKUP_DIR=/var/data/backups
```

Then set Auto-Deploy to **Off** under Settings.

### Python version pinning

Render's default is **Python 3.14.3** for services created on or after
2026-02-11 ([python version](https://render.com/docs/python-version)).
`python-telegram-bot` 21.10 declares support only through **3.13** (its PyPI
classifiers stop there), and this repo is developed on 3.11 (see the
`cpython-311` bytecode in `__pycache__`). The blueprint therefore pins
`PYTHON_VERSION=3.11.15` — the latest 3.11 release, which is a real published
version.

`PYTHON_VERSION` requires a **fully qualified** `x.y.z`. If a build ever fails
resolving that exact patch, `3.11.11` is a safe fallback (Render shipped it as
its own default from 2024-12-16 to 2025-06-12).

> A `.python-version` file would also work and allows omitting the patch
> (`3.11`), but the env-var approach keeps everything in `render.yaml`.

### How `.env` interacts with Render's environment variables

`config.py` runs `load_dotenv(Path(__file__).parent / ".env")`. `.env` is
gitignored, so it will **not** exist on Render. That is harmless: in
python-dotenv 1.0.1 (the pinned version) a missing file makes `load_dotenv`
return `False`, and the "could not find configuration file" warning is only
logged when `verbose=True`, which is not the default. Nothing raises, nothing is
printed.

Render env vars are injected as real process environment variables, so
`os.environ[...]` in `config.py` and `gate/settings.py` reads them directly. No
`.env` file is needed or wanted.

If you ever *do* want a file-based config on Render, note that a
[Secret File](https://render.com/docs/configure-environment-variables) named
`.env` is placed at `/etc/secrets/.env` **and**, for non-Docker services, in the
service root directory — which is exactly where `config.py` looks. Even then
Dashboard env vars still win, because `load_dotenv` defaults to
`override=False`. Prefer plain environment variables; the Secret File route is a
second source of truth waiting to drift.

---

## 6. First deploy

If you used the Blueprint flow, the first deploy starts automatically. Otherwise
trigger it from **Manual Deploy → Deploy latest commit** (or `render deploys
create`).

Watch the build log for:

- `pip install -r requirements.txt` succeeding, including
  `python-telegram-bot[job-queue]` (the `job-queue` extra is load-bearing — the
  DB backup job, the gate's Airtable poll and the announcement re-post all need
  `JobQueue`; without it `app.job_queue` is `None` and they silently never run).
- The Python version in the log matching your pin.

---

## 7. Verify it is actually running

There is no URL to curl. Check in this order:

1. **Dashboard**: the service shows a green **Live** status and the Events tab
   shows `Deploy live`.
2. **Logs** (§8) show the `logging.basicConfig` banner from `main.py` and, if
   backups are enabled, a line like
   `Database backups every 24 h, keeping 14`.
3. **Telegram**: message the bot `/start` in a private chat. This is the only
   real proof that polling works.
4. **`/backup`** as an admin. A successful reply proves the bot can write to the
   persistent disk. Confirm the path it reports is under `/var/data`.
5. **Disk usage**: service → **Disks** page shows non-zero usage after the first
   write.

The single most important verification is the one people skip: **deploy twice and
confirm the data survived.** Add a test application, redeploy, and check it is
still there. That is the only way to know `DB_PATH` really points at the disk.

---

## 8. Logs

- **Dashboard**: service → **Logs** (live tail, filterable).
  ([in-dashboard logs](https://render.com/docs/logging))
- **CLI**: `render services logs` (omit the ID for an interactive picker), or
  `render services logs <SERVICE_ID>`.
- For long-term retention, set up a
  [log stream](https://render.com/docs/log-streams) to syslog/HTTPS. Dashboard
  log retention is limited.

`PYTHONUNBUFFERED=1` is set so nothing sits in a stdout buffer while you watch.

Things worth grepping for: `Conflict: terminated by other getUpdates request`
(two pollers — see §3), `unable to open database file` (`DB_PATH` wrong or its
parent directory missing), `Database backup failed`.

---

## 9. Restart safely, and pull a backup off the box

### Restarting

Service → **Deploys** → **Manual Deploy → Restart service**. Render calls this
"a special form of manual deploy": it creates a new instance and swaps to it.
Normally that swap is zero-downtime, but **because this service has a disk
attached the swap is stop-then-start**, which is exactly what you want — no
double poller.

A restart reuses "the exact same Git commit and configuration as the running
instance at the time of the restart". Consequence: **if you changed environment
variables and have not redeployed, a restart does not pick them up.** To apply
env-var changes, do a real deploy (`Manual Deploy → Deploy latest commit`, or
save the variable with the "rebuild and deploy" option).

Safe restart procedure for this bot:

1. Check the logs / your admin chat that nobody is mid-application (their
   in-memory conversation state will be lost).
2. Restart.
3. Confirm `Deploy live`, then `/start` the bot in Telegram.

To take the bot down deliberately, suspend it: service → **Settings → Suspend**
(also available as a bulk action from the Dashboard's service list, and via the
API's [suspend](https://api-docs.render.com/reference/suspend-service-1) /
[resume](https://api-docs.render.com/reference/resume-service-1) endpoints).
Suspending stops the poller cleanly and stops compute charges for that period.
`maintenanceMode` in the blueprint spec is web-services only and does not apply
here.

### Pulling a DB backup off the box

Render documents SCP over its SSH access for exactly this
([transferring files](https://render.com/docs/disks)). SSH is available for
**paid** web services, private services and background workers — which this is —
and requires a registered SSH key ([SSH](https://render.com/docs/ssh)).

1. One-time setup: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`, then paste the
   `.pub` contents into Render Dashboard → Account Settings → **SSH Public Keys**.
2. Grab your exact SSH target from the service's **Connect → SSH** tab. It looks
   like `srv-xxxxxxxxxxxx@ssh.oregon.render.com`.
3. Take a fresh snapshot first: send **`/backup`** to the bot as an admin. It
   uses SQLite's online backup API, which is safe on a live database (a plain
   file copy is not — it can catch a half-written transaction). Note the path it
   replies with.
4. Copy it down:

   ```bash
   # list what's on the disk
   ssh srv-xxxxxxxxxxxx@ssh.oregon.render.com 'ls -la /var/data /var/data/backups'

   # pull one snapshot (use -s for SFTP, as Render recommends)
   scp -s srv-xxxxxxxxxxxx@ssh.oregon.render.com:/var/data/backups/alumni_bot-20260727-120000-000000.db ./

   # or the whole backups directory
   scp -s -r srv-xxxxxxxxxxxx@ssh.oregon.render.com:/var/data/backups ./render-backups
   ```

   Copy a `/backup` snapshot, **not** the live `/var/data/alumni_bot.db`.

`magic-wormhole` is pre-installed on Render's native runtimes if you would rather
not set up SSH keys: open service → **Shell**, run `wormhole send <file>`, then
`wormhole receive` locally with the printed code.

### Uploading your existing local database (one-time migration)

You already have a populated `alumni_bot.db` locally. To carry it over:

SSH connects to a *running* instance, so you cannot upload into a suspended
service. Do it while the bot is live, at a quiet moment, and restart immediately:

1. Deploy once so the disk exists and the service is Live. Do **not** enable the
   gate or announce the bot yet.
2. Upload, overwriting the freshly-created empty database:

   ```bash
   scp -s ./alumni_bot.db srv-xxxxxxxxxxxx@ssh.oregon.render.com:/var/data/alumni_bot.db
   ```

3. **Restart immediately** (§9) so the running process reopens the new file.
   `init_db()` uses `CREATE TABLE IF NOT EXISTS`, so an existing database is
   adopted rather than clobbered, and any newer tables are added.
4. Verify with an admin command that the old records are visible, then go live.

Do not attempt this from the build command — the build runs on separate compute
and cannot see the disk.

---

## 10. Costs, honestly

Checked on Render's [pricing page](https://render.com/pricing), 2026-07-27:

| Line item | Cost |
| --- | --- |
| Hobby workspace | **$0/month** (25 services, 5 GB egress included) |
| Background worker, Starter (512 MB RAM, 0.5 CPU) | **$7.00/month** |
| Persistent disk, 1 GB @ $0.25/GB/month | **$0.25/month** |
| **Total** | **≈ $7.25/month** |

Context for those numbers:

- Compute is **prorated by the second**, and paid instances "run continuously
  unless you suspend them" — there is no scale-to-zero for a worker, and you
  would not want one for a poller.
- Next step up if 512 MB ever gets tight: Standard, 2 GB / 1 CPU, **$25/month**.
  It should not be needed — PTB + httpx + aiosqlite with a handful of jobs is a
  small footprint. Watch the Metrics tab before upgrading.
- Egress: 5 GB included on Hobby; overage $0.15/GB. A Telegram bot's traffic is
  negligible, unless you start relaying media.
- A Pro workspace ($25/month) buys autoscaling and full-stack previews — both
  actively useless here, since this service must never scale past one instance
  and must never have a preview twin. **Stay on Hobby.**

### There is no free way to do this on Render

Worth being blunt, since it is the most likely wrong turn: the only free
always-on-ish compute Render offers is a **web service**, and a free web service
"spins down after 15 minutes without inbound traffic", taking ~1 minute to spin
back up. Free instances also cannot have persistent disks, and Render "may
suspend a Free web service that initiates an uncommonly high volume of traffic
over the public internet" — which is a fair description of a continuous
`getUpdates` loop. Wrapping the bot in a dummy HTTP server plus an external
pinger to keep a free instance awake would give you an unreliable bot with an
ephemeral database, and would be working against the platform's stated rules.
Pay the $7.

### If you ever do want Postgres instead of SQLite-on-a-disk

You do **not** need this — persistence works on Starter, and nothing here has
been migrated. Recording the shape of it for future reference only:

- Cheapest paid Render Postgres is **Basic-256mb at $6/month** (+$0.30/GB storage
  beyond the plan default). Free Postgres **expires 30 days after creation**, so
  it is not an option for live data.
- Running total would become ~$13/month, and the disk could be dropped — but
  dropping the disk brings back **zero-downtime deploys and therefore the
  double-poller conflict** (§3). You would need another way to guarantee a single
  instance, so this is not a straight win.
- Code effort is not trivial: **44 `aiosqlite.connect(...)` call sites**
  (36 in `database.py`, 8 in `gate/db.py`), plus schema/DDL differences
  (`INTEGER PRIMARY KEY AUTOINCREMENT`, `INSERT OR REPLACE`/upserts, `?` vs `$1`
  placeholders, boolean and datetime handling), plus rewriting `database.backup()`
  (SQLite's online backup API has no Postgres equivalent — you would use
  `pg_dump`, or lean on Render's managed backups/PITR), plus reworking the test
  suite, which currently runs against real SQLite files.

---

## 11. Gotchas checklist

- [ ] **Ephemeral filesystem.** If `DB_PATH` is not under the disk mount path,
      every deploy silently destroys all applications and submissions. This
      fails *quietly* — you get a working bot with an empty database. Verify by
      deploying twice and checking the data survived.
- [ ] **`BACKUP_DIR` too.** Backups written to the default location are wiped
      alongside the DB, so the backup job would give you false comfort.
- [ ] **One instance, always.** `numInstances: 1`, no autoscaling, previews off,
      no local copy on the production token. Two pollers → `Conflict: terminated
      by other getUpdates request` and randomly split updates.
- [ ] **Deploys are not free of consequences.** `drop_pending_updates=True`
      discards anything queued during the restart, and in-memory
      `ConversationHandler` state is lost. Deploy when it is quiet; auto-deploy
      is off by default in the blueprint for this reason.
- [ ] **Restart ≠ redeploy.** A restart reuses the running configuration, so it
      does **not** pick up environment-variable changes. Deploy for those.
- [ ] **`sync: false` prompts happen once.** Later `render.yaml` syncs ignore
      those variables; edit them in the Dashboard.
- [ ] **Don't grow the disk speculatively.** You can increase `sizeGB` but never
      decrease it, and you pay for what you provision.
- [ ] **Disk snapshots are not database backups.** Render says explicitly not to
      restore a disk snapshot to recover a database. Use `/backup`, and pull
      copies off the box periodically (§9).
- [ ] **Build/pre-deploy/one-off jobs cannot see the disk.** Don't script DB work
      there.
- [ ] **Region is permanent.** Changing it means recreating the service and disk.
- [ ] **No free tier for workers**, and no supported free workaround. Budget
      ~$7.25/month.
- [ ] **The job-queue extra matters.** If `requirements.txt` ever loses
      `python-telegram-bot[job-queue]`, backups, the Airtable poll and the
      announcement re-post stop running with no error.

---

## Source index

All fetched 2026-07-27:

- Background workers — <https://render.com/docs/background-workers>
- Service types — <https://render.com/docs/service-types>
- Instance types — <https://render.com/docs/compute-plans>
- Deploy for free (free-tier scope, limitations) — <https://render.com/docs/free>
- Pricing — <https://render.com/pricing>
- Persistent disks — <https://render.com/docs/disks>
- How deploys work (zero-downtime, restart, overlap, graceful shutdown) — <https://render.com/docs/deploys>
- Blueprint YAML reference — <https://render.com/docs/blueprint-spec>
- Blueprint JSON Schema — <https://render.com/schema/render.yaml.json>
- Environment variables & secrets — <https://render.com/docs/configure-environment-variables>
- Setting your Python version — <https://render.com/docs/python-version>
- SSH / shell access — <https://render.com/docs/ssh>
- Render CLI — <https://render.com/docs/cli>
- Scaling — <https://render.com/docs/scaling>
- In-dashboard logs — <https://render.com/docs/logging>
