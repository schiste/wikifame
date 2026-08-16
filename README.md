# WikiFame

WikiFame makes the people behind French Wikipedia articles visible. Its first interface is a
MediaWiki gadget that displays a short attribution below an article title:

> Article rédigé par Alice, Bob, Charlie et 44 autres personnes.

The three names link to user pages. “44 autres personnes” links to the article history.

## Acknowledgements

The original idea—and the real creative brains behind it—came from **Amir Aharoni, his wife,
and their daughter**. This project turns their family idea into an open Wikimedia prototype.

## Components

- `ContributeursHumains.js` and `ContributeursHumains.css`: personal-script prototype.
- `src/wikifame/app.py`: read-only FastAPI service for the gadget.
- `src/wikifame/worker.py`: durable WikiWho calculation worker.
- `src/wikifame/prewarm.py`: preloads popular articles from Wikimedia pageviews.
- `src/wikifame/backfill.py`: resumable, low-priority long-tail coverage.
- `src/wikifame/cleanup.py`: queue and old-revision retention.

See [the architecture and scaling rules](docs/architecture.md) for cache identity, attribution
policy, update behavior, privacy, and the path toward millions of articles.

## Documentation

- [Architecture and scaling](docs/architecture.md): data flow, cache identity, priorities,
  attribution rules, privacy, and capacity limits.
- [API contract](docs/api.md): stable request and response shapes consumed by the gadget.
- [Operations runbook](docs/operations.md): Toolforge deployment, jobs, monitoring, incidents,
  backups, and maintainer transfer.
- [ADR-0001](docs/decisions/0001-attribution-policy.md): accepted attribution policy and its
  known limitations.
- [Contributing](CONTRIBUTING.md): local workflow and change checklist.
- [Agent guide](AGENTS.md): repository invariants and commands for coding agents.

## Local development

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/uvicorn wikifame.app:app --reload
```

Environment variables are read directly by the process. Export the values from `.env` with the
environment manager of your choice; the application intentionally does not load arbitrary files.

Initialize and exercise the asynchronous path:

```bash
.venv/bin/python -c 'from wikifame.runtime import build_runtime; build_runtime().database.create_schema()'
.venv/bin/python -m wikifame.worker --once
.venv/bin/python -m wikifame.prewarm --days 1
```

Run the test suite:

```bash
.venv/bin/pytest
```

## Toolforge deployment

Follow the [operations runbook](docs/operations.md). Deployment requires a Toolforge tool,
ToolsDB database, maintainer contact in `WIKIFAME_USER_AGENT`, Build Service image, webservice,
and the jobs declared in `jobs.yaml`. Toolforge-injected `TOOL_TOOLSDB_USER` and
`TOOL_TOOLSDB_PASSWORD` are used automatically; `DATABASE_URL` remains an explicit override.

## License

WikiFame is licensed under the [GNU Affero General Public License v3.0](LICENSE). WikiWho API data
is published separately under CC BY-SA 4.0, and Wikimedia content remains subject to its own
licenses and terms.
