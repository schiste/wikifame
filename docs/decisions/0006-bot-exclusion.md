# ADR-0006: What counts as a bot

- Status: Accepted
- Date: 2026-08-17
- Algorithm version: `attribution-ladder-v3`

## Context

[ADR-0001](0001-attribution-policy.md) excludes bots by their local `bot` group. That is the
narrowest possible definition, and production showed two ways past it:

- **Globally flagged, locally unflagged.** `Addbot` was named as a top editor of
  *Barranquilla (homonymie)*. On fr.wikipedia it holds only `user`, `autoconfirmed` and
  `autopatrolled`; its bot status lives in CentralAuth as the global group `local-bot`.
  `list=users` never sees it.
- **Flagged nowhere at all.** `Gallicbot` was named as an author of two asteroid articles by the
  token metric. It holds no group on fr.wikipedia and no global group either. Nothing in the API
  distinguishes it from a person.

The second case is the older one: it predates [ADR-0005](0005-attribution-ladder.md) and was
reachable through the token metric all along. What the edit-count rung changed is the frequency.
Interwiki and maintenance bots dominate the histories of stubs, and stubs are exactly the pages
that rung serves.

## Decision

An account is excluded from being named when any of these holds:

1. it is missing or invalid, or its name starts with `~` (ADR-0001, unchanged);
2. it holds a **local** group whose name contains `bot`;
3. it holds a **CentralAuth global** group whose name contains `bot` — `local-bot`, `global-bot`,
   and any spelling added later;
4. **its username ends in `bot`**, in any case.

Rule 4 is a guess about identity rather than a measurement of behaviour, and it is accepted as
such. Wikimedia bot policies require operators to name approved bot accounts this way, which makes
the heuristic accurate in the direction that matters and is the only thing that catches an account
like `Gallicbot`. The cost is that real people whose pseudonym ends in those three letters —
Talbot, Abbot, Thibot — become unnameable on the strength of their pseudonym alone. They keep
their edits, their credit in the page history, and their place in the contributor count; what they
lose is being highlighted by this tool. That trade was made knowingly.

Group matching is by substring rather than by an allowlist of known group names, so a CentralAuth
group renamed or added upstream cannot silently make bots nameable again.

Both rankings call the same `should_highlight_contributor`, as ADR-0005 requires, so all four
rules apply identically to the token metric and the edit-count metric.

### Cost

The global check needs one CentralAuth request per account, since `meta=globaluserinfo` answers
about one user. Three things keep that bounded: the four rules run cheapest-first, so an account
already excluded by its name or its local groups is never looked up; the token path applies its
significance threshold before the exclusion rule, so accounts that could not be named are never
looked up either; and results are cached per process, which matters because a worker meets the
same few prolific bots across thousands of pages. In practice this is a handful of requests on the
pages that name anyone, and zero on the rest.

## Consequences

- `ALGORITHM_VERSION` becomes `attribution-ladder-v3`. Every stored result becomes unreachable and
  recomputes on demand; there is no recompute burst, because pages are recomputed lazily on request
  plus by the daily prewarm.
- Some pages lose a name and gain nothing in its place. A stub whose only substantial editors were
  bots now falls to rung 3 and shows a count alone. That is the correct outcome.
- Contributors named Talbot or Abbot are excluded. This is a known false positive, not a defect to
  be reported.
- The aggregate contributor count is **not** changed by this ADR. It comes from
  `prop=contributors&pcrights=bot`, a server-side filter on the local bot right, and applying rules
  3 and 4 to it would mean enumerating every contributor of every page. The count therefore still
  includes unflagged bots while the names no longer do. The asymmetry is deliberate: the count is
  an approximation of how many people worked on a page, and the names are a claim about specific
  people, which is the stricter thing to get right.
- A bot operator who renames their account away from the convention becomes nameable again. There
  is no way to detect that, and no reason to build one.
