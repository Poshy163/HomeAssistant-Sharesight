# Sharesight API Reference (V2 + V3)

> Working notes for the HomeAssistant-Sharesight integration.
> Source of truth: Sharesight's API landing page at
> <https://portfolio.sharesight.com/api/> and the apiDoc-generated references at
> <https://portfolio.sharesight.com/api/2/doc/index.html> and
> <https://portfolio.sharesight.com/api/3/doc/index.html>.
> Endpoint lists below were extracted from the live apiDoc data files
> (`/api/2/doc/api_data.json`, `/api/3/doc/api_data.json`) and last
> re-verified against a fresh scrape on 2026-08-31.
>
> **Counts, deduped by `(method, url)`:** 49 unique V2 endpoints (52 named
> operations — the tables fold Create/Confirm/Reject onto one row) and 105
> unique V3 endpoints. Earlier revisions of this file deduped by apiDoc `name`,
> which is *not* unique — `Connection_ConsumerList` and
> `Connection_ConsumerShow` each cover two different URLs — and so undercounted
> V3 by two. See §8.
> The one-row-per-method coverage decision for every route is in
> [endpoint-usage.md](endpoint-usage.md).
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
| Edge base (developer `account_type`) | `https://edge-api.sharesight.com/api/` |
| OAuth authorize | `https://api.sharesight.com/oauth2/authorize` |
| OAuth token | `https://api.sharesight.com/oauth2/token` |
| Edge OAuth token | `https://edge-api.sharesight.com/oauth2/token` |

- **Auth:** OAuth 2.0 (authorization-code grant). Access token passed as
  `Authorization: Bearer <token>`.
- **Token lifetime:** ~30 min (integration refreshes with a 300 s margin).
- **Transport:** HTTPS only, JSON request/response.
- **V2** holds the bulk of endpoints. **V3** is the newer surface; Sharesight
  recommends checking V3 first and falling back to V2. The complete V3 User
  API is a closed beta whose methods may change without notice, so pin its
  behaviour with tests and treat its response shapes as less stable than V2.
- **Official apiDoc surface tags matter more than the version number.** Every
  entry carries a `version` tag; suffixes predict whether an ordinary API token
  is likely to reach it, but they are not OAuth scopes or alternate prefixes:
  - `3.0.0` / `2.0.0` — public User API surface. Still subject to account API
    enablement, holding limits and ordinary transient failures.
  - `3.0.0-internal` — Sharesight's own web app (`/totals`, `/overview`,
    `/reports`, `/labels`, `/currencies`, `/markets`, `/exchange_rates`).
    Commonly unavailable to a standard token (`403`, or `406` when the
    route/version is unsupported or the account lacks capability).
  - `3.0.0-mobile` / `2.0.0-mobile` — the mobile app (`/watchlist.json`,
    `/value`, `portfolio_value_data.json`, `instrument_news.json`,
    `average_purchase_price.json`, `cost_base.json`). These can return `403`,
    `404` or `406` when the route/version is unsupported or the account lacks
    capability.
  Prefer a public-tier endpoint wherever one can answer the same question; the
  integration now does (see §3).
- **Canonical V3 prefix.** Public, internal-tagged and mobile-tagged routes all
  use `/api/v3/`. The `-internal` / `-mobile` suffixes annotate apiDoc entries;
  they are not URL version components. Availability remains token- and
  account-dependent.
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
- Tiered polling → the slow financial-year / year-to-date / one-month
  performance windows are only fetched every 12th poll
  (`SLOW_PERIOD_REFRESH_EVERY`, ≈ hourly at the default interval); the day/week
  windows and the combined V3 report still refresh every poll.
- 3-concurrent heavy cap → a shared `SHARESIGHT_HEAVY_CONCURRENCY = 3` gate
  around the documented `/performance`, `/diversity` and `/valuation` routes
  across every loaded portfolio entry using the consumer app. The integration
  conservatively puts `/benchmark.json` reports through the same gate as well.
- General burst cap → a separate shared gate for ordinary requests.
- 401 lockout → detected, then a 10-min consumer-app cooldown
  (`SHARESIGHT_LOCKOUT_COOLDOWN`) + `ConfigEntryAuthFailed`.
- 403 parallel/minute → 1-min consumer-app cooldown.
- Flaky optional endpoints → exponential backoff (1 h → 6 h max).
- A planned optional route that proves version-incompatible →
  capability-parked for the loaded entry. Routes already known to be rejected
  by the supplied token are omitted from the polling plan entirely.

---

## 3. Endpoints currently used by this integration

From [coordinator.py](../custom_components/sharesight/coordinator.py). Setup is
separate from the three polling tiers; see §2 for the cadence rationale.

