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

## External dependencies

| Dependency | Purpose | Failure behavior |
| --- | --- | --- |
| French Wikipedia Action API | Page/revision validation and user resolution | Job retries |
| MediaWiki REST history counts | Aggregate contributor count | Job retries |
| WikiWho French API | Surviving-token provenance | Job retries with backoff |
| Wikimedia Analytics API | Popular-page prewarming | Only the scheduled prewarm fails |
| ToolsDB MariaDB | Results, leases, durable queue, backfill cursor | API health fails; workers stop |

The API web process never calls these upstream services. Only workers and scheduled jobs do.

## First deployment

1. Create or join the Toolforge tool. If its name is not `wikifame`, update the image names in
   `jobs.yaml` and `TOOLFORGE_API_BASE` in `ContributeursHumains.js`.
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

Toolforge environment-variable configuration is deployment state, not source code. Record the
variable names—not their values—in the maintainer handoff.

## Normal deployment

After merging a code change:

```bash
toolforge build start https://github.com/schiste/wikifame
toolforge webservice buildservice restart
toolforge jobs load jobs.yaml
```

Confirm `/healthz`, inspect webservice logs, and check that both worker replicas are running.

## Job inventory

| Job | Type | Expected behavior |
| --- | --- | --- |
| `attribution-worker` | Continuous, two replicas | Claims durable jobs and calls WikiWho |
| `popular-prewarm` | Daily | Enqueues the union of seven daily top-page lists at P50 |
| `gradual-backfill` | Hourly | Enqueues one resumable alphabetical batch at P10 |
| `cache-cleanup` | Weekly | Removes old failed work and superseded result revisions |

Live gadget misses enqueue P100 work. Do not increase worker replicas until WikiWho capacity and
observed latency justify it.

## Monitoring

Check:

- `/healthz` for database reachability;
- `/v1/stats` occasionally for queue growth and dead items;
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

Leave cached `200` responses online. Pause prewarm/backfill if the outage is prolonged. Jobs use
exponential backoff, and exhausted transient jobs can revive after `DEAD_RETRY_SECONDS`.

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
- Review open incidents, dead queue reasons, algorithm version, and current gadget behavior.
- Rotate credentials that were personally controlled.
