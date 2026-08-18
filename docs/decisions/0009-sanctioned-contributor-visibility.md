# ADR-0009: Accounts a wiki has lastingly excluded are not named

- Status: Accepted
- Date: 2026-08-18
- Algorithm version: unchanged (this changes what is presented, not what is computed)

## Context

Until now WikiFame named whoever wrote the text, and nothing about that account's standing
entered into it. [ADR-0001](0001-attribution-policy.md) and
[ADR-0006](0006-bot-exclusion.md) exclude four things — missing users, temporary accounts,
bots by group, bots by name — and a block is not among them. That was never argued; it
simply never came up.

It is worth arguing, because the credit line speaks in the wiki's voice. "Rédigé par X"
under an article title reads as the project vouching for X. When the project has blocked
X indefinitely, or a steward has locked the account globally, the sentence is the tool
contradicting the community that hosts it — and doing so in the most prominent place a
reader will look.

The opposite error is just as real. A block is an ordinary editorial event. People are
blocked for a week over an edit war and come back; treating every sanction as a
disqualification would erase contributors for a fortnight's lapse, and would make the
credit line a public sanction notice, which is not its job. Between "blocked for three
days" and "banned for life" there is a line, and only a duration can draw it.

Two facts are needed and they cost very different amounts. A **local block** rides along on
the `list=users` call the resolver already makes: adding `blockinfo` to `usprop` costs no
extra request and returns `blockedtimestamp`, `blockexpiry` and `blockpartial`. A
**CentralAuth lock** — the strongest statement the movement makes about an account, and
the one a local check cannot see, because an account locked for cross-wiki abuse may have
no block at all on the wiki where it wrote the article — is only available from
`meta=globaluserinfo`, one account per request.

## Decision

**An account whose exclusion is lasting is dropped from the names when the response is
built. The rule is configurable per wiki and is on by default.**

An account is not named when either:

- it is **globally locked for a reason that reads as a sanction**, whatever the local
  threshold, because such a lock has no expiry to
  measure and is not a local decision; or
- it carries a **non-partial local block** whose duration exceeds
  `MAX_VISIBLE_BLOCK_SECONDS` — ninety days by default — where an indefinite block counts
  as exceeding any threshold.

Everything else is named. In particular:

| Case | Named? | Why |
| --- | --- | --- |
| No block | yes | the ordinary case |
| Block of 90 days or less | yes | an editorial event, not an exclusion |
| Block longer than 90 days | no | the wiki has stopped treating this as a member |
| Indefinite block | no | no end date is not a short block |
| Partial block | yes | a block scoped to a page is a remedy, not a ban |
| Block that has since expired | yes | the row outlived the sanction |
| Locked for abuse, spam, ban evasion | no | the strongest exclusion there is |
| Locked as deceased, vanished, compromised | yes | the same mechanism, the opposite meaning |
| Locked for an unrecognised reason | yes | absence of data is not a finding |
| Account never checked | yes | absence of data is not a finding |

The last row is the one that matters operationally. A sync that has never run leaves every
account absent from the table, and reading absence as "sanctioned" would blank the names
off a whole wiki the first time the job was late. This is the same rule the opt-out
follows: an empty answer is an instruction only when it is an answer.

#### A lock does not mean one thing

The first production run of this rule withheld the credit of five deceased Wikipedians on
enwiki — SlimVirgin, Yoninah, MarnetteD, Gobonobo, Bhadani — none of whom carried any block
at all. That was not a bug in the code; it was this decision being wrong. CentralAuth has
one lock mechanism and stewards use it for opposite purposes. They lock abusers, and they
lock the accounts of editors who have died, who have exercised the right to vanish, or
whose account was compromised. `meta=globaluserinfo` reports the flag and not the purpose,
so reading the flag alone turns a memorial into a ban.

The purpose is recoverable: the `globalauth` log on Meta carries the steward's reason, and
the sanctioning ones are formulaic — "long-term abuse", "spam-only account", "lock
evasion", "cross-wiki abuse", "globally or WMF banned user". Courtesy locks share none of
that vocabulary. So a lock withholds a name only when its reason affirmatively matches a
sanction, and an unreadable, missing or unfamiliar reason leaves the account nameable.

