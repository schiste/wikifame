# ADR-0005: Attribution ladder below the token metric

- Status: Accepted
- Date: 2026-08-17
- Algorithm version: `attribution-ladder-v2`

## Context

[ADR-0001](0001-attribution-policy.md) has one metric. When it produces no name the page gets no
name, and a reader who opened a stub sees only a count. Two distinct failures reach that state:

- WikiWho answers HTTP 400 and will never answer anything else for that page;
- WikiWho answers, but no account holds 20 tokens and 1% of a page too short for anyone to.

Measured on fr.wikipedia, the first is rare: 1 page in 80 sampled at random, 1 in 30 among
bot-created stubs. The second is the common case for short articles. Both look identical from the
reader's side.

Edit counts can name someone on those pages, but they measure something else. On 30 sampled
articles the two rankings agreed on 47% of names and on no article agreed on all three. The
disagreement is not noise: on a sample of unrelated stubs, edit-count ranking crowned an account
whose contribution was mass page-moving. Ranking by edits and calling the result authorship would
be false.

## Decision

Three rungs, tried in order, each claiming strictly less than the one above:

1. **Surviving tokens** — ADR-0001 unchanged. Names accounts that wrote the text on screen.
2. **Edit counts** — accounts ranked by revisions made to the page, read from the MediaWiki Action
   API. Reached when rung 1 names nobody, for either reason above.
3. **Nothing** — no names. The aggregate distinct-contributor count is still served.

Constraints on rung 2:

- Exclusion is not reimplemented. Both rankings call the same `should_highlight_contributor`, so
  bots, temporary accounts and missing users are unnameable through either path, and can never
  diverge. Only the ordering differs, which is the one thing the two metrics disagree about.
- No minimum-share gate. A share of the edits is not a share of the text, so reusing the 1% figure
  would import a threshold chosen against a different measurement.
- Revisions with no `userid` are IPs or suppressed authorship. They count toward the total and
  toward nobody's tally.
- The walk stops at `TOP_EDITOR_MAX_REVISIONS` (default 5000). A history longer than that is left
  unranked rather than ranked from its newest slice: "most edits" computed over a window is a
  guess, and rung 3 is the honest answer.

The `metric` field already stored with every result carries which rung answered, so this needs no
schema change. The gadget words the sentence from that field: `wikiwho-surviving-alphanumeric-tokens`
earns "Article rédigé par …", `mediawiki-revision-count` gets "Article le plus modifié par …", and
any metric the gadget does not recognize — including one added after it ships — gets the weaker
wording rather than the stronger one.

WikiWho HTTP 400 is treated as permanent rather than retried. Both observed bodies (rejected
namespace, unknown revision) are statements about the page. It is not an indexing-lag signal
either: measured against fr.wikipedia edits seconds old, WikiWho returned 200 with the exact
revision every time.

## Consequences

- Pages WikiWho refuses stop exhausting eight attempts and serving `503` forever.
- Short articles gain three names that are true about edits and are not described as authorship.
- Two sentences now exist in the interface, and their difference is load-bearing. An operator
  overriding the on-wiki messages must keep the `-edits` wording weaker than the default one.
- Rung 2 costs up to ten extra Action API calls, only for pages rung 1 could not rank.
- `ALGORITHM_VERSION` becomes `attribution-ladder-v2`; every stored result recomputes. The name
  dropped "surviving-tokens" because that is now one rung rather than the whole policy.
- Naming by edit count remains weaker evidence than naming by surviving text. If a future metric
  measures authorship better, it belongs above rung 2, not beside it.
