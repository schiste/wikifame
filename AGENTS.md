# Agent guide

These instructions apply to the whole repository.

## Mission and current scope

WikiFame powers a Wikipedia personal-script prototype that displays three registered, non-bot,
non-temporary accounts associated with surviving WikiWho tokens and links the remaining historical
contributor count to page history. Toolforge is the prototype backend. One unmodified gadget file
serves every Wikipedia WikiWho covers.

## Invariants

- Never call WikiWho, MediaWiki, or Analytics from the FastAPI request path. Cache misses enqueue
  durable work and return `202`.
- Stored-result identity is `(wiki, page_id, revision_id, algorithm_version)`; do not key by
  title. V2 may serve the newest page/algorithm result until its configured freshness expires.
- Never highlight IPs, anonymous actors, temporary usernames (`~…`), missing users, or bots.
- A semantic policy change requires a new `ALGORITHM_VERSION` and documented decision.
- A breaking API change requires a new version rather than silently changing `/v1`.
- Do not store raw WikiWho token responses; retain only compact aggregate results.
- Do not commit secrets, Toolforge account files, database URLs, dumps, or `.env`.
- Keep the gadget backed exclusively by the Toolforge API; do not add production-page fixtures.
- Keep the gadget wiki-agnostic: no wiki name, host, namespace prefix, page title, plural form, or
  list separator may be hard-coded. Per-wiki settings belong in the on-wiki configuration page
  (`User:<name>/wikifame-config.json` while this is a personal script).
- Capability and enablement stay separate. Whether a wiki can be analysed is derived in
  `sites.py`; whether it is served is configuration. Capability always wins.
- Universal serving never implies universal crawling. `BACKFILL_WIKIS` stays an explicit opt-in.

## Repository map

- `wikifame.js`, `.css`: MediaWiki gadget/personal script.
- `src/wikifame/app.py`: cache-only HTTP API.
- `src/wikifame/worker.py`: asynchronous calculation orchestration.
- `src/wikifame/clients.py`: all external HTTP calls.
- `src/wikifame/sites.py`: database name → WikiWho language → Wikipedia host, and enablement.
- `src/wikifame/policy.py`, `attribution.py`: product rules and pure aggregation.
- `src/wikifame/repository.py`, `models.py`: durable cache, queue, leases, retention.
- `prewarm.py`, `backfill.py`, `cleanup.py`: scheduled jobs.
- `docs/api.md`: consumer contract.
- `docs/operations.md`: deployment and incident runbook.
- `docs/onwiki-setup.md`, `config/`: on-wiki configuration reference and per-wiki defaults.
- `docs/decisions/`: accepted product/architecture decisions.

## Required validation

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
node --check wikifame.js
git diff --check
```

Tests must remain offline and deterministic. A live smoke test is optional and read-only.

## Database caution

SQLAlchemy `create_all()` only creates missing tables. It does not migrate deployed schemas. Once
production data exists, accompany model changes with a reviewed, backed-up, versioned migration.

## Handoff expectation

Update README links, API documentation, operations steps, ADRs, environment examples, and tests in
the same change whenever behavior or deployment requirements change. State explicitly what remains
local, uncommitted, undeployed, or dependent on an external decision.
