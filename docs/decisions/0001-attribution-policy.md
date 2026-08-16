# ADR-0001: Initial attribution policy

- Status: Accepted
- Date: 2026-08-16
- Algorithm version: `surviving-tokens-v1`

## Context

The gadget highlights three named accounts while linking an aggregate historical contributor
count. WikiWho exposes token originators as numeric user IDs or anonymous identifiers. A public
highlight can create unwanted visibility for temporary or anonymous contributors.

## Decision

For the three highlighted names:

- count tokens containing at least one Unicode letter or number;
- rank by surviving-token count in the exact current revision;
- resolve registered numeric IDs to current usernames;
- exclude current bots, temporary accounts, missing accounts, IP addresses, and anonymous actors;
- require at least 20 tokens and a 1% share;
- show at most three accounts.

A temporary account is detected by its resolved username beginning with `~`, matching the current
MediaWiki convention. This is a product rule, not an assertion that the account made no valuable
contribution.

The aggregate count continues to include temporary and anonymous actors because the current
history-count endpoint does not return reliable subtotals for subtracting them. The UI links this
aggregate to the full history and never describes it as a count of verified physical people.

## Consequences

- Temporary accounts never appear by name in the sentence.
- The visible names and aggregate count intentionally use related but non-identical populations.
- A future reliable categorized count can change the aggregate rule.
- Any change affecting output requires a new `ALGORITHM_VERSION` and a new ADR or amendment.
- WikiWho measures surviving source tokens; it does not measure review, sourcing, maintenance,
  media work, reverted contributions, or article quality.
