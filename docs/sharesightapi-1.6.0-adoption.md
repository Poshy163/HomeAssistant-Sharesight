# Sharesight 2.4.0: SharesightAPI 1.6.0 adoption

The previous dependency upgrade left HA using raw reads and its own copies of
the client's error classifiers. This release uses the client's classifications
and typed read helpers, preserves the existing request controls, and adds
on-demand holding value history, instrument price history and current portfolio
value actions.

## Request and response contracts

- `SharesightApiError` inherits the upstream status, authentication, entitlement,
  not-found, version-unsupported and retryable classifications. HA retains its
  transport-failure handling, brute-force lockout and header-only rate limits.
  OAuth HTTP exceptions use the client's exported retryable-status set.
- `read_client.py` adapts the pinned client's GET helpers to the existing
  authenticated transport. A separate adapter for each request retains HTTP
  status and headers without sharing mutable response metadata. The coordinator
  continues to own OAuth, concurrency, minute budgeting, retries and fallback.
- Known 1.6.0 helper contracts cover watchlists, fundamentals, official costs,
  value histories, current value, prices, SSO, tax reports, user settings,
  benchmarks and account metadata. Unknown routes or query controls use the
  original raw transport without dropping parameters. The existing combined
  performance request remains unchanged.
- Analytics and service boundaries use upstream holding, payout, trade, tax,
  performance, fundamentals and value-series types. These types document fields;
  they do not replace runtime shape checks. Extra source fields are preserved.
- The adapter overrides the pinned client's private `_request` hook. Contract
  tests execute the real published helper implementations and verify their
  routes, parameters and metadata; rerun them on future dependency upgrades.

## New response actions

See the [README examples](../README.md#holding-value-and-instrument-price-history).

- `get_holding_value_history`: dated value observations for one currently held
  symbol. Supports live chart wrappers, bare arrays and documented value-series
  wrappers. Keeps zeroes, sorts and deduplicates dates, and applies the requested
  end date locally because the endpoint only accepts a start date.
- `get_instrument_price_history`: one page of prices with source OHLC, volume
  and last-traded fields. `has_more` exposes a next-page indication; no arbitrary
  pagination URL is followed.
- `get_portfolio_value`: a fresh lightweight balance request, separate from the
  cached portfolio-summary action.

History windows are inclusive, at most 366 days, and cannot include future dates.
Invalid windows, ambiguous symbols and active cooldowns are rejected before I/O.
`CODE.MARKET` resolves duplicate instrument codes across exchanges. Unidentified
holding-value currency stays null. Unknown response shapes and entitlement
failures remain distinguishable from a valid empty series. No missing days are
interpolated, no holding statistics are imported, and no extra polling calls or
entities are introduced.

## Validation

- 451 portable tests passed.
- 485 tests passed on each of HA 2026.7.4 and HA 2026.9.1, using the published
  SharesightAPI 1.6.0 distribution. Coverage: 79.15% and 79.20%, respectively.
- The adapter has 100% statement coverage, including concurrent metadata,
  unknown query controls and the upstream status classifications.
- Real HA service-registry tests exercise all three new actions, shared budget
  accounting, response headers, invalid dates and rejection after entry unload.
- Ruff lint/format, compilation, dependency checks and Hassfest passed.

## Deployment recovery

Before copying the integration, HA backup `385ebc62` captured configuration and
the recorder database. A separate local copy of the previous integration was
verified against all 28 remote source files. The 2.4.0 deployment contains 29
source files, all verified by SHA-256 after copying over Samba.

For code-only rollback, restore the saved 2.3.1 integration directory, remove
the newly introduced `read_client.py` from that integration directory, verify
the saved hashes and restart HA. The dependency remains 1.6.0 in both versions;
no registry or statistics migration is required. The HA backup is available
for broader recovery if needed.

## Live results

HA loaded 2.4.0 with the installed SharesightAPI 1.6.0 distribution. Startup and
scheduled polling completed without degradation, parked endpoints or logged
failures. All 890 registered integration entities were retained; the same
838 valued, 50 unknown and three unavailable states remained in the broader
Sharesight search. No active Repairs issues were present.

Holding value history returned eight source observations for 2026-08-31 through
2026-09-07. The lightweight current-value action and fundamentals action
succeeded, including official average purchase price and cost base.

The instrument-price action returned HTTP 406: the server rejected V2 for that
endpoint on the live token. The official [V2 API project configuration](https://portfolio.sharesight.com/api/2/doc/api_project.json)
uses `/api/v2`; no alternative request URL or version header is documented for
this route. The integration reports the refusal and leaves normal polling
healthy rather than guessing another API version.

The [official V2 response example](https://portfolio.sharesight.com/api/2/doc/api_data.json)
also uses `instrument_prices`, while the 1.6.0 response model specifies `prices`.
The action accepts both envelopes, with a regression test for each. Successful
live instrument-price retrieval remains unverified because of the server's
version refusal.
