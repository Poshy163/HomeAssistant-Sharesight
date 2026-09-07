# API capability and reporting audit

This is a privacy-safe audit of the integration against Sharesight's official
[API introduction](https://portfolio.sharesight.com/api/),
[V2 reference](https://portfolio.sharesight.com/api/2/doc/index.html), and
[V3 reference](https://portfolio.sharesight.com/api/3/doc/index.html).
Sharesight advises checking V3 first, then V2 where no V3 equivalent exists.
The attached account snapshot was used only as a structural fixture; no account
identifier, person, holding, balance, transaction, or raw response is stored
here.

## Snapshot coverage

The captured envelope contains portfolio metadata and list, a combined report,
period reports (day, week, trailing 30 days, YTD, financial year, 3/6 months,
1/3/5 years, and all time), portfolio value history, paid and upcoming
payouts, trades, cash accounts and their transactions, instrument metadata,
user report settings, benchmark, watchlist, and realised/unrealised capital
gains.  Empty upcoming payouts are a valid response and are not treated as a
forecast.

The report payloads consistently identify their start/end dates, grouping,
currency, sale inclusion, and whether a percentage is annualised.  Consumers
must retain that context: a long-window annualised percentage cannot be
combined with a non-annualised daily return, and figures in a report currency
cannot be relabelled as an instrument currency.

## Reporting semantics and date checks

| Entity period | Request window | Meaning |
|---|---|---|
| Daily | today to today | Portfolio change for the portfolio-local report date |
| Weekly | Monday to today | Week to date; it legitimately equals Daily when captured on a Monday |
| 30-Day (legacy `one-month` key) | today minus 30 days to today | Rolling 30-day period, not calendar month to date |
| YTD | 1 January to today | Calendar year to date |
| Financial year | Portfolio `financial_year_end` boundary to today | Portfolio financial-year to date |
| Extended | Calendar months/years back, day-clamped, to today | Rolling calendar windows; an inception-clamped window is all time |

The integration derives `today` in the portfolio time zone and clamps fiscal
year boundaries safely for leap days and short months.  It sends the actual
current end date rather than a future Sunday or financial-year end.  This
avoids misleading server-side clamping and prevents cache keys from describing
a different request than was made.

The snapshot's daily and weekly reports have identical dates because the
capture date was a Monday.  That is expected week-to-date behaviour, not
evidence of cache reuse.  The snapshot also confirms a consistent
`grouping=market` and explicit annualisation flags. Period/all-time reports
include sold positions; the combined current-holdings report does not. These
controls remain part of the report's scope and explain differing totals.

## Endpoint matrix

| Area | Before this audit | Current decision |
|---|---|---|
| Portfolio and performance | Core combined and period reports | V3 preferred with V2 fallback only after an explicit version rejection; period reports retain dates, grouping, currency, sale inclusion, and annualisation context |
| Holdings and allocation | Combined report | Use the richer combined report rather than per-holding polling; represent current values as entities and keep detailed rows out of routine attributes |
| Trades, payouts, cash | Available through V2 aggregate endpoints | Optional independent polling with bounded carry-forward; no financial write operation is exposed |
| Value history | Mobile-tagged V3 route | Capability-gated, bounded recent series for trend analytics; full history is on demand |
| Benchmark and watchlist | Entitlement-dependent V3 routes | Optional and independently degradable; unavailable access never makes core portfolio data unavailable |
| Australian capital gains | V2 tax reports | Optional only for Australian portfolios; report fields are surfaced as Sharesight data, not independently calculated tax advice |
| Client library | Published `SharesightAPI==1.5.0` generic request interface | No client change required: every integration call is supported by the pinned published interface; tests import the installed package |

The detailed route-by-route inventory is maintained in
[endpoint-usage.md](endpoint-usage.md).  Sharesight's published usage limits
are 360 requests per consumer application per minute and three concurrent
calculation-heavy reports; the coordinator's shared gates and tiered cadence
are intentionally aligned with those limits.

## Comparability guard

V3 `performance` accepts `include_limited=true`, which returns identifying
data for plan-limited holdings rather than full calculations.  The combined
and all-time requests already set it; this audit also sets it on short and
extended period requests for parameter-equivalent comparisons. In particular, an
extended period clamped to portfolio inception may reuse the all-time response
only when its performance controls are identical.  V2 fallback intentionally
removes V3-only controls.

## Deliberate exclusions

Routes labelled `internal` or `mobile` are not assumed to be a stable public
contract.  Per-holding price/value history would also multiply requests by the
holding count.  Financial mutations, SSO URLs, account identity, raw reports,
and personal transaction data remain outside normal entity state and
diagnostics.
