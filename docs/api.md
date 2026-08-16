# API contract

The gadget consumes the page-freshness resource under `/v2`. The exact-revision `/v1` contract
remains available for backwards compatibility. Keep each version backwards compatible; add a new
API version for breaking changes.

## Get current page attribution (`v2`)

```http
GET /v2/{wiki}/pages/{page_id}?revision_id={revision_id}
```

The gadget sends the current revision so a refresh job knows what to calculate. A ready result is
selected by `(wiki, page_id, algorithm_version)`, not by exact requested revision. This lets one
calculation remain usable across ordinary edits for `PAGE_FRESHNESS_SECONDS`, 90 days by default.

### Ready or refreshing — `200 OK`

```json
{
  "status": "ready",
  "wiki": "frwiki",
  "page_id": 123,
  "requested_revision_id": 789,
  "source_revision_id": 456,
  "title": "Exemple",
  "algorithm_version": "surviving-tokens-v1",
  "metric": "wikiwho-surviving-alphanumeric-tokens",
  "contributors": [],
  "distinct_contributors": 47,
  "other_contributors": 47,
  "count_limited": false,
  "countable_tokens": 987,
  "computed_at": "2026-08-16T10:00:00Z",
  "fresh_until": "2026-11-14T10:00:00Z",
  "is_fresh": true,
  "refreshing": false,
  "methodology_url": "https://github.com/schiste/wikifame/blob/main/docs/architecture.md"
}
```

- `source_revision_id` is the exact revision analyzed by WikiWho.
- `requested_revision_id` is the revision displayed when the request was made.
- `is_fresh` is true until `computed_at + PAGE_FRESHNESS_SECONDS`.
- When the result is expired, the same payload is returned with `is_fresh: false` and normally
  `refreshing: true`; a P100 refresh has been queued for the requested revision.

Ready responses use bounded browser caching:

```http
Cache-Control: public, max-age=86400, stale-while-revalidate=604800
X-WikiFame-Algorithm: surviving-tokens-v1
X-WikiFame-Source-Revision: 456
```

The browser may therefore reuse a response for one day, but it is never immutable. Operators can
tune these durations with `PAGE_CACHE_SECONDS` and `PAGE_STALE_WHILE_REVALIDATE_SECONDS`.

### No result yet — `202 Accepted`

The response shape is the same pending shape documented for v1 below. The request creates or
reuses one durable P100 job for the requested revision. The gadget retries briefly during the
current visit and otherwise waits for a later page view.

## Get exact revision attribution (`v1`, legacy)

```http
GET /v1/{wiki}/pages/{page_id}?revision_id={revision_id}
```

Initial scope:

- `wiki`: `frwiki`
- `page_id`: positive MediaWiki page ID
- `revision_id`: positive current revision ID

The API deliberately does not accept a title as identity. Page titles can change; page and
revision IDs are stable.

### Ready — `200 OK`

```json
{
  "status": "ready",
  "wiki": "frwiki",
  "page_id": 123,
  "revision_id": 456,
  "title": "Exemple",
  "algorithm_version": "surviving-tokens-v1",
  "metric": "wikiwho-surviving-alphanumeric-tokens",
  "contributors": [
    {
      "user_id": 10,
      "username": "Alice",
      "token_count": 310,
      "share": 0.314
    }
  ],
  "distinct_contributors": 47,
  "other_contributors": 46,
  "count_limited": false,
  "countable_tokens": 987,
  "computed_at": "2026-08-16T10:00:00Z",
  "methodology_url": "https://github.com/schiste/wikifame/blob/main/docs/architecture.md"
}
```

`share` is a fraction between zero and one. `other_contributors` is always
`max(0, distinct_contributors - contributors.length)`.

Ready responses use:

```http
Cache-Control: public, max-age=31536000, immutable
X-WikiFame-Algorithm: surviving-tokens-v1
```

Immutability is safe for v1 because the revision and algorithm version are part of the cache
identity. New gadget clients should use v2.

### Pending — `202 Accepted`

```json
{
  "status": "pending",
  "wiki": "frwiki",
  "page_id": 123,
  "revision_id": 456,
  "retry_after": 30
}
```

The request created or reused one durable queue item. The gadget intentionally renders nothing
and does not poll continuously. A later page visit can consume the ready result.

### Unavailable — `503 Service Unavailable`

```json
{
  "status": "unavailable",
  "wiki": "frwiki",
  "page_id": 123,
  "revision_id": 456,
  "error_code": "upstream_unavailable"
}
```

Transient dead jobs become eligible again after `DEAD_RETRY_SECONDS`. Permanent data errors stay
unavailable for that algorithm version.

## Operational endpoints

- `GET /healthz`: database connectivity probe; returns `{"status":"ok"}`.
- `GET /v1/stats`: aggregate ready and queue counts. Do not scrape at high frequency because an
  exact result count can become expensive on a large InnoDB table.
- `GET /docs`: generated OpenAPI interface. This documents HTTP structure, while this file
  documents behavioral guarantees.

## CORS and privacy

CORS permits the configured French Wikipedia origins. CORS is not authentication; scripted
clients can still call the public API. Responses contain no reader data. Operational access logs
must not be used to reconstruct reading histories.
