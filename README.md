# WikiFame

WikiFame makes the people behind Wikipedia articles visible. Its first interface is a MediaWiki
gadget that displays a short attribution below an article title:

> Article rédigé par Alice, Bob, Charlie et 44 autres personnes.

> Article written by Alice, Bob, Charlie and 44 other people.

The three names link to user pages; the remainder links to the article history. The sentence is
localised into the reader's interface language, and each wiki can adjust its own wording.

One file runs everywhere. The gadget reports which wiki it is on, and the API serves any Wikipedia
WikiWho covers — around seventy language editions — with no per-wiki code change.

## Acknowledgements

The original idea—and the real creative brains behind it—came from **Amir Aharoni, his wife,
and their daughter**. This project turns their family idea into an open Wikimedia prototype.

## Components

- `wikifame.js` and `wikifame.css`: wiki-agnostic, localised personal-script prototype.
- `src/wikifame/sites.py`: resolves a database name to a WikiWho language and Wikipedia host.
- `src/wikifame/app.py`: read-only FastAPI service for the gadget.
- `src/wikifame/worker.py`: durable WikiWho calculation worker.
- `src/wikifame/prewarm.py`: preloads popular articles from Wikimedia pageviews.
- `src/wikifame/backfill.py`: resumable, low-priority long-tail coverage.
- `src/wikifame/cleanup.py`: queue and old-revision retention.

The gadget uses a page-level result for up to 90 days. After that period, the API serves the
last known attribution while a worker refreshes it asynchronously. Stored results still record
the exact source revision and calculation date for auditability.

See [the architecture and scaling rules](docs/architecture.md) for cache identity, attribution
policy, update behavior, privacy, and the path toward millions of articles.

## Documentation

- [Architecture and scaling](docs/architecture.md): data flow, cache identity, priorities,
  attribution rules, privacy, and capacity limits.
- [API contract](docs/api.md): stable request and response shapes consumed by the gadget.
- [Operations runbook](docs/operations.md): Toolforge deployment, jobs, monitoring, incidents,
  backups, and maintainer transfer.
- [On-wiki setup](docs/onwiki-setup.md): installing the script on a wiki and configuring it,
  for anyone who wants to run it.
- [ADR-0001](docs/decisions/0001-attribution-policy.md): accepted attribution policy and its
  known limitations.
- [ADR-0002](docs/decisions/0002-page-freshness.md): 90-day page freshness and stale-while-
  revalidate behavior.
- [ADR-0003](docs/decisions/0003-universal-wiki-support.md): universal wiki support, demand-driven
  prewarming, and per-wiki on-wiki configuration.
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

## Personal-script installation

Copy the repository files to your user-page subpages on any covered Wikipedia — for example
`User:YOUR_USERNAME/wikifame.js` and `User:YOUR_USERNAME/wikifame.css`, or their localised
namespace name such as `Utilisateur:` on French Wikipedia. Optionally add
`User:YOUR_USERNAME/wikifame-config.json` from [`config/`](config). Then load the first two from
your `common.js`:

```javascript
importScript( 'User:YOUR_USERNAME/wikifame.js' );
importStylesheet( 'User:YOUR_USERNAME/wikifame.css' );
```

The same unmodified files work on every wiki. Where the API does not serve a wiki, the script
renders nothing.

Do not load the previous `ContributeursHumains` pages at the same time: both scripts would request
the same attribution and attempt to render a summary.

## Per-wiki configuration

Alongside the script, you can create `User:YOUR_USERNAME/wikifame-config.json` on the same wiki. It
is optional — without it the script uses its built-in text in your interface language. Its one real
job is supplying the two local titles the script cannot guess:

```json
{
	"enabled": true,
	"showHistoryIntro": true,
	"editHelpPage": "Aide:Comment modifier une page",
	"sandboxPage": "Wikipédia:Bac à sable",
	"historyIntroPage": null,
	"messages": {}
}
```

Defaults per wiki are published in [`config/`](config): [`enwiki.json`](config/enwiki.json),
[`frwiki.json`](config/frwiki.json). Copy the one for your wiki; send a pull request if you work
out the titles for a wiki that has none yet.

Keeping this in user space means installing and configuring WikiFame needs no special rights. When
a community adopts it as a site-wide gadget, the same file moves to
`MediaWiki:Wikifame-config.json`.

Full instructions, field reference, and troubleshooting: [on-wiki setup](docs/onwiki-setup.md).

## Going further than settings

The JSON page stays declarative on purpose, so two escape hatches exist for anything it cannot
express:

- `historyIntroPage` names a **wikitext page** whose parsed content replaces the history-box text.
  Images, galleries, Commons video, and templates all work, because MediaWiki does the parsing and
  the sanitising. Translations go on `/fr`-style language subpages.
- `mw.hook( 'wikifame.history' )` and `mw.hook( 'wikifame.summary' )` fire with the rendered
  element, so arbitrary JavaScript goes in your own `common.js` rather than in a configuration
  page.

Nothing in a configuration page is ever executed or treated as markup. See
[ADR-0004](docs/decisions/0004-on-wiki-extensibility.md).

## Toolforge deployment

Follow the [operations runbook](docs/operations.md). Deployment requires a Toolforge tool,
ToolsDB database, maintainer contact in `WIKIFAME_USER_AGENT`, Build Service image, webservice,
and the jobs declared in `jobs.yaml`. Toolforge-injected `TOOL_TOOLSDB_USER` and
`TOOL_TOOLSDB_PASSWORD` are used automatically; `DATABASE_URL` remains an explicit override.

## License

WikiFame is licensed under the [GNU Affero General Public License v3.0](LICENSE). WikiWho API data
is published separately under CC BY-SA 4.0, and Wikimedia content remains subject to its own
licenses and terms.
