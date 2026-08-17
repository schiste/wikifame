# Setting up WikiFame on a wiki

WikiFame is currently a **personal script**: you install it for yourself, on one wiki, and only you
see it. Everything below is something you do in your own user space, with no special rights and
nobody else's permission.

Toolforge-side operation is covered separately in the [operations runbook](operations.md).

## The three pages in your user space

All three live under your own user name, on whichever wiki you are installing on:

| Page | What it is | Required? |
| --- | --- | --- |
| `User:YOU/wikifame.js` | The script | Yes |
| `User:YOU/wikifame.css` | Its styles | Yes |
| `User:YOU/wikifame-config.json` | Your settings for this wiki | No |

Substitute your wiki's own user-namespace name where it differs — `Utilisateur:` on the French
Wikipedia, for example. The script resolves that itself, so the three pages always sit together
whatever the wiki calls the namespace.

Then load the first two from `User:YOU/common.js`:

```javascript
importScript( 'User:YOU/wikifame.js' );
importStylesheet( 'User:YOU/wikifame.css' );
```

The configuration page is **not** imported. The script looks it up by name on its own.

## Before you start: is the wiki covered?

WikiWho publishes provenance data for around seventy Wikipedia language editions, from Afrikaans to
Chinese, including Simple English. Commons, Wikidata, Wiktionary, and Wikisource are not covered
and cannot be — there is no surviving-token provenance for them.

On a wiki that is not covered, the API answers `404` and the script renders nothing. Installing it
there does no harm, but it does nothing either.

## Creating the configuration page

Everything works without it. Its one real job is supplying the two local page titles the script
cannot guess: your wiki's editing help and its sandbox. Without them, the "to get started, read …
or practise in …" sentence in the history box is simply left out.

1. Pick the file for your wiki from [`config/`](../config) in this repository — currently
   [`enwiki.json`](../config/enwiki.json) and [`frwiki.json`](../config/frwiki.json).
2. Create `User:YOU/wikifame-config.json` on that wiki and paste it in.
3. For a wiki with no published default yet, copy either file and replace the two titles with your
   wiki's own, including their namespace, exactly as they appear locally.
4. Save. MediaWiki treats `.json` subpages as JSON, validates them, and refuses to save invalid
   JSON — so a typo cannot reach the script. It reformats with tab indentation; that is expected.
5. Reload an article **in a new tab**. The script caches the configuration in `sessionStorage`, so
   an already-open tab may still be using the previous version.

### English Wikipedia — [`config/enwiki.json`](../config/enwiki.json)

```json
{
	"enabled": true,
	"showHistoryIntro": true,
	"editHelpPage": "Help:Editing",
	"sandboxPage": "Wikipedia:Sandbox",
	"historyIntroPage": null,
	"messages": {}
}
```

### French Wikipedia — [`config/frwiki.json`](../config/frwiki.json)

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

If you work out the right titles for a wiki that has no default yet, please send them back as a
pull request so the next person on that wiki does not have to.

## Fields

