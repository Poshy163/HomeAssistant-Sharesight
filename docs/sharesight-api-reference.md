# Sharesight API Reference (V2 + V3)

> Working notes for the HomeAssistant-Sharesight integration.
> Source of truth: the apiDoc-generated references at
> <https://api.sharesight.com/api/2/doc/index.html> and
> <https://api.sharesight.com/api/3/doc/index.html>
> (also mirrored at `portfolio.sharesight.com`).
> Endpoint lists below were extracted from the live apiDoc data files
> (`/api/2/doc/api_data.json`, `/api/3/doc/api_data.json`) on 2026-07-01 and
> re-verified complete against a fresh scrape on 2026-07-02
> (52 unique V2 + 103 unique V3 endpoints — all present below).
>
> Docs are rendered client-side, so the human-readable `index.html` pages
> only render in a browser; they are built entirely from the JSON data files.
> To re-scrape, pull the two JSON files (§8) — they contain every endpoint,
> parameter, and response field. The accompanying `api_project.json` files
> hold only a short header ("This page lists and describes all endpoints…")
> and the canonical base URLs, nothing more.

---

## 1. Base URLs, versions & auth

| Thing | Value |
|-------|-------|
| API base (prod) | `https://api.sharesight.com/api/` |
| V2 full prefix | `https://api.sharesight.com/api/v2/` |
| V3 full prefix | `https://api.sharesight.com/api/v3/` |
| Edge base (used when `use_edge_url`) | `https://edge-api.sharesight.com/api/` |
| OAuth authorize | `https://api.sharesight.com/oauth2/authorize` |
| OAuth token | `https://api.sharesight.com/oauth2/token` |
| Edge OAuth token | `https://edge-api.sharesight.com/oauth2/token` |

- **Auth:** OAuth 2.0 (authorization-code grant). Access token passed as
  `Authorization: Bearer <token>`.
- **Token lifetime:** ~30 min (integration refreshes with a 300 s margin).
- **Transport:** HTTPS only, JSON request/response.
- **V2** holds the bulk of endpoints. **V3** is the newer surface; Sharesight
  recommends checking V3 first and falling back to V2. Some V3 endpoints are
  flagged closed beta / "methods may change without notice" — treat V3
  response shapes as less stable than V2.
- A per-account **transaction log** (full XML/JSON request+response) is
  available in Sharesight account settings for debugging.

### Access / plans
- API access is granted per Sharesight email on **Standard, Premium or
  Business** plans (email `api@sharesight.com` to enable). The V3 User API may
  need to be enabled per-application by a Sharesight contact.

---

## 2. Rate limits & restrictions (IMPORTANT)

These are the hard constraints the integration must respect. They match the
constants in [const.py](../custom_components/sharesight/const.py).

| Limit | Value | Trigger / behaviour |
|-------|-------|---------------------|
| **Requests/minute** | **360** per consumer app | Exceeding → HTTP **403**. Response headers `X-MinuteRate-Limit` / `X-MinuteRate-Remaining` report the budget. SSO requests are exempt. |
| **Concurrent heavy requests** | **3** simultaneous | Applies **only** to the calculation-heavy reports: `performance`, `diversity`, `valuation`. Exceeding → HTTP **403** `"Too many parallel requests. Currently 3 in process."` |
| **Brute-force lockout** | ~**10 min** | Repeated calls with an invalid/expired token → HTTP **401** `"Token incorrect, expired or locked out. You must wait at least 10 minutes before calling our API again."` |
| **Trades per holding** | **1,500** max | Excess → HTTP **422** `"Limit of trades per holding reached (more than 1500)"`. |
| **Trades per portfolio** | ~**3,000** design ceiling | Beyond this, performance degrades (not a hard error). |

### Holding-limit headers (plan-gated reports)
`performance` (V2 & V3) and other holding reports cap the number of holdings
returned based on the user's plan. When capped, these response headers are set:

- `X-HoldingLimit-Limit` — the plan's holding limit
- `X-HoldingLimit-Total` — holdings in the requested portfolio
- `X-HoldingLimit-Reason` — human-readable explanation

