# ADR-0007: Cached answers must stay checkable

- Status: Accepted
- Date: 2026-08-17
- Algorithm version: unchanged (this decision does not alter any result)

## Context

[ADR-0006](0006-bot-exclusion.md) widened what counts as a bot, and the deploy that carried it was
verified end to end: the worker restarted after the build, `/v1/stats` announced
`attribution-ladder-v3`, and a direct request for *Mairé-Levescault* returned Roland45, Lyaouanc
and BTH, with Roland45-Bot — 31.6% of the surviving text — correctly gone.

The article still showed Roland45-Bot in the browser.

The Resource Timing API recorded **no request at all** to the API across a full page load while
the box rendered three names. Two caches were replaying an answer computed at 19:02Z, an hour
before the deploy:

- **HTTP.** `/v2` sent `Cache-Control: public, max-age=86400` with no `ETag` and no
  `Last-Modified`. With no validator a browser cannot cheaply ask "is this still current?", so it
  simply waits out the day. `/v1` was worse: `max-age=31536000, immutable`.
- **The gadget's `sessionStorage`.** Keyed `wikifame:<CACHE_VERSION>:<wiki>:<page id>` and held
  for 24 hours. `CACHE_VERSION` versions the payload *shape*, not the server's answer.

Both caches were reasoned about as if the attribution of a revision were a fact about that
revision. It is not. It is the output of a policy applied to that revision, and the policy is not
named anywhere in the URL or the cache key. ADR-0002 made this assumption explicit for v1
("revision-exact and immutable"); it was wrong in the same way, and had simply never been tested
by a policy change reaching production.

The cost was not theoretical. The entire point of ADR-0006 is that a bot stops being presented as
a person. For up to a day on v2 — a year on v1 — that change was invisible to anyone who had
loaded the page before it shipped.

## Decision

- Every ready response carries an `ETag` derived by hashing the response body. Hashing the body
  rather than assembling a tag from chosen fields means the tag cannot drift from what it
  describes: anything that changes the answer changes the tag, `algorithm_version` included.
- Both `/v1` and `/v2` honour `If-None-Match` and answer `304 Not Modified` with the headers and
  no body.
- `/v1` drops `immutable`.
- One freshness horizon, five minutes by default, shared by `PAGE_CACHE_SECONDS`,
  `READY_CACHE_SECONDS`, and the gadget's `CLIENT_CACHE_MAX_AGE_MS`. The number is stated in three
  places because three different layers enforce it; they must not disagree.
- The seven-day `stale-while-revalidate` allowance is unchanged.
- The freshness check and any enqueue run *before* the `304` is returned, so a request that
  receives no body still moves a stale page towards recomputation.

## Consequences

A policy change now reaches readers within five minutes of their next page view instead of within
a day. The cost is one conditional request per reader per page per five minutes; it is normally a
`304` carrying no body, and `stale-while-revalidate` means the reader waits for none of it.

The gadget's session cache still exists, because its purpose — sparing a second request when a
reader moves between an article and its history — is served entirely within five minutes. Its key
is unchanged; shortening its life was enough, and putting `algorithm_version` into the key is not
possible anyway, since the version is only known once the answer arrives.

Three durations now sit close together in `config.py` and mean genuinely different things: ninety
days is how long a stored row stays usable, five minutes is how long a reader may reuse one
without checking, seven days is how long a stale copy may be shown while a fresh one is fetched.
`tests/test_config.py` asserts all three so a future edit cannot quietly collapse them.

This supersedes the caching bullets of [ADR-0002](0002-page-freshness.md), which stand as the
record of what was tried first.