### Setup — once before the first poll

| Ver | Endpoint | Purpose |
|-----|----------|---------|
| V3 → V2 | `GET portfolios/{id}` → `GET portfolios/{id}.json` | Currency, country, inception date, time zone and `financial_year_end`; V2 is tried only after an explicit 406 version rejection, and its bare response/date format is normalised |

### Required tier — every poll, failure degrades the poll

| Ver | Endpoint | Params the integration passes | Purpose |
|-----|----------|-------------------------------|---------|
| V3 → V2 | `GET portfolios` → `GET portfolios.json` | — | Financial-year rollover, matched on **this** portfolio's id; V2 only after an explicit 406 version rejection |
| V3 | `GET portfolios/{id}/performance` | `grouping=market`, `include_limited=true`, `report_combined=true` | The combined report: value, the gain family, holdings, market sub-totals, cash accounts |
| V3 → V2 | `GET portfolios/{id}/performance` → `GET portfolios/{id}/performance.json` | `start_date=end_date=today`, `grouping=market`, `include_sales=true` | Today's change; V2 is used only for a route/version mismatch |
| V3 → V2 | Same performance routes | Monday→today, same extras | Week to date; same fallback boundary |

### Slow tier — every 12th poll (≈ hourly), carried forward in between

| Ver | Endpoint | Params | Purpose |
|-----|----------|--------|---------|
| V3 → V2 | `GET portfolios/{id}/performance` | financial-year bounds | Financial-year device |
| V3 → V2 | `GET portfolios/{id}/performance` | trailing 30 days | Monthly device |
| V3 → V2 | `GET portfolios/{id}/performance` | 1 Jan → today | YTD device |
| V3 → V2 | `GET portfolios/{id}/performance` | inception → today, `include_sales=true` | **All-time including sold positions**, using public performance routes rather than internal totals |
| V3 mobile-tagged | `GET portfolios/{id}/portfolio_value_data.json` | `start_date` = 45 days ago | Capability-gated daily value series → trend, drawdown, volatility sensors |
| V3 → V2 | `GET portfolios/{id}/performance` | 3 m / 6 m / 1 y / 3 y / 5 y windows | **Opt-in** (`enable_extended_performance`) |

### Optional tier — each backs off independently, last payload carried forward

| Ver | Endpoint | Params | Purpose / notes |
|-----|----------|--------|-----------------|
| V2 | `GET portfolios/{id}/payouts.json` | — | Income history (inception→today) |
| V2 | `GET portfolios/{id}/payouts.json` | today→+1 y, `use_date=ex_date` | Announced dividends → next-dividend sensors, calendar, `dividend_announced` events; rejected rows are ignored |
| V2 | `GET portfolios/{id}/trades.json` | — | Trade analytics, per-holding VWAP/brokerage |
| V2 | `GET cash_accounts.json` | — | Cash accounts; also supplies the ids for the per-account transaction calls |
| V2 | `GET cash_accounts/{id}/cash_account_transactions.json` | — | Contributions / withdrawals |
| V3 | `GET portfolios/{id}/user_setting` | — | Report-settings diagnostics |
| V2 | `GET user_instruments.json` | — | P/E, EPS, NTA, price freshness |
| V3 internal-tagged | `GET portfolios/{id}/benchmark.json` | inception→today, `interest_method` matched to the portfolio | Capability-gated benchmark comparison, **plus the undocumented `maximum_drawdown` / `return_over_drawdown`** |
| V2 | `GET my_user.json` | — | Plan tier, subscription health |
| V3 mobile-tagged | `GET watchlist.json` | — | Watchlist device; capability-gated |
| V2 | `GET portfolios/{id}/capital_gains.json` | financial-year bounds | AU only |
| V2 | `GET portfolios/{id}/unrealised_cgt.json` | `balance_date=today` | AU only |

> **Local derivations from fetched payloads.** Market devices/allocation come
> from the combined performance report's market-grouped `sub_totals`; sector,
> industry, investment-type, currency and label allocation; per-holding income / yield-on-cost / franking / last
> dividend; per-holding trade activity / VWAP / brokerage; portfolio
> concentration, weighted yield and P/E, foreign-currency exposure from holding
> currencies, cash drag, stale prices;
> drawdown, high-water mark and volatility; the CGT harvestable-loss figures —
> all computed in memory from payloads already fetched. See
> [analytics.py](../custom_components/sharesight/analytics.py).