> V3 `performance` accepts `include_limited=true` to still return
> *identifying* data (not full calcs) for holdings above the plan limit — the
> integration already passes this.

### How the integration defends against the above
- 360/min budget → default **5-minute** poll interval (`DEFAULT_SCAN_INTERVAL`).
- 3-concurrent heavy cap → `SHARESIGHT_HEAVY_CONCURRENCY = 3` semaphore around
  any path containing `/performance`, `/diversity`, `/valuation`.
- General burst cap → separate semaphore of 8 concurrent requests.
- 401 lockout → detected, then a 10-min global cooldown
  (`SHARESIGHT_LOCKOUT_COOLDOWN`) + `ConfigEntryAuthFailed`.
- 403 parallel/minute → 1-min global cooldown.
- Flaky optional endpoints → exponential backoff (1 h → 6 h max).

---

## 3. Endpoints currently used by this integration

From [coordinator.py](../custom_components/sharesight/coordinator.py):

| Ver | Endpoint | Purpose in integration |
|-----|----------|------------------------|
| V3 | `GET portfolios/{id}` | Startup: portfolio detail + `financial_year_end` |
| V3 | `GET portfolios` | List (refresh FY bounds) |
| V3 | `GET portfolios/{id}/performance` | Primary combined performance report |
| V3 | `GET portfolios/{id}/holdings` | Holdings list |
| V3 | `GET portfolios/{id}/user_setting` | User settings |
| V2 | `GET portfolios/{id}/performance` | Period reports (1d / 1w / 1m / YTD / FY) via `start_date`+`end_date` |
| V2 | `GET portfolios/{id}/payouts` | Income/dividends (historic, inception→today) |
| V2 | `GET portfolios/{id}/payouts` (today→+1y) | Announced/upcoming dividends → next-dividend sensors + dividend calendar |
| V2 | `GET portfolios/{id}/diversity` | Diversity breakdown |
| V2 | `GET portfolios/{id}/trades` | Trades |
| V2 | `GET portfolios/{id}/capital_gains` | AU only: realised CGT for current FY ("tax" device) |
| V2 | `GET portfolios/{id}/unrealised_cgt` | AU only: unrealised CGT as of today ("tax" device) |
| V3 | `GET portfolios/{id}/benchmark` | Benchmark performance + excess return ("benchmark" device; needs a benchmark configured) |
| V2 | `GET cash_accounts` | Cash accounts |
| V2 | `GET cash_accounts/{id}/cash_account_transactions` | Per-account cash transactions |
| V2 | `GET user_instruments` | Per-holding fundamentals (P/E, EPS, NTA, sector, industry, price freshness) + sector/industry allocation device |
| V2 | `GET my_user.json` | Account device: plan tier, member-since, subscription-problem binary sensor |
| V3 | `GET watchlist.json` | Watchlist overview device (count, up/down today, top mover/loser) — mobile-scoped, parks if unreachable |
| V3 | `GET markets` | Market-hours device: open/closed + next open/close per held market — internal-scoped, parks if unreachable |
| V3 | `GET exchange_rates` | Live FX rate sensors (per foreign currency held) — internal-scoped, multi-currency only, parks if unreachable |

> **Zero-cost derivations:** the per-holding dividend income / yield-on-cost /
> franking / last-dividend sensors and the per-holding trade activity / VWAP
> average buy price / brokerage / last-trade sensors are computed in-memory
> from the already-fetched `payouts` and `trades` lists — no extra requests.
> See [analytics.py](../custom_components/sharesight/analytics.py).

| V3 | `GET portfolios/{id}/portfolio_value_data.json` | One-shot at startup: backfills the inception→today daily value series into the Portfolio value sensor's long-term statistics (opt-out via options). Mobile-scoped — skips silently if unreachable. See [statistics_import.py](../custom_components/sharesight/statistics_import.py). |

**Not yet used but potentially useful** (read-only, low cost): V2
`GET my_user.json`, V2 `GET currencies.json`, V3 `GET .../value` (30-day
portfolio value series — lighter than a full performance report), V3
`GET .../totals` (inception-to-date totals), V3 `GET .../overview` (holdings +
cash, "performance minus calculations" — cheaper than `performance`), V3
`GET exchange_rates`, V3 `GET instruments/{id}/sharechecker` (per-instrument
fundamentals — one request per holding, so budget carefully).

