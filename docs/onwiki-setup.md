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
| `wikifame-people` | `{{PLURAL:$1|$1 person|$1 people}}` |
| `wikifame-others` | `{{PLURAL:$1|$1 other person|$1 other people}}` |
| `wikifame-at-least` | `at least $1` |
| `wikifame-user-title` | `View the user page of $1` |
| `wikifame-share` | `$1 of the currently visible tokens` |
| `wikifame-history-title` | `View the full page history` |
| `wikifame-tooltip` | `Main authors of the text according to WikiWho.` |
| `wikifame-computed` | `Data computed on $1.` |
| `wikifame-history-intro` | `Each line is one version of the article, showing who changed it.` |
| `wikifame-history-help` | `To get started, read $1 or practise in $2.` |
| `wikifame-history-help-label` | `the editing help` |
| `wikifame-history-sandbox-label` | `the sandbox` |
| `wikifame-history-edit` | `You can also $1.` |
| `wikifame-history-edit-label` | `edit this article directly` |

If you do override something:

- Keep every `$1` and `$2` placeholder. They are replaced by real links and numbers; a message that
  drops its placeholder silently loses that link.
- `{{PLURAL:$1|…}}` is supported and should be kept, with as many forms as the language needs.
- Values are inserted as text, never as HTML. Wikitext markup will appear literally.
- Only string values are applied; anything else is ignored.

## When nothing renders

The script is deliberately silent — it never shows an error to a reader. Work through these in
order:

| Symptom | Likely cause |
| --- | --- |
| Nothing on any article, this wiki only | The wiki is not covered by WikiWho, or `enabled` is `false`. |
| Nothing on one article, others fine | No result computed yet. The first request queues the work; come back later. Normal for a page nobody has viewed with the script before. |
| Nothing anywhere, on every wiki | The script is not loading. Check the `importScript` line in your `common.js`, and the browser console. |
| The sentence shows but the help sentence does not | `editHelpPage` and `sandboxPage` are not both set, or `showHistoryIntro` is `false`. |
| Configuration edits have no effect | Stale `sessionStorage`; open a new tab. Or a key is misspelled — unknown keys are ignored silently. |

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