| Key | Type | Default | Effect |
| --- | --- | --- | --- |
| `enabled` | boolean | `true` | `false` switches the script off on this wiki. It stops before rendering anything. |
| `showHistoryIntro` | boolean | `true` | `false` removes the explanatory box on page-history views but keeps the attribution sentence on articles. |
| `editHelpPage` | string or `null` | `null` | Local title of the editing help page. |
| `sandboxPage` | string or `null` | `null` | Local title of the sandbox. |
| `historyIntroPage` | string or `null` | `null` | Title of a wikitext page whose content replaces the history box text. See [Rich content](#rich-content-images-video-anything-wikitext-can-do). |
| `messages` | object | `{}` | Overrides individual interface strings by key. See the warning below. |

Unknown keys are ignored, so a future option can be added without breaking existing pages.

The two boolean options are read strictly: only a literal `false` turns them off. `"false"` as a
string, `0`, or `null` all leave the option on. Write real JSON booleans.

`editHelpPage` and `sandboxPage` work as a pair. The help sentence appears only when **both** are
set; setting just one leaves it out entirely.

## Where the wording comes from

The script carries its own text in English and French, and picks which to use from **your interface
language** (`wgUserLanguage`), not from the wiki. Four layers apply in order, each overwriting the
one before:

1. built-in English — always applied, the floor;
2. built-in text for your base language, e.g. `fr` for a reader set to `fr-ca`;
3. built-in text for your exact language code;
4. whatever `messages` in your configuration page says.

So on the French Wikipedia a reader with a French interface sees French, and a reader with a German
interface sees English — because German is not built in yet, not because of any configuration.

### Leave `messages` empty

**Recommended: omit it, or leave it as `{}`.** Layers 1–3 follow the reader's language; layer 4
does not. An override replaces that string for **every** language, so text written to improve the
French wording also replaces the English one.

That matters less for a personal script, where you are the only reader, than it will when this
becomes a site-wide gadget. But the better fix in almost every case is to add the wording to the
script's own message table, where it is language-aware and helps every wiki at once.

Available keys, with the built-in English text:

| Key | Default |
| --- | --- |
| `wikifame-summary-prefix` | `Article written by ` |
| `wikifame-summary-prefix-edits` | `Article most edited by ` |
| `wikifame-people` | `{{PLURAL:$1|$1 person|$1 people}}` |
| `wikifame-others` | `{{PLURAL:$1|$1 other person|$1 other people}}` |
| `wikifame-at-least` | `at least $1` |
| `wikifame-many-people` | `many people` |
| `wikifame-user-title` | `View the user page of $1` |
| `wikifame-share` | `$1 of the currently visible tokens` |
| `wikifame-share-edits` | `$1 of the edits to this page` |
| `wikifame-history-title` | `View the full page history` |
| `wikifame-tooltip` | `Main authors of the text according to WikiWho.` |
| `wikifame-tooltip-edits` | `Accounts that edited this page most, from its history. The text itself could not be analysed.` |
| `wikifame-computed` | `Data computed on $1.` |
| `wikifame-history-intro` | `Each line is one version of the article, showing who changed it.` |
| `wikifame-history-help` | `To get started, read $1 or practise in $2.` |
| `wikifame-history-help-label` | `the editing help` |
| `wikifame-history-sandbox-label` | `the sandbox` |
| `wikifame-history-edit` | `You can also $1.` |
| `wikifame-history-edit-label` | `edit this article directly` |

The three `-edits` keys are used only when the text itself could not be analysed and the names come
from the page history instead — who edited most, rather than who wrote what you are reading. They
are worded as a weaker claim on purpose. If you override them, keep them weaker than their
counterparts: the same names under `wikifame-summary-prefix` would credit people for text they may
never have written.

If you do override something:

- Keep every `$1` and `$2` placeholder. They are replaced by real links and numbers; a message that
  drops its placeholder silently loses that link.
- `{{PLURAL:$1|…}}` is supported and should be kept, with as many forms as the language needs.
- Values are inserted as text, never as HTML. Wikitext markup will appear literally.
- Only string values are applied; anything else is ignored.

## Rich content: images, video, anything wikitext can do

The `messages` object handles words. When you want more than words in the history box — a diagram,
a screenshot of the history page with callouts, a short Commons video, a template — write a
**wikitext page** and point at it:

```json
"historyIntroPage": "User:YOU/wikifame-history"
```

Then create `User:YOU/wikifame-history` and write ordinary wikitext:

```wikitext
[[File:Wikipedia history page annotated.png|thumb|right|300px|Each line is one version.]]
Every line below is one version of this article, and the name on it is the person who
made that change. Nothing here is permanent: you can add to it too.
```

MediaWiki parses and sanitises that page, and the script inserts the result. Images, galleries,
Commons video, templates, tables, and formatting all work, because none of it is being handled by
this script — the wiki does the work, and you preview and revert it like any other page.

### What you should know before using it

- **It replaces, it does not add.** When the page exists, its content takes the place of the
  built-in explanation *and* the editing-help sentence. `editHelpPage` and `sandboxPage` stop
  affecting the box, so re-add those links in your wikitext if you still want them.
- **The "you can also edit this article" line stays**, always, below your content. It is built by
  the script on purpose: your page is parsed on its own, so it has no idea which article the reader
  is looking at. `{{FULLPAGENAME}}` in your wikitext would resolve to the *introduction page*, and
  the link would offer to edit the wrong page. Same for `{{PAGENAME}}` and friends.
- **One language per page.** The script tries `…/fr-ca`, then `…/fr`, then the bare title, using
  your interface language. So `User:YOU/wikifame-history/fr` serves French readers and
  `User:YOU/wikifame-history` catches everyone else. This is the piece `messages` gets wrong, and
  the reason to prefer this route for anything longer than a phrase.
- **Weight is on you.** This box renders on every history view. A large image or an autoplaying
  video would be paid for every time. The script sets images to load lazily and stops video from
  autoplaying or preloading, but it cannot make a 4 MB PNG small.
- **Video needs TimedMediaHandler**, which most Wikipedias have. Where it is missing you get a
  plain player rather than the enhanced one. Nothing breaks.
- **A missing page is not an error.** If the page does not exist, is deleted, or the wiki is
  unreachable, the built-in wording renders instead. The result — including "there is no such
  page" — is cached for 24 hours, so create the page *before* pointing at it, or expect up to a
  day's delay in a tab you have already opened.

### Showing the real number of authors

Your page is parsed once and reused for every article, so it cannot contain this article's
contributor count. Declare a slot instead, and the script fills it in:

```wikitext
Cet article a été écrit par <span class="wikifame-count">des dizaines de personnes</span>.

<span class="wikifame-number">plusieurs centaines</span> de personnes y ont contribué.
```

| Class | Becomes |
| --- | --- |
| `wikifame-count` | The full localised phrase — `1 234 personnes`, correctly pluralised, prefixed with `au moins` when the count is a lower bound. |
| `wikifame-number` | Just the formatted number — `1 234` — for when you write the sentence yourself. |

Use as many of each as you like; they all get the same value.

- **Whatever you write inside the element is the fallback.** It stays exactly as written if the
  article has no result yet, if the wiki is not covered, or if the API is unreachable. So write
  something that reads well on its own — `des dizaines de personnes`, not `…`, and never leave it
  empty.
- **No slot means no request.** A page without either class costs nothing on a history view, which
  is why this is opt-in rather than always on.
- **It does not wait.** The box renders first and the number lands a moment later. On an article
  whose result is still being computed the script does not retry and does not rewrite the box —
  your fallback wording simply stays.
- **It is one API call, not two.** The count shares the session cache with the article-view
  sentence, keyed by page, so reading an article and then opening its history costs one request.

### JavaScript

Not through the configuration page — a `.json` page that might contain code is a page nobody can
review by reading it. The script fires two hooks instead:

```javascript
mw.hook( 'wikifame.history' ).add( function ( box, wikiConfig ) {
	// box is the rendered <div>, already in the page.
} );

mw.hook( 'wikifame.summary' ).add( function ( summary, data ) {
	// data.contributors, data.distinct_contributors, data.computed_at…
} );
```

Put that in your own `common.js`, after the `importScript` line. This gives you the full DOM and
the full language, which is strictly more than a configuration page could ever offer, and it keeps
the JSON to things that can be validated.

## When nothing renders

The script is deliberately silent — it never shows an error to a reader. Work through these in
order:

| Symptom | Likely cause |
| --- | --- |
| Nothing on any article, this wiki only | The wiki is not covered by WikiWho, or `enabled` is `false`. |
| Nothing on one article, others fine | No result computed yet. The first request queues the work; come back later. Normal for a page nobody has viewed with the script before. |
| Nothing anywhere, on every wiki | The script is not loading. Check the `importScript` line in your `common.js`, and the browser console. |
| The sentence shows but the help sentence does not | `editHelpPage` and `sandboxPage` are not both set, `showHistoryIntro` is `false`, or `historyIntroPage` is set and has replaced it. |
| Configuration edits have no effect | Stale `sessionStorage`; open a new tab. Or a key is misspelled — unknown keys are ignored silently. |
| `historyIntroPage` is set but the built-in text still shows | The page does not exist under any of the three titles tried, or its absence is still cached. Create it, then open a new tab. |
| Custom content renders but looks wrong | It is your wikitext, parsed as usual. Preview the page on its own; what you see there is what the box gets. Oversized media is constrained by `wikifame.css`, not fixed. |
| The author count stays on its fallback wording | No result for this article yet — open the article itself once, wait, then come back. Also check the class name: `wikifame-count`, on an element, not a template parameter. |

The attribution sentence appears on normal article views only: not on diffs, not on old revisions,
not outside the main namespace.

For a deeper look, open the browser console. Initialisation failures are logged through
`mw.log.warn` with a `WikiFame:` prefix.

## Later: becoming a site-wide gadget

Once a community adopts WikiFame for all its readers, the configuration stops being personal and
moves to `MediaWiki:Wikifame-config.json` on that wiki — same fields, same file, one copy shared by
everyone, editable by interface administrators. The files in [`config/`](../config) become the
starting point for that page instead of for a personal one.

That step needs a community discussion first; nothing in the design substitutes for asking. Until
then, user space keeps the prototype installable by anyone, with no rights and no gatekeeper.

## See also

- [Operations runbook](operations.md) — the Toolforge side, including how to pin a wiki for
  prewarming.
- [Architecture](architecture.md) — how a wiki is resolved and what gets stored.
- [ADR-0003](decisions/0003-universal-wiki-support.md) — why configuration lives on-wiki rather
  than in the service.
- [ADR-0004](decisions/0004-on-wiki-extensibility.md) — why rich content is a wikitext page and
  JavaScript is a hook, rather than either living in the JSON.