---

## 4. V2 endpoints (complete — 52 endpoints)

Full path = `https://api.sharesight.com/api/v2` + path shown.
`:param` and `{param}` are path variables.

### Portfolios
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios.json` | List portfolios (full info if owner, else basic) |
| GET | `/portfolios/{id}.json` | Show one portfolio + settings |
| POST | `/portfolios.json` | Create portfolio |
| PUT | `/portfolios/{id}.json` | Update portfolio |
| DELETE | `/portfolios/{id}.json` | Delete portfolio |

### Reports (heavy — 3-concurrent limit)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/:portfolio_id/performance.json` | Performance report |
| GET | `/portfolios/:portfolio_id/valuation.json` | Valuation report |
| GET | `/portfolios/:portfolio_id/diversity.json` | Diversity report |
| GET | `/portfolios/:portfolio_id/capital_gains.json` | Capital gains (AU portfolios only) |
| GET | `/portfolios/:portfolio_id/unrealised_cgt.json` | Unrealised CGT (AU portfolios only) |

### Trades
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/:portfolio_id/trades.json` | List trades in portfolio |
| GET | `/trades/:id.json` | Show trade |
| POST | `/trades.json` | Create / Confirm / Reject trade |
| PUT | `/trades/:id.json` | Update trade |
| DELETE | `/trades/:id.json` | Delete trade |

### Holdings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/holdings/:holding_id/trades.json` | List a holding's trades |
| GET | `/holdings/:holding_id/rejected_trades.json` | List rejected trades |
| GET | `/holdings/:holding_id/payouts.json` | List a holding's payouts |

### Holding merges
| Method | Path | Description |
|--------|------|-------------|
| POST | `/portfolios/:portfolio_id/holding_merges.json` | Create holding merge |
| PUT | `/portfolios/:portfolio_id/holding_merges/:id.json` | Update holding merge |

### Payouts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/:portfolio_id/payouts.json` | List portfolio payouts |
| GET | `/payouts/:id.json` | Show payout |
| POST | `/payouts` / `/payouts.json` | Create / Confirm / Reject payout |
| PUT | `/payouts/:id.json` | Update payout |
| DELETE | `/payouts/:id.json` | Delete payout |

### Cash accounts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/cash_accounts.json` | List cash accounts |
| GET | `/cash_accounts/:id.json` | Show cash account |
| POST | `/portfolios/:portfolio_id/cash_accounts.json` | Create cash account |
| PUT | `/cash_accounts/:id.json` | Update cash account |
| DELETE | `/cash_accounts/:id.json` | Delete cash account |
| GET | `/cash_accounts/:cash_account_id/cash_account_transactions.json` | List transactions |
| POST | `/cash_accounts/:cash_account_id/cash_account_transactions.json` | Create transaction |
| PUT | `/cash_account_transactions/:id.json` | Update transaction |
| DELETE | `/cash_account_transactions/:id.json` | Delete transaction |

### Instruments & metadata
| Method | Path | Description |
|--------|------|-------------|
| GET | `/user_instruments.json` | List instruments across the user's portfolios |
| GET | `/instruments/:instrument_id/prices.json` | List instrument prices |
| GET | `/portfolios/:id/instrument_news.json` | List instrument news |
| GET | `/currencies.json` | Currency definitions |
| GET | `/groups.json` | List groups |

### Memberships / documents / user / SSO
| Method | Path | Description |
|--------|------|-------------|
| GET | `/memberships.json` | List memberships |
| POST | `/memberships.json` | Create membership |
| PUT | `/memberships/:id.json` | Update membership |
| DELETE | `/memberships/:id.json` | Delete membership |
| GET | `/documents/:id.json` | Show document |
| GET | `/my_user.json` | Current user info |
| GET | `/single_sign_on.json` | Request single sign-on (rate-limit exempt) |
| GET | `.1-mobile/identity/by_token.json` | Identify user by Google token (login) |
| GET | `.1-mobile/identity/signup_by_token.json` | Identify user by Google token (signup) |