> **On-demand (service/button only), never polled.** Each tolerates an
> inaccessible route by
> returning an `{"error": …}` block:
> - V3 `GET holdings/{id}?average_purchase_price=true&cost_base=true` — the
>   `get_instrument_fundamentals` service. Public tier, and it returns strictly
>   more than the two mobile-tagged `average_purchase_price.json` /
>   `cost_base.json` calls it replaced (which remain as a fallback).
> - V3 mobile-tagged `GET instruments/{id}/sharechecker` — same service.
> - V2 `GET single_sign_on.json` — the `get_login_link` service. Rate-limit
>   exempt; the URL it returns is a live session and is **never logged**.
> - V3 → V2 `GET portfolios/{id}/performance` →
>   `GET portfolios/{id}/performance.json` — the
>   `generate_performance_report` service; V2 is tried only after an explicit
>   406 version rejection, and both response envelopes are normalised flat.
> - V3 mobile-tagged `GET portfolios/{id}/portfolio_value_data.json`
>   (inception→today) — the long-term statistics backfill.

### Deliberately not called

| Endpoint | Why not |
|----------|---------|
| V3 `GET portfolios/{id}/holdings` | The performance report's `holdings` array is a strict superset — the standalone endpoint returns no quantity, value or gains at all. It was a wasted request on every poll |
| V2 `GET portfolios/{id}/diversity` | Investigated, but redundant: market-grouped performance `sub_totals` already drive Top Market, while industry/sector allocation is derived from holding metadata. Removing it saves a calculation-heavy request and avoids mislabelling its default industry buckets as markets |
| V3 `GET portfolios/{id}/totals` | Internal-tagged and not reliable for ordinary OAuth applications. The integration uses the public `include_sales=true` performance window and does not call or consume this route |
| V3 internal `GET markets`, `GET exchange_rates` | The supplied standard token returned a permanent version rejection. Market devices/allocation already come from performance `sub_totals`, and foreign-currency exposure comes from holding currencies; no live FX-rate or market-hours entities are created |
| V2 mobile `GET portfolios/{id}/instrument_news.json` | The advertised mobile route was rejected by the supplied token, so it is not polled and the integration does not advertise a news sensor or event |
| V3 `GET portfolios/{id}/overview` | Internal-tagged. It uniquely exposes `sold_at_end`, but an ordinary OAuth application cannot rely on it |
| V3 `GET portfolios/{id}/reports`, `/labels` | Internal saved-report/label metadata; the integration consumes embedded holding labels and probes only optional routes whose data it uses |
| V3 `GET portfolios/{id}/performance_index_chart` | Public tier and genuinely useful (growth-of-10 000 vs a benchmark) — see §9 |
| V2 `GET instruments/{id}/prices.json`, `GET groups.json` | See §9 |

---

## 4. V2 endpoints (complete — 49 unique method+path, 52 named operations)

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
| GET | `/memberships.json` | List memberships (requires paid plan) |
| POST | `/memberships.json` | Create membership (requires paid plan) |
| PUT | `/memberships/:id.json` | Update membership (requires paid plan) |
| DELETE | `/memberships/:id.json` | Delete membership (requires paid plan) |
| GET | `/documents/:id.json` | Show document |
| GET | `/my_user.json` | Current user info |
| GET | `/single_sign_on.json` | Request single sign-on (rate-limit exempt) |
| GET | `.1-mobile/identity/by_token.json` | Identify user by Google token (login) |
| GET | `.1-mobile/identity/signup_by_token.json` | Identify user by Google token (signup) |

---

## 5. V3 endpoints (complete — 105 endpoints)

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
| GET | `/portfolios/{portfolio_id}/value` | Portfolio value **as at now** — a single balance, not a series (its only params are `consolidated`/`currency_code`). Unused by the integration; use `portfolio_value_data.json` for a series |
| GET | `/portfolios/{portfolio_id}/benchmark.json` | Benchmark performance report |
| GET | `/portfolios/{portfolio_id}/performance_index_chart` | Index chart data |
| GET | `/portfolios/{portfolio_id}/reports` | List reports |
| GET | `/portfolios/{id}/portfolio_value_data.json` | Portfolio value data series |

### Holdings
| Method | Path | Description |
|--------|------|-------------|
| GET | `/holdings` | List holdings |
| GET | `/holdings/{id}` | Get holding (opt: avg price, cost base, values over time) |
| PUT | `/holdings/{id}` | Update holding (DRP toggle only) |
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
| GET | `/connections/{id}/connection_consumers` | List a connection's consumers (`3.0.0-internal`; hidden by the old name-based dedupe) |
| GET | `/portfolio/{id}/connection_consumers.json` | Legacy singular-path alias of the plural route above. No HA value |

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
| POST | `/oauth/revoke` | Remove API access (revokes **all** access + refresh tokens for the user; `/oauth2/revoke` revokes a single token) |

