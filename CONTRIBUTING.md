# Contributing

## Local setup

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
node --check wikipeople.js
```

SQLite is the default local database. Production uses MariaDB/ToolsDB through `DATABASE_URL`.
Never commit `.env`, credentials, database files, dumps, or generated virtual environments.

## Change checklist

Before proposing a change:

1. Preserve the request-path rule: the FastAPI process reads/writes ToolsDB only and never waits
   for WikiWho or MediaWiki.
2. Add or update tests for queue concurrency, API states, and attribution rules.
3. If output semantics change, increment `ALGORITHM_VERSION` and update the relevant ADR.
4. If the `/v1` JSON contract breaks, introduce a new API version and update the gadget together.
5. If the database schema changes after first deployment, provide a real migration; `create_all()`
   is insufficient.
6. Keep temporary, anonymous, missing, and bot accounts out of the highlighted top three unless a
   later accepted ADR explicitly changes that rule.
7. Update the runbook when jobs, environment variables, commands, or ownership requirements move.

## Tests using external services

Unit tests must not depend on live Wikimedia or WikiWho services. Use fakes or `httpx.MockTransport`.
A deliberate read-only smoke test may be run manually before deployment and must use a descriptive
User-Agent with maintainer contact information.

## Licensing

Contributions are accepted under AGPL-3.0-only. Do not copy incompatible code or upstream API data
into the repository. WikiWho data and Wikimedia content have separate reuse terms.
