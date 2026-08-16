# Architecture and scaling rules

## Request path

1. The gadget requests `GET /v2/frwiki/pages/{page_id}?revision_id={revision_id}`.
2. The API selects the newest result for the page and current algorithm.
3. A result younger than 90 days is returned with a bounded one-day browser cache.
4. An older result is returned immediately and a P100 refresh is enqueued in the background.
5. A page with no result creates one durable queue row and returns `202 pending` immediately.
6. A continuous worker validates that the requested revision is still current.
7. The worker fetches token provenance from WikiWho, resolves user IDs through the
   MediaWiki Action API, obtains the historical editor count, and stores a compact result.

No WikiWho request runs in the web process. A unique database constraint collapses a
thundering herd for the same page and revision into one job.

## Storage identity, freshness, and updates

The immutable identity is:

```text
wiki + page_id + revision_id + algorithm_version
```

Titles are metadata rather than identity because pages can be renamed. V1 reads this exact
identity. V2 selects the newest stored result by `wiki + page_id + algorithm_version`, then exposes
its revision as `source_revision_id` for provenance.

The default freshness window is 90 days. Ordinary edits do not invalidate a fresh page-level
result because the product accepts attribution calculated within the last three months. Once the
window expires, stale-while-revalidate keeps the old data visible while exactly one current-
revision job is queued. Pending work for older revisions is marked `superseded`. The cleanup job
removes old non-current result versions but always retains the newest computed version for a page.

This policy separates three clocks:

- ToolsDB calculation freshness: 90 days;
- browser and intermediary freshness: one day;
- stale browser reuse during transient failure: seven days.

All three are configurable. A future editor-triggered invalidation can enqueue a refresh without
discarding the last known result; it does not require changing the storage identity.

Changing any attribution rule that can affect output requires a new `ALGORITHM_VERSION`.
This prevents an old response from being confused with a new interpretation of contribution.

## Attribution policy (`surviving-tokens-v1`)

- Count WikiWho tokens containing at least one Unicode letter or number.
- Rank origin editors by count of tokens surviving in the requested revision.
- Resolve numeric WikiWho editor IDs to current Wikimedia usernames.
- Permanently exclude bots, temporary accounts, missing users, IPs, and anonymous actors from
  the top three.
- Require at least 20 surviving tokens and 1% of the countable tokens.
- Keep anonymous and temporary actors in the historical distinct-contributor count.
- Subtract accounts that currently hold the MediaWiki `bot` right from that total.

“Exclude temporary accounts” refers to public highlighting, not the aggregate count. The current
MediaWiki history-count endpoint does not provide a reliable temporary-account subtotal. The UI
therefore never names temporary accounts, while the linked aggregate can still include them.
See [ADR-0001](decisions/0001-attribution-policy.md).

This metric recognizes originators of currently visible wikitext. It does not measure research,
review, maintenance, media work, reverted contributions, or the quality of an edit.

## Priority and load control

| Priority | Source | Purpose |
| --- | --- | --- |
| 100 | Live gadget cache miss | Serve demonstrated reader demand first |
| 50 | Union of seven available daily top-1000 lists | Keep likely requests warm |
| 10 | Resumable alphabetical backfill | Grow long-tail coverage without starving demand |

Workers retry transient failures with exponential backoff capped at six hours. A lease makes a
job recoverable after a worker crash. Two worker replicas are the conservative starting point;
increase concurrency only after agreeing on a safe rate with WikiWho operators.

The Analytics dataset can be published several days late. Prewarm scans backwards, skips `404`
days, and stops after finding seven available daily lists. Each run only queues pages whose newest
result has passed the 90-day window, so recurring popularity does not cause repeated WikiWho work.

## Millions of pages

The schema stores only a compact top-three result, not WikiWho token payloads. At roughly one
kilobyte per ready row, several million current results fit within the nominal ToolsDB 25 GB
boundary, but indexes, historical revisions, queue rows, and backups reduce that headroom.

For sustained multi-million coverage:

1. Measure average row and index size after the first 100,000 pages.
2. Keep only the newest result per page plus a short revision grace period.
3. Rate-limit backfill to the capacity explicitly accepted by WikiWho.
4. Move to a dedicated Trove database before ToolsDB reaches operational limits.
5. If the gadget becomes default for readers, migrate the API behind Wikimedia production
   caching; Toolforge is appropriate for an opt-in prototype, not Wikipedia-wide request volume.

## Abuse and privacy

The public API accepts only configured wikis and positive numeric IDs. Workers reject missing,
non-main-namespace, and stale revisions before contacting WikiWho. CORS limits browser use to
French Wikipedia, although CORS is not authentication and cannot prevent scripted traffic.

Revision-specific responses contain no reader identifier and are safe for public caching. The
service still receives an IP address and article ID on a cache miss, so access logs should use
short retention and must never be repurposed as reader profiles.

## Known boundaries

- WikiWho attributes surviving source-wikitext tokens, not rendered prose or editorial quality.
- The bot subtraction reflects accounts that currently hold the `bot` right, not their status at
  the time of every historical edit.
- `Base.metadata.create_all()` creates a fresh schema but is not a migration framework. Introduce
  versioned migrations before changing a database that already contains production data.
- The gadget has no page-specific fixture; every article uses traceable Toolforge data.
- Toolforge is the prototype host. A default gadget for all readers requires a Wikimedia-scale
  request path and privacy review.