---

## 6. Detailed parameters — key read endpoints

### V2 `GET /portfolios/:portfolio_id/performance.json`
Returns a **flat** object: `value`, `capital_gain(_percent)`,
`payout_gain(_percent)`, `currency_gain(_percent)`, `total_gain(_percent)`,
`start_date`, `end_date`, `include_sales`, `holdings[]`, `sub_totals[]`,
`cash_accounts[]`.

> The apiDoc's v2.1 entry for this endpoint describes a *sideloaded* schema
> (`portfolio_performance`, `portfolio_performance_holdings`,
> `portfolio_performance_sub_totals`, …). That shape is served under the
> `/api/v2.1` prefix only — `/api/v2` returns the flat one above. See §7.1.

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

### Pagination boundary

The read endpoints used by the coordinator do not expose page or cursor
parameters in Sharesight's current API definitions, so the integration does
not silently truncate a paginated portfolio feed. The API client has dedicated
typed helpers for the public custom-investment child routes below. They accept
the opaque `pagination.page` cursor returned by Sharesight (it is not a numeric
page) and remain outside Home Assistant's polling plan:

- V3 `GET custom_investments/{instrument_id}/adjustments`
- V3 `GET custom_investments/{instrument_id}/coupon_rates`
- V3 `GET custom_investment/{id}/prices.json`
- V3 internal `GET file_imports/{id}/items`

The internal file-import route is not exposed as a typed client helper. Any
future consumer must likewise iterate its pagination metadata rather than
assuming the first response is complete.

---

## 7. Notable behavioural gotchas

- **Sharesight documents `performance`/`diversity`/`valuation` as the routes
  under the 3-concurrent cap.** The integration also gates `/benchmark.json`
  conservatively; ordinary routes only count against the 360/min budget.
- **Combined performance `sub_totals` can be empty or partial** (e.g. when a
  poll races a token refresh); a missing or malformed result carries the
  previous derived market breakdown forward to avoid sensor flap, while a
  valid empty list is treated as authoritative.
- **404 on the configured portfolio** = deleted or access lost. OAuth can still
  be valid, so setup raises a configuration error directing the user to add a
  replacement portfolio rather than incorrectly starting reauthentication.
- **AU-only reports**: `capital_gains` and `unrealised_cgt` only work for
  Australian portfolios.
- **V3 is less stable**: the V3 User API is a closed beta whose methods may
  change without notice — pin behaviour with tests and prefer V2 where a
  stable equivalent exists (the integration mixes both deliberately).
- Grouping vocabulary differs slightly between V2 (`markets`,
  `industry_classification`, ...) and V3 (adds `country`, `currency`,
  `custom_group`).
- **`use_date` defaults are opposite between versions.** V2
  `portfolios/:id/payouts.json` defaults to `paid_on`; the V3 analogue
  `holdings/{id}/payouts` defaults to `ex_date`. The integration explicitly
  sends `use_date=ex_date` for the forward-looking window and excludes payouts
  whose status is `rejected`.
- **Portfolio dates are portfolio-local.** Request windows are derived in the
  portfolio's configured time zone and then sent as API dates; changing Home
  Assistant's host time zone must not shift financial-year or dividend
  boundaries.
- **`custom_group_id` is unreachable from V3 alone.** Both performance
  endpoints describe it as "an id returned from the CustomGroupsList
  endpoint", which does not exist in V3. The ids come from V2
  `GET groups.json`.

### 7.1 Documented vs. actually returned

Confirmed against a live portfolio's payloads. Every row here has cost real
debugging time, and three of them were the direct cause of permanently-unknown
sensors.

