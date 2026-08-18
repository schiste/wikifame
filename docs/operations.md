# Toolforge operations runbook

This runbook is the handoff reference for deploying and maintaining WikiPeople. Never commit
credentials, `.env`, database dumps, or Toolforge account files.

## Required ownership

Before production use, ensure at least two maintainers have:

- access to the GitHub repository;
- membership in the Toolforge `wikipeople` tool;
- permission to inspect webservice and job logs;
- access to the on-wiki gadget or personal-script pages;
- a documented contact path to WikiWho operators.

Update `WIKIPEOPLE_USER_AGENT` whenever the operational contact changes.

The personal-script prototype lives on the user-page subpages `wikipeople.js` and `wikipeople.css` of
each maintainer's account, on whichever wiki they use. Its `common.js` must not simultaneously
import the former `ContributeursHumains` filenames.

## External dependencies

| Dependency | Purpose | Failure behavior |
| --- | --- | --- |
| Wikipedia Action API, per wiki | Page/revision validation and user resolution | Job retries |
| MediaWiki REST history counts | Aggregate contributor count | Job retries |
| WikiWho API, per language | Surviving-token provenance | Job retries with backoff |
| Wikimedia Analytics API | Popular-page prewarming | Only the scheduled prewarm fails |
| ToolsDB MariaDB | Results, leases, durable queue, backfill cursor | API health fails; workers stop |

The API web process never calls these upstream services. Only workers and scheduled jobs do.

## First deployment

1. Create or join the Toolforge tool. If its name is not `wikipeople`, update the image names in
   `jobs.yaml` and `TOOLFORGE_API_BASE` in `wikipeople.js`.
2. Create the ToolsDB database named `${TOOL_TOOLSDB_USER}__wikipeople` using the credential-user
   prefix required by Toolforge. The application automatically consumes Toolforge's injected
   `TOOL_TOOLSDB_USER` and `TOOL_TOOLSDB_PASSWORD`; do not copy those secrets into the repository.
   Set `TOOLSDB_DATABASE` only if a different database suffix is intentionally used.
3. Configure `WIKIPEOPLE_USER_AGENT` with a monitored contact address or user page.
4. Build from the public repository:

   ```bash
   toolforge build start https://github.com/schiste/wikifame
   ```

5. Start the webservice defined by `service.template`:

   ```bash
   toolforge webservice buildservice start --mount=none
   ```

6. Load the continuous and scheduled jobs:

   ```bash
   toolforge jobs load jobs.yaml
   ```

7. Check the service and jobs:

   ```bash
   curl -fsS https://wikipeople.toolforge.org/healthz
   toolforge jobs list --output long
   ```

8. Request one real page/revision, run or wait for a worker, and verify the transition from
   `202 pending` to `200 ready` before publishing the gadget URL.

No database migration is required for the page-freshness release: it reuses the existing
`computed_at` column and page/algorithm index. Its three environment controls are:

- `PAGE_FRESHNESS_SECONDS` (default `7776000`, 90 days) — how long a stored row stays usable;
- `PAGE_CACHE_SECONDS` (default `300`, five minutes) — how long a browser may reuse an answer
  without revalidating. Keep it short: it is the delay between deploying a policy change and
  readers seeing it. Every response carries an `ETag`, so the revalidation this buys is normally
  a `304` with no body;
- `READY_CACHE_SECONDS` (default `300`) — the same, for the legacy v1 endpoint;
- `PAGE_STALE_WHILE_REVALIDATE_SECONDS` (default `604800`, seven days) — beyond the window above
  the cached answer is still shown immediately while the refresh happens behind it, so a short
  window costs no waiting.

After deploying a change to `ALGORITHM_VERSION`, readers get the new answer on their next page
view once their five-minute window lapses. Before ETags existed this took up to a day on v2 and a
year on v1; if you are debugging a stale answer in a browser, check `sessionStorage` for
`wikipeople:*` as well, which the gadget holds for five minutes.

The opt-out release adds the `page_optout` table, which `create_all()` creates on first start.
Its two environment controls are:

