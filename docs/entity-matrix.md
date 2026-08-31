# Entity coverage matrix

This matrix groups entities that share a data source, lifecycle and refresh
policy. The individual entity names within each family are listed in the
[main README](../README.md#sensors), while the exact v2/v3 request parameters
and deliberate exclusions are documented in the
[API reference](sharesight-api-reference.md#3-endpoints-currently-used-by-this-integration).

| Data source | API version / endpoint | Home Assistant platform | Entity or representation | Default enabled | Update frequency |
|---|---|---|---|---|---|
| Portfolio metadata | V3 `GET portfolios/{id}` | Sensor / device | Currency, country, inception, financial-year and portfolio metadata; portfolio hub device | Yes (low-signal metadata entities may be registry-disabled) | Setup, then portfolio list checked every poll |
| Combined portfolio report | V3 `GET portfolios/{id}/performance` | Sensor | Value, return, gain, allocation, cash and holding count overview | Yes | Every poll (default 5 minutes) |
| Today performance | V3 performance, V2 fallback | Sensor | Daily performance device and daily-close trigger/event context | Yes | Every poll |
| Week-to-date performance | V3 performance, V2 fallback | Sensor | Weekly performance device | Yes | Every poll |
| Financial year / month / YTD | V3 performance, V2 fallback | Sensor | Three period devices with value and gain families | Yes | Every 12th poll (about hourly) and financial-year rollover |
| 3/6 month and 1/3/5 year | V3 performance, V2 fallback | Sensor | Extended performance device | Option off | Every 12th poll when enabled |
| Inception including sold positions | V3 performance, V2 fallback | Sensor | All-time value/return sensors | Yes when source is available | Every 12th poll |
| Market subtotals | Combined performance payload | Sensor / device | Dynamic per-market value, cost, gains, return, weight and holding count | Yes | Every poll; dynamic discovery after refresh |
| Live holdings | Combined performance payload | Sensor / device | Dynamic per-holding value, quantity, price, cost, gain and return family | Option off for new entries; upgraded entries preserve their old setting | Every poll when enabled |
| Holding fundamentals | V2 `GET user_instruments.json` plus required holding rows | Sensor | Currency, P/E, EPS, NTA, sector, industry, type and price time | Follows holding option; niche fields may be registry-disabled | Optional slow tier; last good data carried forward up to 12 hours |
| Holding income | V2 portfolio payouts | Sensor | TTM income, yield on cost, franking and last dividend | Follows holding option | Optional slow tier |
| Holding trade analytics | V2 portfolio trades | Sensor | VWAP, brokerage, last trade, trade count and net shares | Follows holding option | Optional tier (trades refreshed frequently) |
| Cash accounts | V3 combined report and V2 `cash_accounts.json` | Sensor / device | Dynamic cash account balances and portfolio cash totals | Yes | Combined balance every poll; v2 metadata optional slow tier |
| Cash transactions | V2 cash-account transactions | Sensor / event | Contributions, withdrawals, counts, last transaction and activity events | Yes when cash endpoints are available | Frequent optional tier, independently cached per account |
| Income and distributions | V2 portfolio payouts | Sensor / calendar / event | Historical, trailing, announced and next-income entities; dividend calendar and announced/paid events | Yes when endpoint is available | Optional slow tier; upcoming rows refreshed independently |
| Capital gains tax | V2 capital gains and unrealised CGT | Sensor | Australian realised/unrealised CGT and loss-harvesting families | AU portfolios only | Optional slow tier |
| Benchmark | V3 internal-tagged `GET portfolios/{id}/benchmark.json` | Sensor | Benchmark performance, drawdown and excess return | Only after Sharesight returns a configured benchmark | Optional slow tier |
| Value history | V3 mobile-tagged `GET portfolios/{id}/portfolio_value_data.json` | Sensor / recorder statistics | 7/30-day change, drawdown, high-water mark, volatility and optional long-term backfill | Sensors yes; backfill option on | Recent series hourly; full history at setup/background or button press |
| Sector / industry / asset / currency / labels | Combined holdings plus instrument metadata | Sensor | Ranked allocation and concentration families; dynamic label entities | Yes when source data exists | Derived after each relevant source refresh |
| Watchlist | V3 mobile watchlist route | Sensor / device | Watchlist breadth and capped per-instrument price/change family | Only when the account can access the route | Optional slow tier |
| Account / subscription | V2 `GET my_user.json` | Sensor / binary sensor | Plan, subscription metadata and subscription-problem flag | Yes when endpoint is available | Optional slow tier |
| Portfolio analytics | Locally derived from fetched payloads | Sensor | Concentration, effective holdings, weighted yield/P/E, FX exposure, cash drag and stale prices | Yes; volatile/niche sensors may be registry-disabled | Recomputed after each coordinator refresh without extra API calls |
| Coordinator health | Coordinator and request gate | Sensor / binary sensor | Last success, interval, endpoint cooldown count, stale/degraded flags | Yes; diagnostic category | Every coordinator refresh |
| Activity stream | Diffs of trades, payouts, holdings and cash transactions | Event / device trigger | Bounded activity events and translated automation triggers | Yes | After each successful source refresh; first snapshot seeds silently |
| Manual refresh / history rebuild | Coordinator / V3 value data | Button | Refresh and idempotent statistics rebuild | Yes | User initiated |

No writable Sharesight portfolio, holding, trade, payout, cash, label or
watchlist control is exposed as a Home Assistant entity. Those actions can
change financial records and are not appropriate for a polling integration.
`select`, `number` and `switch` platforms are therefore deliberately absent.