| Field | apiDoc says | Live API returns | Consequence |
|-------|-------------|------------------|-------------|
| `payouts[].tax_credit` | documented | **absent** on every payout | "Total Tax Credits" read it and sat at Unknown. The real field is `franking_credits` |
| `payouts[].capital_gains` | *not documented* (invented name) | **absent** | "Total Capital Gains Distributions" read it. The real fields are `discounted_capital_gains` + `non_discounted_capital_gains` |
| `benchmark.capital_gain_percentage` | documented | returns `capital_gain_percent` | "Benchmark Capital Gain %" read the documented name and was always Unknown |
| `benchmark.maximum_drawdown`, `.return_over_drawdown` | *not documented* | **present on every response** | Free risk metrics; now surfaced |
| `portfolios/{id}/totals` → `percentage_annulaised` | documented (transposed letters) | example and reality both use `percentage_annualised` | Retained as an API quirk; the integration does not consume the internal totals route |
| `markets[]` | documents `id`, `market_description`, `country_id`, `currency_id`, `allow_decimal_quantities` | returns only `code`, `tz_name`, `trading_start_time`, `trading_end_time`, plus an undocumented `source` | Not polled; market allocation uses performance `sub_totals` |
| `cash_account_transactions[].cash_account_transaction_type.name` | documented | can be **null** on broker-synced rows | Classifying only on the type dropped real deposits; fall back to the sign of `amount` |
| `payouts[].amount`, `.gross_amount` | in the **payout** currency | ditto — with `exchange_rate` alongside | Summing them raw across currencies produces a number in no currency at all |
| `trades[].value` | — | **negative for a SELL** | Aggregates must take the magnitude, and net flow must subtract explicitly |
| V2 performance response shape | v2.1 entries describe a sideloaded `portfolio_performance` + `portfolio_performance_holdings` schema | `/api/v2` returns a **flat** object (`value`, the gain family, `holdings`, `sub_totals`, `cash_accounts`) | The sideloaded schema is only served under the `/api/v2.1` prefix |

---

## 8. Re-scraping the docs

```bash
curl -s -o v2.json https://portfolio.sharesight.com/api/2/doc/api_data.json
curl -s -o v3.json https://portfolio.sharesight.com/api/3/doc/api_data.json
# Authenticated calls still use the execution base declared by api_project.json:
# https://api.sharesight.com/api/v2 and https://api.sharesight.com/api/v3
```

Each entry has `type` (method), `url`, `title`, `name`, `group`, `version`,
`description`, `parameter.fields`, and `success.fields`.

**Dedupe by `(type.upper(), url)`, not by `name`** — apiDoc `name` is not
unique per endpoint, and deduping by it silently drops real endpoints.

**Take the UNION of `success.fields` across every version of an entry**, rather
than keeping only the highest. The v2.1 entries describe a *sideloaded* schema
served under the `/api/v2.1` prefix, and they omit fields the v2.0 entries
document that the plain `/api/v2` endpoints really return (ten of the trade
fields, among others).

---

## 9. Unused endpoints worth revisiting

Ordered by value, annotated with the official surface tag that predicts whether
an ordinary token can reach them.

| Ver | Endpoint | Tier | What it would unlock |
|-----|----------|------|----------------------|
| V3 | `GET portfolios/{id}/performance_index_chart` | **public** | `dates` + `lines[]` where each line is `PORTFOLIO`, `BENCHMARK` or a group — a growth-of-10 000 series for an ApexCharts card, with per-market index lines included when `grouping=market`. Response caveat: the apiDoc field list wraps it in `performance_index_chart` while the example does not, and names the discriminator `type` rather than `line_type`. Parse defensively |
| V2 | `GET instruments/{id}/prices.json` | mobile | `high`, `low`, `volume`, `last_traded_on`, `last_traded_value` — the only source of 52-week high/low and distance-from-high. Its mobile tag and one-request-per-holding cost require capability detection and per-instrument backoff |
| V2 | `GET groups.json` | public | `groups[].id` + `.custom` — the ids that make `grouping=custom_group` usable on both performance endpoints, i.e. per-custom-group performance sensors |
| V3 | `GET holdings/{id}?values_over_time=<date>` | public | Per-holding value history, for a per-holding long-term-statistics backfill |
| V3 | `GET portfolios/{id}/performance?benchmark_code=X.Y` | public | Returns `report.benchmark` inline, which could fold per-period excess return (1 d / 1 w / 1 m / YTD / FY vs the benchmark) into the existing period reports |
| V3 | `GET watchlist.json?start_date=…` | mobile | Reframes `price.diff_*` from a one-day change to a period change |
| V3 | `GET portfolios/{id}/overview` | internal | `holdings[].sold_at_end` — Sharesight's own answer to "is this position closed?", which the integration currently infers from a dust-quantity heuristic |
| V3 | `GET portfolios/{id}/reports` | internal | `report_tiles[].show_full_report` — an authoritative entitlement probe, instead of learning by 403 |
| V2 | `GET currencies.json`, V3 `GET countries`, `/cryptocurrencies` | mixed | Metadata only; nothing the payloads do not already carry |
| V3 | `GET portfolios/{id}/value` | mobile | A single point-in-time balance. **Not** a series — `portfolio_value_data.json` is the series |
| V3 | custom-investment/price/adjustment/coupon-rate writes | public | Financial-record mutations; available generically but deliberately excluded from Home Assistant |
| V3 | connections, file imports, bulk custom prices and label writes | internal | Internal workflow/mutation surfaces; deliberately excluded |