- `OPTOUT_PAGE` (default `Project:WikiPeople/opt-out`) — the on-wiki list each community maintains.
  MediaWiki resolves the canonical `Project:` prefix per wiki, so one value reaches
  `Wikipédia:WikiPeople/opt-out` on frwiki and `Wikipedia:WikiPeople/opt-out` on enwiki. Always set it
  in canonical form. A localized prefix only resolves on the wiki it came from: `Utilisateur:…` is
  userspace on frwiki but a *mainspace article title* on enwiki, and the sync reads every active
  wiki, so a stray article by that name would become that wiki's list. While WikiPeople is a personal
  script the list may live under the maintainer's `User:` tree instead; give it no `.json`, `.js`
  or `.css` extension, or MediaWiki restricts it to interface administrators and the people it is
  meant to serve can no longer edit it;
- `OPTOUT_CATEGORY_LIMIT` (default `5000`) — how many articles one category entry may cover.
  Categories are not walked recursively. A category past the cap is logged as truncated by
  `optout-sync`, and that log line is the only signal, so read it.

See [ADR-0008](decisions/0008-article-opt-out.md) for what the list does and does not do.

The sanctioned-contributor release adds the `contributor_standing` table, which
`create_all()` creates on first start. Its controls are:

- `HIDE_SANCTIONED_CONTRIBUTORS` (default `true`) — whether an account the wiki has lastingly
  excluded is dropped from the names. Switching it off leaves `standing-sync` running, so
  switching it back on takes effect on the next response rather than on the next run;
- `MAX_VISIBLE_BLOCK_SECONDS` (default `7776000`, ninety days) — the longest block an account may
  carry and still be named. Indefinite blocks exceed every threshold, as do global locks whose
  steward reason reads as a sanction; a lock recorded as deceased, vanished or compromised does
  not withhold a name, and neither does one whose reason cannot be read. A block whose own
  reason reads as a courtesy — blocked at their own request, or because the account was
  compromised — does not withhold a name either, whatever its duration. The two tests take
  opposite defaults on purpose, and ADR-0009 says why. Equal by
  coincidence to `PAGE_FRESHNESS_SECONDS` and unrelated to it: one is about when an answer goes
  stale, the other about what a community has decided about a person. Do not tie them;
- `MAX_VISIBLE_BLOCK_SECONDS_BY_WIKI` (default empty) — per-wiki overrides as
  `frwiki:2592000,enwiki:0`, where `0` withholds the name of anyone under an active non-partial
  block. A malformed pair is dropped and that wiki keeps the global default, because these are
  read at import time in the web process;
- `STANDING_LOCK_CHECKS_PER_RUN` (default `500`) — CentralAuth answers about one account per
  request, so lock checks are rationed and rotate, never-checked accounts first. Blocks are
  refreshed for every tracked account on every run. At the defaults, and with the current few
  thousand named accounts, every lock is confirmed within a day; the job logs when it caps out,
  and that log line is the only signal that the rotation is falling behind;
- `STANDING_LOCK_RECHECK_SECONDS` (default `86400`) — how old a lock check may be before that
  account rejoins the queue.

Expect roughly an hour between a block being imposed and the name disappearing, and up to a day
for a global lock alone. The opt-out list stays the fast path when something must go now. See
[ADR-0009](decisions/0009-sanctioned-contributor-visibility.md) for the rule and its edge cases.

Both reasons are stored, in `block_reason` and `lock_reason`, because neither flag says what the
sanction means. **On a database that predates them, `create_all()` will not add the columns** —
it only creates missing tables. Run `ALTER TABLE contributor_standing ADD COLUMN block_reason
TEXT NULL` and the same for `lock_reason` before deploying, or every read of the table fails.
`TEXT` rather than a bounded column on purpose: administrators write long block reasons, and a
truncated one could hide a courtesy phrased late and withhold the name. If a courtesy wording is found being read as a sanction, the fix is to extend the patterns
in `policy.py` and deploy; nothing is recomputed and no row needs editing.

The universal-wiki release adds the `active_wikis` table, which `create_all()` creates on first
start; no migration is required either. Its environment controls are:

- `SUPPORTED_WIKIS` (default `*`): wikis served on demand. A list of database names narrows it.
  A wiki WikiWho cannot analyse is never served, whether or not it appears here.
- `PREWARM_WIKIS` (default empty): wikis pinned for daily top-1000 prewarming, in addition to the
  wikis discovered automatically.
- `BACKFILL_WIKIS` (default empty): wikis crawled article by article. Never inferred.
- `WIKIWHO_LANGUAGES` (default empty): overrides the built-in WikiWho coverage list.
- `CORS_ORIGIN_REGEX` (default matches every Wikipedia, desktop and mobile).

