# Toolforge operations runbook

This runbook is the handoff reference for deploying and maintaining WikiFame. Never commit
credentials, `.env`, database dumps, or Toolforge account files.

## Required ownership

Before production use, ensure at least two maintainers have:

- access to the GitHub repository;
- membership in the Toolforge `wikifame` tool;
- permission to inspect webservice and job logs;
- access to the on-wiki gadget or personal-script pages;
- a documented contact path to WikiWho operators.

Update `WIKIFAME_USER_AGENT` whenever the operational contact changes.

The personal-script prototype lives on the user-page subpages `wikifame.js` and `wikifame.css` of
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

1. Create or join the Toolforge tool. If its name is not `wikifame`, update the image names in
   `jobs.yaml` and `TOOLFORGE_API_BASE` in `wikifame.js`.
2. Create the ToolsDB database named `${TOOL_TOOLSDB_USER}__wikifame` using the credential-user
   prefix required by Toolforge. The application automatically consumes Toolforge's injected
   `TOOL_TOOLSDB_USER` and `TOOL_TOOLSDB_PASSWORD`; do not copy those secrets into the repository.
   Set `TOOLSDB_DATABASE` only if a different database suffix is intentionally used.
3. Configure `WIKIFAME_USER_AGENT` with a monitored contact address or user page.
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
   curl -fsS https://wikifame.toolforge.org/healthz
   toolforge jobs list --output long
   ```

8. Request one real page/revision, run or wait for a worker, and verify the transition from
   `202 pending` to `200 ready` before publishing the gadget URL.

No database migration is required for the page-freshness release: it reuses the existing
`computed_at` column and page/algorithm index. Its three environment controls are:

- `PAGE_FRESHNESS_SECONDS` (default `7776000`, 90 days);
- `PAGE_CACHE_SECONDS` (default `86400`, one day);
- `PAGE_STALE_WHILE_REVALIDATE_SECONDS` (default `604800`, seven days).

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
python -m wikifame.prewarm --wiki dewiki --days 1
```

Enable `BACKFILL_WIKIS` only after measuring row size. English Wikipedia alone has millions of
articles against a nominal 25 GB ToolsDB boundary.

### The on-wiki configuration page

While WikiFame is a personal script, each user's settings live at
`User:<name>/wikifame-config.json` on the wiki, next to their copy of the script. Per-wiki defaults
are published in [`config/`](../config) for people to copy; the full field reference and
troubleshooting steps are in [on-wiki setup](onwiki-setup.md).

Operationally this means the service has no say in it, by design: installing, configuring, and
switching the script off all happen in user space with no rights and no deployment. Expect to learn
about a local opt-out from a page history, not from a ticket.

Edits propagate within minutes (`action=raw` is CDN-cached) and reach a given reader on their next
browser session, or after 24 hours at the latest.

When a community adopts the script as a site-wide gadget, its configuration moves to
`MediaWiki:Wikifame-config.json` on that wiki, maintained by its interface administrators.

## Normal deployment

After merging a code change:

```bash
toolforge build start https://github.com/schiste/wikifame
toolforge webservice buildservice restart
toolforge jobs load jobs.yaml
toolforge jobs restart attribution-worker
```

The last line is not redundant. `jobs load` reconciles the job *definition*, and a deployment
that changes only code leaves that definition identical, so nothing is recreated and the
continuous worker keeps running the image it started with. The webservice restarts and the
workers do not, which is the worst version of a half-deployment: the API announces the new
`ALGORITHM_VERSION` while old workers compute the rows filed under it. Scheduled jobs need no
restart because each run starts a new pod.

Confirm the rollout before believing it:

```bash
toolforge jobs show attribution-worker    # "Started at" must be after the build
curl -fsS https://wikifame.toolforge.org/v1/stats
```

Then inspect webservice logs and check that both worker replicas are running.

## Job inventory

| Job | Type | Expected behavior |
| --- | --- | --- |
| `attribution-worker` | Continuous, two replicas | Claims durable jobs and calls WikiWho |
| `popular-prewarm` | Daily | Per active wiki, scans backward to enqueue seven available top-1000 lists at P50 |
| `gradual-backfill` | Hourly | Per `BACKFILL_WIKIS` entry, enqueues one resumable alphabetical batch at P10 |
| `cache-cleanup` | Weekly | Removes old failed work and superseded result revisions |

Live gadget misses and expired results enqueue P100 work. Prewarm and backfill skip any page with
a result younger than `PAGE_FRESHNESS_SECONDS`. Do not increase worker replicas until WikiWho
capacity and observed latency justify it.

Prewarm runtime grows with the number of active wikis, so watch its duration as wikis are
discovered. Each wiki is isolated: one unavailable wiki logs and is skipped rather than cancelling
the run. `gradual-backfill` does nothing while `BACKFILL_WIKIS` is empty.

## Monitoring

Check:

- `/healthz` for database reachability;
- `/v1/stats` occasionally for queue growth, dead items, and the `active_wikis` list;
- `/v2/{wiki}/pages/{page_id}?revision_id={revision_id}` for `is_fresh`, `refreshing`, and the
  `X-WikiFame-Source-Revision` header on a known article;
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
`User:<name>/wikifame-config.json`, or simply removes the import from their `common.js`. Removing
the wiki from `SUPPORTED_WIKIS` is the operator-side equivalent and is only needed when the wiki
must stop being served entirely.

### A policy result is wrong

Do not edit cached rows manually. Fix the policy, increment `ALGORITHM_VERSION`, deploy, and let
requests create results in the new namespace. Keep the previous version available for rollback.

### Database schema must change

`create_all()` does not migrate existing tables. Take a backup, introduce a versioned migration,
test it against a copy, and deploy it before code that requires the new schema.

### Backfill must restart

Run `python -m wikifame.backfill --restart` once, then let the scheduled job resume. Restarting
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
- Transfer the monitored email/user-page contact and update `WIKIFAME_USER_AGENT`.
- Review Toolforge variables, job status, database name/size, and last backup.
- Share the WikiWho contact history and any agreed request-rate limits.
- Identify the on-wiki personal script/gadget pages and interface-administrator contacts.
- List the wikis in `active_wikis`, the on-wiki script and configuration pages in use on each,
  and any community agreements made when each wiki was enabled.
- Review open incidents, dead queue reasons, algorithm version, and current gadget behavior.
- Rotate credentials that were personally controlled.