---

## 5. V3 endpoints (complete — 103 endpoints)

Full path = `https://api.sharesight.com/api/v3` + path shown.

### Portfolios
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios` | List portfolios |
| GET | `/portfolios/{portfolio_id}` | Get portfolio |
| GET | `/portfolios/{portfolio_id}/holdings` | List portfolio holdings |
| GET | `/portfolios/{portfolio_id}/user_setting` | Show portfolio user setting |
| PATCH | `/portfolios/{portfolio_id}/user_setting` | Update portfolio user setting |

### Reports & values
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/{id}/performance` | Performance report (heavy) |
| GET | `/portfolios/{portfolio_id}/overview` | Overview: holdings + cash, "performance minus calculations" |
| GET | `/portfolios/{portfolio_id}/totals` | Inception-to-date total performance |
| GET | `/portfolios/{portfolio_id}/value` | Portfolio value, last 30 days → today |
| GET | `/portfolios/{portfolio_id}/benchmark.json` | Benchmark performance report |
| GET | `/portfolios/{portfolio_id}/performance_index_chart` | Index chart data |
| GET | `/portfolios/{portfolio_id}/reports` | List reports |
| GET | `/portfolios/{id}/portfolio_value_data.json` | Portfolio value data series |

### Holdings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/holdings` | List holdings |
| GET | `/holdings/{id}` | Get holding (opt: avg price, cost base, values over time) |
| PUT | `/holdings/{id}` | Update holding |
| DELETE | `/holdings/{id}` | Delete holding |
| GET | `/holdings/{holding_id}/trades.json` | List holding trades |
| GET | `/holdings/{id}/rejected_trades.json` | List rejected trades |
| POST | `/holdings/{id}/confirm_trades.json` | Confirm trade |
| POST | `/holdings/{id}/reject_trade.json` | Reject trade |
| GET | `/holdings/{id}/average_purchase_price.json` | Avg purchase price |
| GET | `/holdings/{id}/cost_base.json` | Cost base |
| GET | `/holdings/{id}/holding_value_data.json` | Holding value data series |
| GET | `/holdings/{holding_id}/valuation` | Show holding valuation |

### Trades
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/{portfolio_id}/trades.json` | List trades |
| GET | `/trades/{id}.json` | Show trade |
| POST | `/trades.json` | Create trade |
| PUT | `/trades/{id}.json` | Update trade |
| DELETE | `/trades/{id}.json` | Delete trade |

### Payouts
| Method | Path | Description |
|--------|------|-------------|
| GET | `/holdings/{holding_id}/payouts` | List payouts |
| POST | `/holdings/{holding_id}/payouts` | Create payout |
| GET | `/payouts/{id}` | Show payout (confirmed) |
| PUT | `/payouts/{id}` | Update payout |
| DELETE | `/payouts/{id}` | Delete payout (confirmed) |
| GET | `/holdings/{holding_id}/unconfirmed_payouts` | Show unconfirmed payout |
| PUT | `/holdings/{holding_id}/unconfirmed_payouts` | Update unconfirmed payout |
| DELETE | `/holdings/{holding_id}/unconfirmed_payouts` | Delete unconfirmed payout |

### Instruments, custom investments & prices
| Method | Path | Description |
|--------|------|-------------|
| GET | `/instruments` | Search instruments |
| GET | `/instruments/{id}/sharechecker` | Sharechecker data |
| GET | `/custom_investments` | List custom investments |
| POST | `/custom_investments` | Create custom investment |
| GET/PUT/DELETE | `/custom_investments/{id}` | Get / update / delete custom investment |
| GET/POST | `/custom_investment/{id}/prices.json` | List / create custom prices |
| POST/DELETE | `/custom_investments/{id}/bulk_prices` | Bulk create / delete prices |
| PUT/DELETE | `/prices/{id}.json` | Update / delete price |
| GET/POST | `/custom_investments/{instrument_id}/adjustments` | List / create adjustments |
| GET/PUT/DELETE | `/adjustments/{id}` | Show / update / delete adjustment |
| GET/POST | `/custom_investments/{instrument_id}/coupon_rates` | List / create coupon rates |
| PUT/DELETE | `/coupon_rates/{id}` | Update / delete coupon rate |

### Labels
| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolios/{id}/labels` | List labels |
| POST | `/portfolios/{id}/labels` | Create label |
| GET | `/portfolios/{id}/labels/{label}` | Get label by name |
| PUT | `/portfolios/{id}/labels/{label}` | Attach label to holding(s) |
| DELETE | `/portfolios/{id}/labels/{label}` | Delete label |
| DELETE | `/holdings/{id}/labels/{label}` | Detach label from holding |