Toolforge environment-variable configuration is deployment state, not source code. Record the
variable names—not their values—in the maintainer handoff.

## Enabling a wiki

Serving requires no action: `SUPPORTED_WIKIS=*` already answers for every WikiWho-covered
Wikipedia, and the first result a worker stores enrols that wiki into daily prewarming. What
remains is a community conversation before the script is advertised on that wiki.

Check current state with `GET /v1/stats`: `supported_wikis` is the configured enablement,
`active_wikis` is what is actually being prewarmed.

To pin a wiki before its first reader arrives, add it to `PREWARM_WIKIS`. To prime it by hand:

```bash
python -m wikipeople.prewarm --wiki dewiki --days 1
```

Enable `BACKFILL_WIKIS` only after measuring row size. English Wikipedia alone has millions of
articles against a nominal 25 GB ToolsDB boundary.

### The on-wiki configuration page

While WikiPeople is a personal script, each user's settings live at
`User:<name>/wikipeople-config.json` on the wiki, next to their copy of the script. Per-wiki defaults
are published in [`config/`](../config) for people to copy; the full field reference and
troubleshooting steps are in [on-wiki setup](onwiki-setup.md).

Operationally this means the service has no say in it, by design: installing, configuring, and
switching the script off all happen in user space with no rights and no deployment. Expect to learn
about a local opt-out from a page history, not from a ticket.

Edits propagate within minutes (`action=raw` is CDN-cached) and reach a given reader on their next
browser session, or after 24 hours at the latest.

When a community adopts the script as a site-wide gadget, its configuration moves to
`MediaWiki:Wikipeople-config.json` on that wiki, maintained by its interface administrators.

## Normal deployment

After merging a code change:

```bash
scp jobs.yaml login.toolforge.org:/mnt/nfs/labstore-secondary-tools-project/wikipeople/jobs.yaml
toolforge build start https://github.com/schiste/wikifame
toolforge webservice buildservice restart
toolforge jobs load jobs.yaml
toolforge jobs restart attribution-worker
```

The first line is easy to forget and fails quietly. `toolforge build` reads the repository from
GitHub, but `toolforge jobs load` reads a copy of `jobs.yaml` that lives in the tool's home
directory and is not a checkout of anything. A deployment that adds or changes a job definition
loads the *old* file, reports success, and leaves the new job uncreated. `jobs load` prints one
line per job it loaded; count them against `jobs.yaml` before believing it.

The last line is not redundant. `jobs load` reconciles the job *definition*, and a deployment
that changes only code leaves that definition identical, so nothing is recreated and the
continuous worker keeps running the image it started with. The webservice restarts and the
workers do not, which is the worst version of a half-deployment: the API announces the new
`ALGORITHM_VERSION` while old workers compute the rows filed under it. Scheduled jobs need no
restart because each run starts a new pod.

Confirm the rollout before believing it:

```bash
toolforge jobs show attribution-worker    # "Started at" must be after the build
toolforge jobs list                       # every job in jobs.yaml must appear
curl -fsS https://wikipeople.toolforge.org/v1/stats
```

Then inspect webservice logs and check that both worker replicas are running.

## Job inventory

| Job | Type | Expected behavior |
| --- | --- | --- |
| `attribution-worker` | Continuous, two replicas | Claims durable jobs and calls WikiWho |
| `popular-prewarm` | Daily | Per active wiki, scans backward to enqueue seven available top-1000 lists at P50 |
| `gradual-backfill` | Hourly | Per `BACKFILL_WIKIS` entry, enqueues one resumable alphabetical batch at P10 |
| `cache-cleanup` | Weekly | Removes old failed work and superseded result revisions |
| `optout-sync` | Every 15 minutes | Per active wiki, materialises the on-wiki opt-out list into `page_optout` |
| `standing-sync` | Hourly | Per active wiki, refreshes block and lock status for named accounts into `contributor_standing` |

Live gadget misses and expired results enqueue P100 work. Prewarm and backfill skip any page with
a result younger than `PAGE_FRESHNESS_SECONDS`. Do not increase worker replicas until WikiWho
capacity and observed latency justify it.

Prewarm runtime grows with the number of active wikis, so watch its duration as wikis are
discovered. Each wiki is isolated: one unavailable wiki logs and is skipped rather than cancelling
the run. `gradual-backfill` does nothing while `BACKFILL_WIKIS` is empty.