That default is the same one the rest of the rule runs on. Failing towards naming costs a
sanction occasionally missed, which leaves a name up that the block pass usually catches
anyway. Failing the other way costs someone erased for a reason nobody can state, and the
population it erases first is the one least able to object.

The reason costs one extra request, made only for accounts already found locked — well
under one percent of those tracked — and asked of Meta rather than of the wiki being
served, because only Meta holds that log.

## Where the rule runs

**When the response is built, never when the result is computed.** This is the whole
design and it follows from one observation: *a sanction changes without anyone touching
the article*. If `should_highlight_contributor` applied the rule, the verdict would be
frozen into a stored row and stay wrong for as long as that row stays fresh — up to
`PAGE_FRESHNESS_SECONDS`, ninety days. Someone blocked this morning would keep their
credit until winter.

Serving-time filtering also makes the setting real rather than nominal. A threshold baked
into stored rows would be part of the algorithm's identity, so moving it would mean a new
`ALGORITHM_VERSION` and a full recomputation — which in practice means it would never be
moved, and could never differ between wikis. Filtering at the edge makes a change take
effect on the next response, in both directions, at no cost.

[ADR-0007](0007-cache-validation.md) is what makes this enforceable rather than advisory.
The `ETag` is a hash of the response body, so withholding a name changes the tag and a
reader holding the older copy cannot revalidate it.

### The materialisation job

The serve path may not call MediaWiki, so `standing-sync` reads the two facts on a
schedule and leaves behind a `contributor_standing` table read with one indexed lookup.

It tracks **only the accounts stored results actually name** — about 7,000 across all three
active wikis, not the wikis' block logs, because the top three contributors of popular
articles are the same prolific editors over and over. Blocks are refreshed for every
tracked account on every run, fifty per request. Locks are **rationed and rotated**:
`STANDING_LOCK_CHECKS_PER_RUN` accounts per run, never-checked ones first, each row
remembering when its turn came. At the defaults every account's lock status is confirmed
within a day.

The table stores **facts with timestamps, not verdicts**. The threshold is applied per
response, so it exists in exactly one place and cannot drift.

### Cost

- **A name is dropped, not replaced.** Only three contributors were ever stored, so hiding
  one leaves two rather than promoting a fourth. Backfilling would require recomputation,
  which is precisely what this design avoids. The share is not lost: it moves into
  `other_contributors`, and `distinct_contributors` never changes.
- **Up to an hour of delay** between a block and the name disappearing, against fifteen
  minutes for the opt-out. A ban is a considered decision, not an emergency; the opt-out
  list remains the fast path when something must go now.
- **Up to a day of delay for a lock**, because of the rationing. The same reasoning
  applies, and a locked account is nearly always locally blocked too, so the block pass
  usually catches it first.
- **Wrongly withheld names.** A long block for something procedural — a compromised
  account, a username violation — reads to this rule as exclusion. The per-wiki threshold
  is the remedy; there is no per-account exception, and adding one would mean maintaining
  a list of people the tool has decided to keep naming, which is worse than the problem.
- **An expired block leaves no trace**, so the rule only ever sees currently-active
  sanctions. Someone blocked for two years in 2019 is named today. That is intended: the
  question is whether the wiki excludes this person now.

## Consequences

- No response field announces a withheld name, deliberately. A flag would be a
  machine-readable "one of this article's main authors is banned", which is a worse
  disclosure than the credit it replaced. `opted_out` stays as it is, because that is a
  fact about an article rather than about a person.
- `/v1/stats` reports `standing` per wiki — tracked, blocked, locked — but not how many
  names are withheld, because that depends on the threshold and would be a second copy of
  the rule.
- The rule can be switched off with `HIDE_SANCTIONED_CONTRIBUTORS=false`, and the sync
  keeps running so that switching it back on is immediate. A wiki can set its own line
  with `MAX_VISIBLE_BLOCK_SECONDS_BY_WIKI`; `0` means any active non-partial block
  withholds the name.
- Ninety days is numerically equal to `PAGE_FRESHNESS_SECONDS` and unrelated to it. One is
  about when an answer goes stale, the other about what a community has decided about a
  person. Do not tie them.
- **This is not a redaction mechanism.** The names stay in the stored rows, and the page
  history is public and unchanged. A revision whose content must actually be suppressed is
  a matter for oversighters.
