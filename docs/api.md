# API contract

The gadget consumes one revision-specific, read-only resource. Keep this contract backwards
compatible within `/v1`; add a new API version for breaking changes.

## Get attribution

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

Immutability is safe because the revision and algorithm version are part of the cache identity.

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