## Monitoring

Check:

- `/healthz` for database reachability;
- `/v1/stats` occasionally for queue growth, dead items, the `active_wikis` list, and the
  per-wiki `opted_out` counts — a count that drops to zero on a wiki that had entries means the
  list page was blanked, moved, or is being read from the wrong title;
- `/v2/{wiki}/pages/{page_id}?revision_id={revision_id}` for `is_fresh`, `refreshing`, and the
  `X-WikiPeople-Source-Revision` header on a known article;
- `toolforge webservice buildservice logs -f` for API errors;
- `toolforge jobs logs attribution-worker -f` for upstream or worker failures;
- ToolsDB size after 100,000, 500,000, and 1,000,000 ready rows;
- WikiWho latency, HTTP 408/429/5xx rates, and response-size failures.

Suggested initial alerts are: health failure for five minutes, no running worker, P100 queue growth
for one hour, or repeated WikiWho 429 responses.

## Common incidents

### Queue grows while workers run

Inspect WikiWho latency and rate responses. Pause `gradual-backfill` before adding worker replicas.
Reader-demand jobs have higher priority and should recover first.

### WikiWho is unavailable

Leave cached `200` responses online. V2 deliberately continues returning an expired result while
its refresh retries. Pause prewarm/backfill if the outage is prolonged. Jobs use exponential
backoff, and exhausted transient jobs can revive after `DEAD_RETRY_SECONDS`.

### A community wants the gadget off, or its wording changed

This is a local decision and needs no deployment. Each user sets `"enabled": false` in their own
`User:<name>/wikipeople-config.json`, or simply removes the import from their `common.js`. Removing
the wiki from `SUPPORTED_WIKIS` is the operator-side equivalent and is only needed when the wiki
must stop being served entirely.

### An article should stop naming its contributors

This is a community decision and needs no deployment. Add the article — or a category it belongs
to — as a bulleted link on `Wikipédia:WikiPeople/opt-out` (or the same page in the local project
namespace). Where that page does not exist yet, `docs/onwiki/optout.fr.wiki` is a starter copy to
paste: it documents the entry format for editors and ships with no entries. `optout-sync` picks it up within fifteen minutes, and readers see the change once
their five-minute cache lapses. Removing the entry reverses it just as quickly; nothing is
recomputed either way.

To check what a list will cover before it takes effect:

```bash
python -m wikipeople.optout --wiki frwiki --dry-run
```

If a page seems not to be covered, the sync log names what it dropped and why: a redlinked title,
a link in a namespace that is neither article nor category, or a category past
`OPTOUT_CATEGORY_LIMIT`. A wiki whose Action API was unreachable keeps its previous list rather
than losing it, and says so in the log.

### A policy result is wrong

Do not edit cached rows manually. Fix the policy, increment `ALGORITHM_VERSION`, deploy, and let
requests create results in the new namespace. Keep the previous version available for rollback.

### Database schema must change

`create_all()` does not migrate existing tables. Take a backup, introduce a versioned migration,
test it against a copy, and deploy it before code that requires the new schema.

### Backfill must restart

Run `python -m wikipeople.backfill --restart` once, then let the scheduled job resume. Restarting
millions of pages is expensive; confirm the need first.

## Backups and retention

ToolsDB does not provide offline backups for tool-owned databases. Schedule or perform a
`mariadb-dump` before schema changes and periodically after substantial cache growth. Protect dump
permissions and store them outside the public repository.

Ready rows are reproducible from public upstream data. The durable queue and backfill cursor are
operationally useful but not irreplaceable. This reduces disaster-recovery urgency, not the need
to test restoration before a migration.

## Maintainer transfer checklist

- Add the new maintainer to GitHub and Toolforge before removing the previous one.
- Transfer the monitored email/user-page contact and update `WIKIPEOPLE_USER_AGENT`.
- Review Toolforge variables, job status, database name/size, and last backup.
- Share the WikiWho contact history and any agreed request-rate limits.
- Identify the on-wiki personal script/gadget pages and interface-administrator contacts.
- List the wikis in `active_wikis`, the on-wiki script and configuration pages in use on each,
  and any community agreements made when each wiki was enabled.
- Identify each wiki's opt-out list page and who watches it; a list nobody watches is a list that
  silently stops working.
- Review open incidents, dead queue reasons, algorithm version, and current gadget behavior.
- Rotate credentials that were personally controlled.
