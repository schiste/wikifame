# ADR-0003: Universal wiki support with local on-wiki configuration

## Status

Accepted on 2026-08-16.

## Context

The prototype hard-coded `frwiki` in three places: a host map and a language map in the
client, and the API path inside the gadget. Adding a wiki therefore meant a code change, a
deployment, and a separate on-wiki script edit requiring interface-administrator rights on
that wiki.

That cost was accepted while WikiWho coverage was assumed to be narrow. It is not: WikiWho
publishes provenance data for roughly seventy Wikipedia language editions. The limiting
factor for expansion is request rate and community consent, not data availability.

Two further facts shaped the decision:

- Every WikiWho language code is dash-free, so `<code>wiki` maps to `<code>.wikipedia.org`
  exactly. No sitematrix lookup is needed to resolve a host or to test coverage.
- Serving a wiki on demand and crawling all of its articles are very different commitments.
  English Wikipedia alone has millions of articles, against a nominal 25 GB ToolsDB budget.

## Decision

### Capability is derived, not configured

`wikifame.sites.SiteResolver` strips the `wiki` suffix from a database name and checks the
result against the set of WikiWho language codes. This answers both "can we analyse this
wiki" and "what host do we call" without any network request, which preserves the invariant
that the FastAPI process never contacts an upstream service.

Non-Wikipedia projects are excluded by the same rule rather than by a denylist:
`frwikisource` fails the suffix test, and `commonswiki` and `wikidatawiki` resolve to
language codes WikiWho does not publish.

`WIKIWHO_LANGUAGES` overrides the built-in set so that a coverage change at WikiWho does not
require a code release.

### Capability and enablement are separate

- `SUPPORTED_WIKIS` defaults to `*`: any WikiWho-covered Wikipedia is served on demand.
- `PREWARM_WIKIS` pins wikis whose popular pages stay warm before anyone reads them.
- `BACKFILL_WIKIS` defaults to empty. Bulk crawling is never inferred.

Enabling a wiki WikiWho cannot analyse is meaningless, so capability always wins over
configuration.

### Wikis enrol themselves through demonstrated demand

When a worker stores the first result for a wiki, it registers that wiki in `active_wikis`.
The daily prewarm job unions that table with `PREWARM_WIKIS`, so a wiki discovered through
real readership starts keeping its own top-1000 warm with no configuration change.

Registration is driven by a completed calculation rather than by an API request. A scripted
client cannot enrol seventy wikis into scheduled work by poking seventy URLs.

Backfill deliberately does not participate in this discovery.

### The gadget is wiki-agnostic and locally configurable

The gadget reports `wgDBname` and lets the API decide. An unsupported wiki answers `404`,
the fetch rejects, and nothing renders — so one file ships everywhere while the backend
enables wikis progressively, with no further on-wiki edits.

Wording is localised through a built-in message table keyed on `wgUserLanguage`, using
`{{PLURAL:}}` for counts and `Intl.ListFormat` for name conjunctions, so no separator or
plural form is hard-coded.

Per-wiki settings live in an on-wiki JSON page:

```json
{
  "enabled": true,
  "showHistoryIntro": true,
  "editHelpPage": "Aide:Comment modifier une page",
  "sandboxPage": "Wikipédia:Bac à sable",
  "messages": {}
}
```

The page is optional; a missing page yields the built-in defaults. Per-wiki defaults are
published in `config/` for people to copy.

While WikiFame is a **personal script**, that page is `User:<name>/wikifame-config.json`,
resolved from `wgUserName` and namespace 2 so the localised namespace name is correct on
every wiki. The reader and the installer are the same person, so this keeps installation and
configuration free of any rights requirement — nobody has to find an interface administrator
to try the tool. It also sits next to `wikifame.js` and `wikifame.css`, which is where people
will look for it.

Should WikiFame become a **site-wide gadget**, the page moves to
`MediaWiki:Wikifame-config.json`: one shared copy, in a namespace where changing what every
reader sees requires the same rights as changing any other interface message. Only
`CONFIG_PAGE_SUFFIX` and `configPage()` change.

`enabled: false` is a local opt-out that does not depend on the operator in either case.

`messages` overrides are applied on top of the reader's language rather than per language, so
an override replaces that string for every language. This is documented as "leave it empty":
acceptable while each config page has exactly one reader, and worth revisiting before the
gadget step, when one page would serve readers of many interface languages.

## Consequences

- Adding a wiki is a configuration change plus a community conversation, never a code change
  or an on-wiki script edit.
- Users own their own wording, help links, and opt-out without a pull request, and without
  needing rights on the wiki. Communities inherit that ownership at the gadget step.
- The attribution policy is unchanged, so `ALGORITHM_VERSION` stays `surviving-tokens-v1`.
  Stored rows are already keyed by wiki and remain valid.
- `active_wikis` is a new table, which `create_all()` creates safely; no migration is needed.
- Serving is universal but load stays demand-driven. A wiki nobody reads costs nothing.
- Universal CORS uses an origin regex. CORS was never authentication, and this does not
  change what an unauthenticated scripted client could already do.
- Non-Wikipedia projects remain out of scope for as long as WikiWho covers Wikipedia only.
- A dashed WikiWho code, or coverage beyond Wikipedia, would break the suffix rule.
  `wikifame/sites.py` is the single place where a real sitematrix lookup would replace it.
