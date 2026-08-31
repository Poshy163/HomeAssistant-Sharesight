# Sharesight endpoint usage inventory

Verified from Sharesight's [official API landing page](https://portfolio.sharesight.com/api/)
against the live [V2 apiDoc JSON](https://portfolio.sharesight.com/api/2/doc/api_data.json)
and [V3 apiDoc JSON](https://portfolio.sharesight.com/api/3/doc/api_data.json) on
**2026-08-31**:

- V2: **49** unique method + path endpoints, SHA-256
  `6430942759dfee9e737935a952518d0ceef87e410e8d5932904d7175890bb289`
- V3: **105** unique method + path endpoints, SHA-256
  `11dc95586038736ab090e15580fbb42bca88dbc9240fcd7ffbb51c5aac1a13b3`

The `Official apiDoc tag` column reproduces Sharesight's `version` metadata.
The `-internal` and `-mobile` suffixes are surface annotations, not alternate
URL prefixes or formal OAuth scopes. “Typed client” means
`Sharesight-API` has a dedicated checked helper. “Excluded” means Home
Assistant intentionally does not call the route; it was not overlooked.
Write operations are never called by Home Assistant or by live validation.
apiDoc labels the two V2 show operations as `SHOW`; the tables render their
actual HTTP method, `GET`.

## V2 — 49 unique method + path endpoints

| Method | Official path | Official apiDoc tag | Current use / decision |
|---|---|---|---|
| GET | `.1-mobile/identity/by_token.json` | 2.0.0<br>2.1.0 | Excluded — mobile identity login, not portfolio data |
| GET | `.1-mobile/identity/signup_by_token.json` | 2.0.0<br>2.1.0 | Excluded — mobile identity signup, not portfolio data |
| DELETE | `/cash_account_transactions/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| PUT | `/cash_account_transactions/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/cash_accounts.json` | 2.0.0 | HA optional poll + typed client |
| GET | `/cash_accounts/:cash_account_id/cash_account_transactions.json` | 2.0.0 | HA optional per-account poll + typed client |
| POST | `/cash_accounts/:cash_account_id/cash_account_transactions.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| DELETE | `/cash_accounts/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/cash_accounts/:id.json` | 2.0.0 | Typed client; HA list route already supplies required account data |
| PUT | `/cash_accounts/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/currencies.json` | 2.0.0<br>2.1.0 | Typed client; HA payloads already identify their currencies |
| GET | `/documents/:id.json` | 2.0.0 | Excluded — document metadata has no useful HA entity semantics |
| GET | `/groups.json` | 2.0.0 | Typed client; future custom-group configuration candidate |
| GET | `/holdings/:holding_id/payouts.json` | 2.0.0 | Typed client; HA uses one portfolio aggregate instead of N holding calls |
| GET | `/holdings/:holding_id/rejected_trades.json` | 2.0.0<br>2.1.0 | Generic read only; rejected workflow data is not useful HA state |
| GET | `/holdings/:holding_id/trades.json` | 2.0.0<br>2.1.0 | Typed client; HA uses one portfolio aggregate instead of N holding calls |
| GET | `/instruments/:instrument_id/prices.json` | 2.0.0-mobile<br>2.1.0-mobile | Excluded for now — mobile-tagged and one request per holding |
| GET | `/memberships.json` | 2.0.0 | Excluded — account-sharing administration |
| POST | `/memberships.json` | 2.0.0 | Excluded — account-sharing mutation; generic client only |
| DELETE | `/memberships/:id.json` | 2.0.0 | Excluded — account-sharing mutation; generic client only |
| PUT | `/memberships/:id.json` | 2.0.0 | Excluded — account-sharing mutation; generic client only |
| GET | `/my_user.json` | 2.0.0<br>2.1.0 | HA optional poll + typed client |
| POST | `/payouts` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| POST | `/payouts.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| DELETE | `/payouts/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/payouts/:id.json` | 2.0.0 | Generic read only; portfolio payout list already supplies HA data |
| PUT | `/payouts/:id.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/portfolios.json` | 2.0.0 | HA required fallback + typed client; used only after explicit V3 406 version rejection |
| POST | `/portfolios.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/portfolios/:id/instrument_news.json` | 2.0.0-mobile | Excluded — mobile-tagged and rejected by the tested token |
| GET | `/portfolios/:portfolio_id/capital_gains.json` | 2.0.0<br>2.1.0 | HA optional AU-only poll + typed client |
| POST | `/portfolios/:portfolio_id/cash_accounts.json` | 2.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/portfolios/:portfolio_id/diversity.json` | 2.0.0<br>2.1.0 | Typed client; HA derives allocation from richer combined performance data |
| POST | `/portfolios/:portfolio_id/holding_merges.json` | 2.0.0<br>2.1.0 | Excluded — destructive holding workflow; generic client only |
| PUT | `/portfolios/:portfolio_id/holding_merges/:id.json` | 2.0.0<br>2.1.0 | Excluded — destructive holding workflow; generic client only |
| GET | `/portfolios/:portfolio_id/payouts.json` | 2.0.0 | HA optional poll + typed client |
| GET | `/portfolios/:portfolio_id/performance.json` | 2.0.0<br>2.1.0 | HA windowed polling and on-demand performance fallback + typed client; used only after explicit V3 406 |
| GET | `/portfolios/:portfolio_id/trades.json` | 2.0.0<br>2.1.0 | HA optional poll + typed client; preferred over internal V3 equivalent |
| GET | `/portfolios/:portfolio_id/unrealised_cgt.json` | 2.0.0<br>2.1.0 | HA optional AU-only poll + typed client |
| GET | `/portfolios/:portfolio_id/valuation.json` | 2.0.0<br>2.1.0 | Typed client; HA combined performance already supplies required valuation data |
| DELETE | `/portfolios/{id}.json` | 2.0.0 | Excluded — destructive portfolio mutation; generic client only |
| GET | `/portfolios/{id}.json` | 2.0.0<br>2.1.0 | HA setup fallback + typed client; bare V2 shape/date normalised |
| PUT | `/portfolios/{id}.json` | 2.0.0 | Excluded — portfolio mutation; generic client only |
| GET | `/single_sign_on.json` | 2.0.0 | HA on-demand login-link service; returned secret URL is never logged |
| POST | `/trades.json` | 2.0.0<br>2.1.0 | Isolated typed mutation helper; mocks/developer sandbox only, never HA |
| DELETE | `/trades/:id.json` | 2.0.0<br>2.1.0 | Excluded — financial-record mutation; generic client only |
| GET | `/trades/:id.json` | 2.0.0<br>2.1.0 | Generic read only; portfolio trade list already supplies HA data |
| PUT | `/trades/:id.json` | 2.0.0<br>2.1.0 | Excluded — financial-record mutation; generic client only |
| GET | `/user_instruments.json` | 2.0.0<br>2.1.0 | HA optional poll + typed client |

## V3 — 105 unique method + path endpoints

| Method | Official path | Official apiDoc tag | Current use / decision |
|---|---|---|---|
| DELETE | `/adjustments/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/adjustments/{id}` | 3.0.0 | Typed client; HA excludes custom-investment management data |
| PUT | `/adjustments/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| DELETE | `/connection_consumers/{id}` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| GET | `/connection_consumers/{id}` | 3.0.0-internal | Excluded — internal Sharesight application surface |
| PATCH | `/connection_consumers/{id}` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| GET | `/connections` | 3.0.0-internal | Excluded — internal Sharesight application surface |
| POST | `/connections` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| DELETE | `/connections/{id}` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| GET | `/connections/{id}` | 3.0.0-internal | Excluded — internal Sharesight application surface |
| PATCH | `/connections/{id}` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| GET | `/connections/{id}/connection_consumers` | 3.0.0-internal | Excluded — internal Sharesight application surface |
| POST | `/connections/{id}/connection_consumers` | 3.0.0-internal | Excluded — internal connection workflow/mutation |
| GET | `/countries` | 3.0.0 | Typed client; HA portfolio data already carries country code |
| DELETE | `/coupon_code` | 3.0.0 | Excluded — subscription billing mutation |
| GET | `/coupon_code` | 3.0.0 | Excluded — subscription billing workflow, not portfolio state |
| POST | `/coupon_code` | 3.0.0 | Excluded — subscription billing mutation |
| DELETE | `/coupon_rates/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| PUT | `/coupon_rates/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/cryptocurrencies` | 3.0.0-internal | Excluded — internal metadata; payloads identify held instruments |
| GET | `/currencies` | 3.0.0-internal | Excluded — public V2 currency definitions are typed |
| GET | `/custom_investment/{id}/prices.json` | 3.0.0 | Typed client with opaque-cursor support; HA excludes management history |
| POST | `/custom_investment/{id}/prices.json` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/custom_investments` | 3.0.0 | Typed client; HA excludes custom-investment management data |
| POST | `/custom_investments` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| DELETE | `/custom_investments/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/custom_investments/{id}` | 3.0.0 | Typed client; HA excludes custom-investment management data |
| PUT | `/custom_investments/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| DELETE | `/custom_investments/{id}/bulk_prices` | 3.0.0-internal | Excluded — internal bulk financial-record mutation |
| POST | `/custom_investments/{id}/bulk_prices` | 3.0.0-internal | Excluded — internal bulk financial-record mutation |
| GET | `/custom_investments/{instrument_id}/adjustments` | 3.0.0 | Typed client with opaque-cursor support; HA excludes management history |
| POST | `/custom_investments/{instrument_id}/adjustments` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/custom_investments/{instrument_id}/coupon_rates` | 3.0.0 | Typed client with opaque-cursor support; HA excludes management history |
| POST | `/custom_investments/{instrument_id}/coupon_rates` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/exchange_rates` | 3.0.0-internal | Excluded — internal; holding currencies drive HA FX exposure |
| POST | `/feedback.json` | 3.0.0-mobile | Excluded — mobile feedback workflow |
| POST | `/file_imports` | 3.0.0-internal | Excluded — internal import workflow/mutation |
| GET | `/file_imports/{id}` | 3.0.0-internal | Excluded — internal import workflow |
| PUT | `/file_imports/{id}` | 3.0.0-internal | Excluded — internal import workflow/mutation |
| GET | `/file_imports/{id}/column_mapping` | 3.0.0-internal | Excluded — internal import workflow |
| PUT | `/file_imports/{id}/column_mapping` | 3.0.0-internal | Excluded — internal import workflow/mutation |
| POST | `/file_imports/{id}/commit` | 3.0.0-internal | Excluded — internal import commit mutation |
| DELETE | `/file_imports/{id}/items` | 3.0.0-internal | Excluded — internal import workflow/mutation |
| GET | `/file_imports/{id}/items` | 3.0.0-internal | Excluded — internal import workflow |
| PUT | `/file_imports/{id}/items` | 3.0.0-internal | Excluded — internal import workflow/mutation |
| GET | `/holdings` | 3.0.0 | Typed client; HA does not poll because combined performance supplies richer portfolio-scoped holdings |
| GET | `/holdings/{holding_id}/payouts` | 3.0.0-internal | Excluded — public V2 holding payouts are typed |
| POST | `/holdings/{holding_id}/payouts` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/holdings/{holding_id}/trades.json` | 3.0.0-internal | Excluded — public V2 holding trades are typed |
| DELETE | `/holdings/{holding_id}/unconfirmed_payouts` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/holdings/{holding_id}/unconfirmed_payouts` | 3.0.0-internal | Excluded — internal payout workflow |
| PUT | `/holdings/{holding_id}/unconfirmed_payouts` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/holdings/{holding_id}/valuation` | 3.0.0-internal | Excluded — internal; public portfolio reports supply required values |
| DELETE | `/holdings/{id}` | 3.0.0 | Excluded — destructive holding mutation |
| GET | `/holdings/{id}` | 3.0.0 | HA on-demand cost basis + typed client; sharechecker supplies instrument fundamentals |
| PUT | `/holdings/{id}` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| GET | `/holdings/{id}/average_purchase_price.json` | 3.0.0-mobile | HA on-demand fallback when the public holding route is version-unavailable or omits the requested cost fields |
| POST | `/holdings/{id}/confirm_trades.json` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/holdings/{id}/cost_base.json` | 3.0.0-mobile | HA on-demand fallback when the public holding route is version-unavailable or omits the requested cost fields |
| GET | `/holdings/{id}/holding_value_data.json` | 3.0.0-mobile | Excluded — public holding GET with `values_over_time` is preferred; per-holding polling scales poorly |
| DELETE | `/holdings/{id}/labels/{label}` | 3.0.0-internal | Excluded — internal label mutation |
| POST | `/holdings/{id}/reject_trade.json` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/holdings/{id}/rejected_trades.json` | 3.0.0-internal | Excluded — internal rejected-trade workflow |
| GET | `/instruments` | 3.0.0-mobile | Generic mobile search; no polling value |
| GET | `/instruments/{id}/sharechecker` | 3.0.0-mobile | HA on-demand, capability-gated mobile detail |
| GET | `/markets` | 3.0.0-internal | Excluded — performance market subtotals supply HA allocation |
| GET | `/mobile_app.json` | 3.0.0-mobile | Excluded — Sharesight mobile-app update metadata |
| POST | `/oauth/revoke` | 3.0.0 | Excluded — destructive credential revocation |
| DELETE | `/payouts/{id}` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/payouts/{id}` | 3.0.0-internal | Excluded — public V2 payout aggregate supplies HA data |
| PUT | `/payouts/{id}` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/portfolio/{id}/connection_consumers.json` | 3.0.0-internal | Excluded — internal connection workflow |
| GET | `/portfolios` | 3.0.0 | HA required poll + typed client; preferred over public V2 fallback |
| GET | `/portfolios/{id}/labels` | 3.0.0-internal | Excluded — embedded holding labels supply HA allocation |
| POST | `/portfolios/{id}/labels` | 3.0.0-internal | Excluded — internal label mutation |
| DELETE | `/portfolios/{id}/labels/{label}` | 3.0.0-internal | Excluded — internal label mutation |
| GET | `/portfolios/{id}/labels/{label}` | 3.0.0-internal | Excluded — internal label workflow |
| PUT | `/portfolios/{id}/labels/{label}` | 3.0.0-internal | Excluded — internal label mutation |
| GET | `/portfolios/{id}/performance` | 3.0.0 | HA required/slow polls + on-demand reports + typed client |
| GET | `/portfolios/{id}/portfolio_value_data.json` | 3.0.0-mobile | HA optional poll + on-demand backfill + typed client; capability-gated |
| GET | `/portfolios/{portfolio_id}` | 3.0.0 | HA setup + typed client; preferred over public V2 fallback |
| GET | `/portfolios/{portfolio_id}/benchmark.json` | 3.0.0-internal | HA optional poll + typed client; internal-tagged and capability-gated |
| GET | `/portfolios/{portfolio_id}/connection_consumers` | 3.0.0-internal | Excluded — internal connection workflow |
| GET | `/portfolios/{portfolio_id}/holdings` | 3.0.0 | Typed client; HA combined performance supplies richer holding rows |
| GET | `/portfolios/{portfolio_id}/overview` | 3.0.0-internal | Excluded — uniquely has `sold_at_end`, but ordinary OAuth apps cannot rely on it |
| GET | `/portfolios/{portfolio_id}/performance_index_chart` | 3.0.0 | Typed client; useful HA on-demand candidate, not currently called |
| GET | `/portfolios/{portfolio_id}/reports` | 3.0.0-internal | Excluded — internal saved-report metadata; HA probes only data routes it consumes |
| GET | `/portfolios/{portfolio_id}/totals` | 3.0.0-internal | Excluded — public performance with `include_sales` supplies all-time totals |
| GET | `/portfolios/{portfolio_id}/trades.json` | 3.0.0-internal | Excluded — public V2 portfolio trade list is used |
| GET | `/portfolios/{portfolio_id}/user_setting` | 3.0.0 | HA optional poll + typed client |
| PATCH | `/portfolios/{portfolio_id}/user_setting` | 3.0.0 | Excluded — user-setting mutation; generic client only |
| GET | `/portfolios/{portfolio_id}/value` | 3.0.0-mobile | Excluded — single balance duplicates combined performance value |
| DELETE | `/prices/{id}.json` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| PUT | `/prices/{id}.json` | 3.0.0 | Excluded — financial-record mutation; generic client only |
| POST | `/trades.json` | 3.0.0-internal | Excluded — internal financial-record mutation; public V2 helper exists |
| DELETE | `/trades/{id}.json` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/trades/{id}.json` | 3.0.0-internal | Excluded — public V2 portfolio aggregate supplies HA data |
| PUT | `/trades/{id}.json` | 3.0.0-internal | Excluded — internal financial-record mutation |
| GET | `/watched_portfolios.json` | 3.0.0-mobile | Excluded — mobile portfolio-watch workflow |
| POST | `/watched_portfolios.json` | 3.0.0-mobile | Excluded — mobile watch mutation |
| DELETE | `/watched_portfolios/{id}.json` | 3.0.0-mobile | Excluded — mobile watch mutation |
| GET | `/watchlist.json` | 3.0.0-mobile | HA optional poll; capability-gated |
| POST | `/watchlist/add_instrument.json` | 3.0.0-mobile | Excluded — mobile watchlist mutation |
| GET | `/watchlist/instruments/{id}.json` | 3.0.0-mobile | Generic mobile detail; list route already supplies HA data |
| DELETE | `/watchlist/remove_instrument.json` | 3.0.0-mobile | Excluded — mobile watchlist mutation |