### File imports
| Method | Path | Description |
|--------|------|-------------|
| POST | `/file_imports` | Create file import |
| GET/PUT | `/file_imports/{id}` | Get / update file import |
| POST | `/file_imports/{id}/commit` | Commit imported trades |
| GET/PUT | `/file_imports/{id}/column_mapping` | Get / update column mapping |
| GET/PUT/DELETE | `/file_imports/{id}/items` | Get / update / delete items |

### Connections (data sharing)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/connections` | List / create connections |
| GET/PATCH/DELETE | `/connections/{id}` | Show / update / delete connection |
| POST | `/connections/{id}/connection_consumers` | Create connection consumer |
| GET | `/portfolios/{portfolio_id}/connection_consumers` | List portfolio connection consumers |
| GET/PATCH/DELETE | `/connection_consumers/{id}` | Show / update / delete consumer |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/watchlist.json` | Get watchlist |
| POST | `/watchlist/add_instrument.json` | Add instrument to watchlist |
| DELETE | `/watchlist/remove_instrument.json` | Remove instrument from watchlist |
| GET | `/watchlist/instruments/{id}.json` | Get watchlist details for an instrument |
| GET | `/watched_portfolios.json` | Get watched portfolios |
| POST | `/watched_portfolios.json` | Add portfolio to watchlist |
| DELETE | `/watched_portfolios/{id}.json` | Remove portfolio from watchlist |

### Metadata
| Method | Path | Description |
|--------|------|-------------|
| GET | `/countries` | Country definitions |
| GET | `/currencies` | Currency definitions |
| GET | `/cryptocurrencies` | Cryptocurrency definitions |
| GET | `/markets` | Market definitions |
| GET | `/exchange_rates` | Exchange rates |

### Coupon codes / feedback / app / user
| Method | Path | Description |
|--------|------|-------------|
| GET/POST/DELETE | `/coupon_code` | Show / apply / delete coupon code |
| POST | `/feedback.json` | Give feedback |
| GET | `/mobile_app.json` | Get mobile app update |
| POST | `/oauth/revoke` | Remove API access (revoke token) |

---

## 6. Detailed parameters — key read endpoints

### V2 `GET /portfolios/:portfolio_id/performance.json`
Returns `portfolio_performance` with `value`, `capital_gain(_percent)`,
`payout_gain(_percent)`, `currency_gain(_percent)`, `total_gain(_percent)`,
`start_date`, `end_date`, plus grouped holdings/sub_totals.

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `start_date` | Date | inception | `YYYY-MM-DD` |
| `end_date` | Date | today | `YYYY-MM-DD` |
| `consolidated` | Bool | false | consolidated view |
| `include_sales` | Bool | false | include sold holdings |
| `grouping` | String | market | `markets` / `industry_classification` / `sector_classification` / `investment_type` / `countries` |
| `custom_group_id` | Int | — | group by custom group |

### V3 `GET /portfolios/{id}/performance`
Superset of V2. Extra params the integration relies on:

| Param | Type | Notes |
|-------|------|-------|
| `start_date` / `end_date` | String | `YYYY-MM-DD`, in `portfolio_tz_name` |
| `consolidated` | Bool | default false |
| `include_sales` | Bool | default false |
| `report_combined` | Bool | **also return totals combined by instrument** (integration passes `true`) |
| `include_limited` | Bool | **return identifying data for plan-limited holdings** (integration passes `true`) |
| `labels` | Array | filter by label name(s), repeated param |
| `grouping` | String | `country`, `currency`, `custom_group`, `industry_classification`, `investment_type`, `market`, `sector_classification`, ... |
| `custom_group_id` | Int | group by custom group |
| `benchmark_code` | String | e.g. `SPY.NYSE` |

### V2 `GET /portfolios/:portfolio_id/diversity.json`
| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `date` | Date | today | `YYYY-MM-DD` |
| `consolidated` | Bool | false | |
| `grouping` | String | industry_classification | same grouping vocab as performance |
| `custom_group_id` | Int | — | |

### V2 `GET /portfolios/:portfolio_id/valuation.json`
| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `balance_date` | Date | today | report as of date |
| `consolidated` | Bool | false | |
| `include_sales` | Bool | false | |
| `grouping` | String | market | |
| `custom_group_id` | Int | — | |

### V2 `GET /portfolios/:portfolio_id/payouts.json`
| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `start_date` | Date | inception | |
| `end_date` | Date | today | |
| `use_date` | String | paid_on | `paid_on` or `ex_date` |

### V2 `GET /portfolios/:portfolio_id/trades.json`
| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `start_date` | Date | inception | |
| `end_date` | Date | today | |
| `unique_identifier` | String | — | find one trade |

### V2 `GET /cash_accounts.json`
| Param | Type | Notes |
|-------|------|-------|
| `date` | Date | balance as of date |

### V2 `GET /cash_accounts/:cash_account_id/cash_account_transactions.json`
| Param | Type | Notes |
|-------|------|-------|
| `from` / `to` | Date | date range |
| `description` | String | filter |
| `foreign_identifier` | String | filter |

### V3 `GET /portfolios` / `GET /portfolios/{portfolio_id}`
| Param | Type | Notes |
|-------|------|-------|
| `consolidated` | Bool | default false |
| `instrument_id` | Int | (list only) forces `consolidated=false` |
| `subscription_status` | Bool | include subscription status of expired portfolios |

### V3 `GET /portfolios/{portfolio_id}/holdings`, `/user_setting`, `/totals`, `/value`, `/overview`
- `holdings`, `user_setting`, `totals`: accept `consolidated` (bool).
- `totals`: also `include_sales` (bool).
- `value`: `consolidated` (bool), `currency_code` (string).
- `overview`: `consolidated`, `start_date`, `grouping`, `custom_grouping_id`,
  `report_combined`, `include_sales`.

### V3 `GET /holdings/{id}`
| Param | Type | Notes |
|-------|------|-------|
| `average_purchase_price` | Bool | also return avg purchase price |
| `cost_base` | Bool | also return cost base |
| `values_over_time` | String | `true`, or a start-date, for value series since inception |

---

## 7. Notable behavioural gotchas

- **`performance`/`diversity`/`valuation` are the only endpoints under the
  3-concurrent cap** — everything else only counts against the 360/min budget.
- **Diversity/report payloads occasionally return empty/partial** (e.g. when a
  poll races a token refresh); the coordinator carries the previous breakdown
  forward to avoid sensor flap.
- **404 on a portfolio** = deleted or access lost → treat as reauth, not a
  transient error.
- **AU-only reports**: `capital_gains` and `unrealised_cgt` only work for
  Australian portfolios.
- **V3 is less stable**: some V3 endpoints are beta and "may change without
  notice" — pin behaviour with tests and prefer V2 where a stable equivalent
  exists (the integration mixes both deliberately).
- Grouping vocabulary differs slightly between V2 (`markets`,
  `industry_classification`, ...) and V3 (adds `country`, `currency`,
  `custom_group`).

---

## 8. Re-scraping the docs

```bash
curl -s -o v2.json https://api.sharesight.com/api/2/doc/api_data.json
curl -s -o v3.json https://api.sharesight.com/api/3/doc/api_data.json
# same data is mirrored at https://portfolio.sharesight.com/api/{2,3}/doc/api_data.json
```

Each entry has `type` (method), `url`, `title`, `name`, `group`, `version`,
`description`, `parameter.fields`, and `success.fields`. Dedupe by `name`
keeping the highest `version` (apiDoc keeps historical versions of each entry).
