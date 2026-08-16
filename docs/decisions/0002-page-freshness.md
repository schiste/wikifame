# ADR-0002: Serve page attribution for a 90-day freshness window

## Status

Accepted on 2026-08-16.

## Context

WikiWho attribution changes much more slowly than article revisions. Recalculating after every
edit would create avoidable load for WikiWho, Toolforge workers, and ToolsDB. For the prototype,
the product accepts a top-three attribution calculated within the previous three months.

The stored result must nevertheless remain auditable: maintainers and readers need to know which
exact Wikipedia revision was analyzed and when.

## Decision

- Keep immutable stored rows keyed by wiki, page ID, source revision ID, and algorithm version.
- Add a `/v2` read contract that selects the newest row for the page and algorithm.
- Treat that row as fresh for 90 days by default, regardless of ordinary intervening edits.
- Return an expired row immediately and enqueue one current-revision P100 refresh. Do not blank
  the gadget while recalculation is pending.
- Return `202 pending` only when no result exists for the page and algorithm.
- Expose `requested_revision_id`, `source_revision_id`, `computed_at`, `fresh_until`, `is_fresh`,
  and `refreshing` so the compromise is observable.
- Cache v2 responses in browsers for one day with a seven-day stale-while-revalidate allowance.
- Make all three durations configurable without changing the algorithm version.
- Make prewarm and backfill skip pages with a fresh result.

## Consequences

Popular or frequently edited pages normally require at most four calculations per year. A stale
result can remain visible during a WikiWho outage, which improves availability but means the top
three may temporarily be older than 90 days. The exact source revision and date make that age
explicit.

V1 remains revision-exact and immutable for backwards compatibility. A future manual refresh
control should enqueue a privileged refresh while retaining the current row; authorization and
abuse protection are deliberately left for a separate decision.
