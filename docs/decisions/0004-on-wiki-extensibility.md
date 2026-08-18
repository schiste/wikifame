# ADR-0004: On-wiki extensibility through wikitext and hooks, not code in configuration

## Status

Accepted on 2026-08-16.

## Context

[ADR-0003](0003-universal-wiki-support.md) put per-wiki settings in an on-wiki JSON page:
booleans, two page titles, and a flat `messages` map. That covers wording. It does not
cover wanting a diagram, an annotated screenshot, a short video, or a template in the
history-page box, and it never will — a settings file that grows a rendering language is
a settings file that has stopped being checkable.

The obvious next step is to let the configuration page carry markup, or JavaScript, and
have the gadget execute it. That is worth rejecting explicitly, because the usual argument
against it does not apply here.

**It is not a privilege escalation.** The configuration page lives in the reader's own
user space, so whoever can write it can already edit their copy of `wikipeople.js`. At the
gadget step both pages sit in the `MediaWiki:` namespace behind the same interface-admin
right. Executing configuration-supplied code grants nobody anything they did not have.

The real objections are different:

- It buys no capability that a hook does not already give, more cleanly.
- It destroys what the JSON page is good for: validated by MediaWiki on save, checkable
  against `config/*.json` in CI, and reviewable by reading it. A page that *might* contain
  code cannot be reviewed by reading it.
- MediaWiki already has a sanitising renderer for user-supplied rich content, with preview,
  history, and rollback. Reimplementing a worse one inside a gadget is unserious.

## Decision

Three mechanisms, chosen so that each kind of customisation lands where it is cheapest to
review.

### Settings stay declarative

`wikipeople-config.json` holds booleans, page titles, and message strings. Nothing in it is
ever executed or interpreted as markup. It remains the easy path: copy a file from
`config/`, change nothing, and everything works.

### Rich content is a wikitext page

`historyIntroPage` names a page whose parsed HTML replaces the built-in history
introduction. The gadget fetches `action=parse` anonymously, so the response is
CDN-cacheable and reader-independent, and inserts the result through `DOMParser` rather
than `innerHTML`.

This inherits MediaWiki's sanitiser, its preview, its history, and its rollback. Images,
galleries, Commons video, templates, and tables work because the gadget is not
implementing any of them.

Translations are language subpages — `/fr-ca`, then `/fr`, then the base title — resolved
against `wgUserLanguage`. This is deliberately *not* how `messages` works, and is the
better pattern: one reviewable page per language instead of one page in whichever language
its author happened to speak.

Two guarantees are kept in the gadget rather than delegated:

- The "you can also edit this article" link is always built by the script. A page parsed on
  its own cannot know which article the reader is on, so `{{FULLPAGENAME}}` in wikitext
  would name the introduction page and the link would edit the wrong thing.
- Images load lazily and video never autoplays or preloads. This box renders on every
  history view; the weight is paid every time.

### JavaScript is a hook

`mw.hook( 'wikipeople.history' )` and `mw.hook( 'wikipeople.summary' )` fire with the rendered
element. Arbitrary JavaScript belongs in the reader's own `common.js`, which is the
idiomatic MediaWiki extension point, costs two lines here, and is strictly more powerful
than anything a configuration page could express.

## Consequences

- Anyone can put essentially any content in the history box without a pull request, a
  deployment, or a change to this repository.
- What renders is no longer guaranteed by our tests. They cover the mechanism, the
  language fallback, and the fallback to built-in wording; the content is the author's.
  This is the trade being made, not an oversight.
- One extra request per history view when `historyIntroPage` is set. It is anonymous and
  CDN-cached, and the outcome — including "no such page" — is cached in `sessionStorage`
  for 24 hours so that an unwritten page does not cost three lookups per view.
- A 24-hour negative cache means pointing at a page before creating it delays it by up to a
  day for an already-open tab.
- `wikipeople.css` now has to contain foreign markup: parser output is scoped and its media
  constrained, but a deliberately oversized image can still make the box ugly.
- Commons video degrades to a plain player where TimedMediaHandler is absent.
- At the gadget step this scales better than JSON-embedded markup would: a wikitext page in
  the `MediaWiki:` namespace is already a normal community review surface, and per-language
  subpages already have an established workflow.
- `messages` stays flat and language-blind. Anything longer than a phrase should now use a
  content page instead, which is a better answer than the language-keyed `messages` map
  considered in ADR-0003.
