# Home Assistant Sharesight Integration

![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)

Monitor your [Sharesight](https://www.sharesight.com/) investment portfolio directly from Home Assistant. Track portfolio value, daily/weekly/financial-year performance, per-market breakdowns, holdings, dividends, trades, contributions, and more.

**Key features:**
- OAuth2 authentication — no API keys stored in YAML
- Automatic portfolio discovery — select your portfolio from a dropdown during setup
- Per-market devices — each exchange (ASX, NYSE, LSE, etc.) gets its own HA device
- Cash account tracking — including Xero-linked accounts
- Auto-discovery of new markets and cash accounts — picked up automatically on the next poll cycle (every 5 minutes by default)
- Dividend calendar — received and announced dividends as a native HA calendar entity
- Capital gains tax (CGT) sensors for Australian portfolios — realised FY + unrealised positions
- Benchmark comparison — track your excess return against the benchmark configured in Sharesight
- Per-holding fundamentals (P/E, EPS, NTA, sector, industry), dividend income, yield-on-cost and average buy price
- Sector & industry allocation breakdown — a diversification lens beyond markets
- Account device — plan tier, member-since, and a subscription-lapse alert (binary sensor)
- Watchlist (overview plus per-instrument live price and day-change), live FX rates and market trading-hours sensors (where your Sharesight API access exposes them)
- Long-term statistics backfill — imports your full portfolio value history so HA charts show years, not just days since install (opt-out in options)
- Response services — pull a portfolio summary, holdings or income into scripts/templates, generate an on-demand performance report, fetch per-instrument fundamentals, or mint a one-minute single-sign-on login link
- Activity events & device triggers — automate on new trades, dividends, holdings opened/closed, cash transactions, published instrument news and the daily-close rollover
- Portfolio analytics — concentration (HHI), effective number of holdings, weighted yield/PE, foreign-currency exposure, cash drag and stale-price count
- All-time totals & forward income — lifetime value/return including sold positions, plus projected forward dividend income and yield
- Value-trend, Latest News & label sensors — 7-day / 30-day portfolio value change (with a sparkline series), a latest-headline sensor, plus value and portfolio share per Sharesight label (where your API access and labels expose them)
- Supports both standard and Edge (developer) API accounts
- Multiple portfolio support — add the integration once per portfolio

---

## Upgrading to 2.0

**Version 2.0 modernises how devices and entities are presented in Home Assistant. Your data, history and automations are preserved — only display names and device layout change.**

- **Entity IDs are unchanged.** Every `sensor.…`, `binary_sensor.…`, `button.…`, `event.…` and `calendar.…` entity keeps the exact ID it had on 1.9.x, so dashboards, automations, templates and long-term statistics keep working with no edits. Only the human-readable *friendly names* change.
- **Names now derive from device + entity.** Entities use Home Assistant's modern naming: the displayed name is the **device name plus the entity name** (e.g. the *Sharesight Portfolio 123* device's value sensor now reads as "Sharesight Portfolio 123 Portfolio value"). This makes names consistent and translatable, and is why friendly names look different after upgrading even though the entity IDs did not move.
- **Nested device tree.** The per-category devices (Daily Performance, Holdings, Income, each market, each holding, Watchlist, Analytics, …) now nest **under the portfolio hub device** via Home Assistant's *via_device* link. Open the portfolio device and you'll see the whole fleet as a tree instead of ~25 flat, separately-listed devices.
- **Diagnostics categorisation.** Low-signal metadata sensors (portfolio ID, update interval, access level, price-updated timestamps, "return is annualised", …) are now tagged as **Diagnostic**. They move into the *Diagnostic* section of their device and out of the main sensor list — they remain fully available and recordable.
- **A few niche entities ship disabled by default.** To cut first-run clutter, a handful of rarely-needed entities are now **disabled by default**. Nothing you already rely on is switched off — this only affects new/niche entities. To enable one: **Settings → Devices & Services → Sharesight → the device → the entity → the gear (Settings) icon → toggle _Enabled_ → Update** (or use the device page's "_+N entities not shown_" link). The entity keeps its stable ID once enabled.

No reconfiguration is required: update, restart Home Assistant, and the devices re-parent and rename themselves on the next load. Because this changes displayed names and device organisation, it ships as a **major** version bump (2.0.0).

### Deleting stale devices

When you sell out of a holding, exit a market, or close a cash account, its per-item device is no longer refreshed. 2.0 lets you **delete such a device from the UI** (its three-dot menu → **Delete**) once it no longer appears in your current portfolio data. The portfolio hub and the fixed report/container devices can't be deleted this way, and a device that is still live (or that can't be confirmed stale because the integration is mid-refresh) is kept — so you can't accidentally remove an active device.

Entities of a per-item device go **unavailable** as soon as their item leaves your portfolio — a holding sold, a market exited, a cash account closed, an instrument dropped from your watchlist, a label removed from the last holding carrying it. That is the cue that the device is ready to delete; previously they sat on *Unknown* indefinitely.

Prefer not to do it by hand? Turn on **Automatically delete devices for sold holdings** in the integration's **Options**. Each holding / market / cash-account device is then deleted on its own once its item has been missing from **three consecutive successful updates** — long enough to ride out a short Sharesight payload, and it never acts on an empty list, so a bad response can't wipe out live devices. The load that follows saving the option counts as the first, so expect the device to disappear about **two poll cycles (≈10 minutes)** after you enable it, not instantly. Restarting Home Assistant in the meantime restarts the count. It is off by default because deleting a device also deletes its entities and their recorded history; the portfolio hub and the fixed report/container devices are never touched. Buy back in later and the holding's device and entities come back on the next poll.

Sharesight sometimes leaves a sold holding in its performance report when the sale doesn't net to exactly zero (a residue such as `-0.00004` shares, worth nothing, flagged as an invalid position). The integration treats that as sold rather than as a live $0 holding, so the device is deletable and the ghost can't skew the holding count, smallest-holding or label sensors.

---

## Prerequisites

Before installing, you need to create an API application on Sharesight. **Note:** Sharesight API access is only available on paid plans (Standard, Premium or Business) and may need to be enabled for your account — if you can't create an API application or authorization fails, email `api@sharesight.com` to request access.

Once API access is enabled, create the application:

1. Log in to your Sharesight account at [portfolio.sharesight.com](https://portfolio.sharesight.com)
2. Navigate to **[API Settings](https://portfolio.sharesight.com/users/api_token)** (also accessible via your profile menu)
3. Click **Create New Application**
4. Fill in the details:
   - **Application Name:** `Home Assistant` (or anything you like)
   - **Redirect URI:** Your Home Assistant OAuth redirect URL. This is typically:
     ```
     https://my.home-assistant.io/redirect/oauth
     ```
     > If you access HA via a custom domain, use `https://YOUR_HA_DOMAIN:PORT/auth/external/callback` instead.
5. Click **Save** and note down your **Client ID** and **Client Secret** — you'll need these during setup.

---

## Installation

### Option A: HACS (Recommended)

1. Open [HACS](https://hacs.xyz/docs/setup/download) in Home Assistant
2. Go to **Integrations** → click the **⋮** menu (top right) → **Custom Repositories**
3. Add this repository URL with category **Integration**:
   ```
   https://github.com/Poshy163/HomeAssistant-Sharesight
   ```
4. Go to **HACS** → **Integrations** → **Explore & Download Repositories** → search for **Sharesight** → **Download**
5. **Restart Home Assistant**

### Option B: Manual

1. Download or clone this repository
2. Copy the `custom_components/sharesight` folder into your Home Assistant `custom_components/` directory
3. **Restart Home Assistant**

---

## Setup

After installation and restart:

1. In Home Assistant, go to **Settings** → **Devices & Services** → **Application Credentials**
2. Click **Add Application Credentials** and select **Sharesight**
3. Enter the **Client ID** and **Client Secret** from the [Prerequisites](#prerequisites) step
4. Go to **Settings** → **Devices & Services** → click **+ Add Integration** → search for **Sharesight**
5. You'll be redirected to Sharesight to **authorize** the connection — log in and click **Allow**
6. After returning to Home Assistant, **select your portfolio** from the dropdown list
7. Optionally enable **Use Edge API** if you have a Sharesight developer account
8. Click **Submit** — the integration will create devices and sensors for your portfolio

> **Adding multiple portfolios:** Repeat steps 4–8 for each portfolio you want to monitor. Each portfolio gets its own set of devices.

---

## Sensors

All sensors are organized into separate HA devices by category. Data refreshes every **5 minutes** by default; you can change the poll interval (60–3600 seconds) in the integration's **Options**, along with the long-term-statistics backfill and the automatic removal of [devices for sold holdings](#deleting-stale-devices).

> **Tiered polling:** the headline value plus the day and week windows refresh on **every** poll, but the slower financial-year, year-to-date and one-month performance windows only re-fetch roughly **hourly** (every 12th poll) — so those period sensors update less often by design, keeping well inside the API rate limit. See [Polling, performance & the recorder](#polling-performance--the-recorder).

### Portfolio
| Sensor | Description |
|--------|-------------|
| Portfolio Value | Current total portfolio value |
| Capital Gain / Percent | Total capital gain and percentage |
| Total Gain / Percent | Total return including dividends |
| Currency Gain / Percent | Gain/loss from currency movements |
| Dividend Gain / Percent | Total dividend income gain |
| Cost Basis | Total amount invested |
| Unrealised Gain / Percent | Paper profit/loss on open positions |
| Annualised Return Percent | Annualised total return |
| Portfolio Start Value | Portfolio value at inception |
| Value Change 7d / 30d | Portfolio value change over the last 7 / 30 days; the 30-day sensor carries the daily value series as a `series` attribute for ApexCharts / sparkline cards |
| Latest News | Most recent instrument-news headline; up to 25 recent articles (title, source, link, published time) in attributes — mobile-scoped, appears only where your API access exposes the feed |
| Portfolio ID, User ID, Primary Currency, Portfolio Name, Financial Year End | Diagnostic info |

### Daily Performance
| Sensor | Description |
|--------|-------------|
| Daily Change Amount / Percent | Today's total change |
| Daily Capital / Currency / Dividend Gain (+ Percent) | Breakdown of today's change |
| Daily Start Value / End Value | Opening and current value today |

### Weekly Performance
| Sensor | Description |
|--------|-------------|
| Weekly Change Amount / Percent | This week's total change |
| Weekly Capital / Currency / Dividend Gain (+ Percent) | Breakdown of this week's change |
| Weekly Start Value / End Value | Monday open and current value |

### Financial Year
| Sensor | Description |
|--------|-------------|
| FY Change Amount / Percent | Financial year total change |
| FY Capital / Currency / Dividend Gain (+ Percent) | Breakdown of FY change |
| FY Annualised Return Percent | Annualised return for the FY |
| FY Start Value / End Value | Start-of-FY and current value |

### Per-Market (one device per exchange, e.g. ASX, NYSE, LSE)
| Sensor | Description |
|--------|-------------|
| Value | Total value of holdings on this exchange |
| Capital / Total / Currency / Dividend Gain (+ Percent) | Gain breakdowns per market |
| Cost Basis | Total invested in this market |
| Annualised Return Percent | Annualised return for this market |
| Holding Count | Number of holdings on this exchange |

### Holdings
| Sensor | Description |
|--------|-------------|
| Number of Holdings | Total count of holdings |
| Largest Holding (Symbol / Value / Percent) | Your biggest position |
| Smallest Holding (Symbol / Value) | Your smallest position |
| Top Gain (Symbol / Amount / Percent) | Best performing holding |
| Worst Gain (Symbol / Amount / Percent) | Worst performing holding |
| Positive / Negative Holdings Count | How many holdings are green vs red |
| Positive / Negative Holdings Percent | Share of holdings that are green vs red |
| Average / Median Holding Value | Central tendency of holding sizes |
| Total Holdings Value / Gain | Aggregate value and gain across holdings |
| Top 3 / Top 5 Holdings Percent | Concentration of portfolio in largest holdings |
| Unconfirmed Transactions | Trades awaiting confirmation |

### Cash Accounts
| Sensor | Description |
|--------|-------------|
| Cash Balance | Balance per cash account (including Xero) |

### Income / Dividends
| Sensor | Description |
|--------|-------------|
| Total Dividend Income | Total dividends received (accrual basis) |
| Number of Dividends | Count of dividend payments |
| Average / Largest Dividend Amount | Central tendency / record dividend |
| Largest Dividend Symbol | Holding that paid the largest single dividend |
| Last Dividend Date | Date of most recent dividend |
| Dividends Last 30 Days / YTD / Last 12 Months / Previous Year | Period-bucketed dividend totals |
| Dividends Received (Cash) | Actual dividends paid into cash accounts |
| Dividend Yield Percent (current / TTM) | Yield on portfolio value |
| Upcoming Dividends Count | Payouts with future ex-dividend dates |
| Next Dividend (Date / Amount / Symbol) | Soonest upcoming dividend |
| DRP Reinvestment Count | Dividends reinvested via DRP |
| Total Gross Dividend Income | Pre-tax dividend total |
| Total Resident / Non-Resident Withholding Tax | Withholding tax aggregates |
| Total Tax Credits | Imputation / franking credits |
| Total Franked / Unfranked Amount | Franked vs unfranked split |
| Total Foreign Source Income | Income classified as foreign-source |
| Total Capital Gains Distributions | Capital gains distributed via dividends |
| Forward Annual Income | Projected next-12-month dividend income (announced payouts + trailing run-rate) |
| Forward Dividend Yield | Forward annual income as a percent of portfolio value |
| Income Next 30 / 90 Days | Dividends due within the next 30 / 90 days |
| Days Until Next Dividend | Days to the soonest upcoming ex-dividend / pay date |
| Announced Income Unpaid | Declared-but-not-yet-paid dividend total |
| **Dividend Calendar** (calendar entity) | Received + announced dividends as all-day calendar events, **plus a distinct all-day ex-dividend event** on each payout's ex-date (summary `<SYMBOL> ex-dividend`, with the pay date in the description) — use it in calendar cards or calendar-trigger automations |

### Tax (CGT) — Australian portfolios only
| Sensor | Description |
|--------|-------------|
| CGT Taxable Gain FY | Realised capital gains tax position for the current financial year |
| CGT Short / Long Term Gains FY | Realised gains split by holding period (long-term gains are concession-eligible) |
| CGT Losses FY | Realised losses available to offset |
| CGT Concession Amount FY | CGT discount applied to long-term gains |
| CGT Discounted / Non-Discounted Distributions FY | Capital gain distributions from funds/trusts |
| Unrealised CGT Taxable Gain | "If I sold everything today" tax exposure |
| Unrealised CGT Short / Long Term Gains / Losses | Unrealised gains split by holding period |
| Unrealised CGT Concession Amount | CGT discount that would apply today |

### Benchmark (requires a benchmark set on the portfolio in Sharesight)
| Sensor | Description |
|--------|-------------|
| Benchmark Name / Code | The configured benchmark instrument |
| Benchmark Total / Capital / Dividend / Currency Gain Percent | Benchmark performance since portfolio inception |
| Portfolio Excess Return vs Benchmark | Your total return minus the benchmark's (positive = beating it) |

### Sector & Industry Allocation
| Sensor | Description |
|--------|-------------|
| Top Sector 1–5 (Name / Percent / Value) | Your largest sector exposures (value-weighted) |
| Sector Count / Top 3 / Top 5 Sectors Percent | Sector diversification and concentration |
| Top Industry 1–2 (Name / Percent) / Industry Count | Finer industry breakdown |

### Labels (only when your holdings carry Sharesight labels)
| Sensor | Description |
|--------|-------------|
| `<Label>` value | Total portfolio value carrying this label |
| `<Label>` percent | That value as a share of the whole portfolio |

> Labels are **non-exclusive** — a holding can carry several — so these percentages can sum to **more than 100%**. Each figure is the share of portfolio value carrying that label, not a slice of a mutually-exclusive pie. The Labels device (and its sensors) appear only when at least one holding has a label; if none do, nothing is created. Derived in-memory from the already-fetched holdings, so they add no API cost.

### Analytics (concentration & quality — zero extra API cost)
| Sensor | Description |
|--------|-------------|
| Concentration (HHI) | Herfindahl-Hirschman index of holding weights (higher = more concentrated) |
| Effective Number of Holdings | 1 / HHI — how many equally-weighted holdings your concentration is equivalent to |
| Weighted Dividend Yield | Value-weighted trailing dividend yield across holdings |
| Weighted P/E | Value-weighted price/earnings ratio (holdings that report a P/E) |
| Foreign Currency Exposure | Share of portfolio value held in non-base currencies |
| Cash Drag | Cash as a percent of total portfolio value |
| Stale Price Count | Holdings whose Sharesight price hasn't refreshed recently |

### Portfolio Totals (all-time, including sold positions)
| Sensor | Description |
|--------|-------------|
| All-Time Value (incl. sold) | Lifetime portfolio value from the V3 `totals` endpoint |
| All-Time Return (incl. sold) | Lifetime return including realised gains from exited holdings |
| All-Time Return Percent (incl. sold) | Lifetime return as a percentage |
| Return Is Annualised | Whether Sharesight annualised the above percentage (diagnostic) |

> The main performance report omits fully-sold positions, so this device restores true lifetime P&L. It appears only once the V3 `totals` endpoint returns data for your token.

### Account (from `my_user.json`)
| Sensor | Description |
|--------|-------------|
| Sharesight Plan / Plan Code | Your subscription tier (explains why plan-gated data may be absent) |
| Sharesight Member Since / Account Name | Profile info |
| Sharesight Subscription Status | Active / Expired / Cancelled |
| Subscription Problem (binary sensor) | **Turns on if your subscription lapses** — data silently goes stale otherwise; alert on this |

### Status Flags (binary sensors)
Derived from already-fetched data, so they add no API cost and give you something concrete to automate against.

| Binary sensor | On when | Device |
|--------------|---------|--------|
| Any Market Open | Any market you hold in is currently within trading hours (weekends closed; holidays not modelled) | Market Hours |
| Has Unconfirmed Transactions | The portfolio has trades awaiting confirmation | Portfolio |
| Dividend Imminent | A held instrument goes ex-dividend within the next 3 days | Income |
| API Degraded | The Sharesight API is in a rate-limit / lockout cooldown (diagnostic — stays available during failures) | Portfolio |

> The **Subscription Problem** flag (on the Account device, above) is the other binary sensor — alert on it so you notice if data silently goes stale.

### Per-Holding extras (added to each holding device)
| Sensor | Description |
|--------|-------------|
| PE Ratio / EPS / NTA | Fundamentals from Sharesight's instrument feed (null for many ETFs/funds) |
| Sector / Industry / Instrument Type | Classification metadata |
| Price Updated | When Sharesight last refreshed this instrument's price |
| Dividends TTM / Yield on Cost / Franking Credits TTM | Trailing-12-month income per holding |
| Last Dividend Amount / Date / Dividend Count | Per-holding dividend history |
| Average Buy Price / Brokerage Paid / Net Shares Traded | Volume-weighted cost and trade activity |
| Last Trade Date / Trade Count | Per-holding trade activity |

### Watchlist / FX / Market Hours (availability depends on your Sharesight API access)
| Sensor | Description |
|--------|-------------|
| Watchlist Count / Up Today / Down Today / Average Change | Overview of instruments you watch but don't hold |
| Watchlist Top Gainer / Top Loser (+ percent) | Biggest daily movers on your watchlist |
| Watchlist `<CODE>` price / day change percent | Live price and today's percent change for each instrument on your watchlist (up to 50, all on the single Watchlist device; auto-discovered on each poll) |
| `<CUR>` to `<BASE>` rate | Live FX rate per foreign currency you hold (multi-currency portfolios) |
| `<MARKET>` status / next open / next close | Trading-hours state per market you hold in |

> These last three groups rely on Sharesight's mobile/internal API surface. If
> your API token can't reach them they simply never appear (the integration
> parks the endpoint) — everything else keeps working.

### Diversity
| Sensor | Description |
|--------|-------------|
| Top Market 1–5 (Name / Percent / Value) | Your five largest market exposures |
| Diversity Group Count | Number of distinct market groups |
| Top 3 / Top 5 Markets Percent | Concentration of portfolio in largest markets |

### Trades
| Sensor | Description |
|--------|-------------|
| Last Trade (Date / Symbol / Type / Value) | Details of your most recent trade |
| Last Buy (Date / Symbol / Value) | Details of your most recent buy trade |
| Last Sell (Date / Symbol / Value) | Details of your most recent sell trade |
| Total Trades / Buy Count / Sell Count | All-time trade counts |
| Trades Last 7 Days / 30 Days / YTD | Period-bucketed trade counts |
| Total Buy Value / Sell Value / Net Trade Flow | Aggregate trade values |
| Average Trade / Buy / Sell Value | Central tendency of trade sizes |
| Largest Trade (Symbol / Value) | Your biggest single trade |
| Total Brokerage | Sum of broker fees across all trades |
| Most Traded Symbol | Symbol with the highest trade count |
| Average Trades Per Month | Trading frequency since portfolio inception |

### Contributions
| Sensor | Description |
|--------|-------------|
| Total Contributions | Total cash deposited |
| Total Withdrawals | Total cash withdrawn |
| Net Contributions | Deposits minus withdrawals |
| Contribution / Withdrawal Count | Number of cash movements |
| Average Contribution Amount | Central tendency of deposit sizes |
| Last Contribution Date / Amount | Most recent cash movement |
| Net Investment Gain / Percent | Portfolio value minus net contributions |

### Diagnostics
| Sensor | Description |
|--------|-------------|
| Last Successful Update | Timestamp of the last successful poll |
| Update Interval (s) | Current coordinator polling interval |
| Optional Endpoints On Cooldown | Count of endpoints temporarily skipped due to rate limits |
| Portfolio Inception Date / Country / Owner / Access Level | Portfolio metadata |
| Portfolio Age (days) | Days since portfolio inception |
| Performance Calculation Method | How returns are calculated |

---

## Services

The integration registers six **response services** — they return data rather than change state. Call them with `response_variable` in a script/automation, or tick **Return response** in **Developer Tools → Actions**. Each targets a portfolio via `config_entry_id` or `device_id`; both are optional when only one portfolio is configured.

### `sharesight.get_portfolio_summary`

Headline value, period gains, top/worst movers, cash and trailing dividend income — read from already-held data (no extra API call).

```yaml
action: sharesight.get_portfolio_summary
data:
  config_entry_id: 1a2b3c4d5e6f7a8b9c0d   # optional with one portfolio
response_variable: summary
```

Response:

```yaml
value: 152340.55
day: { gain: 421.30, percent: 0.28 }
week: { gain: -180.10, percent: -0.12 }
month: { gain: 2110.00, percent: 1.4 }
ytd: { gain: 9800.00, percent: 6.9 }
fy: { gain: 12040.00, percent: 8.6 }
holding_count: 23
total_cash: 5120.00
top_mover: { symbol: CBA.ASX, amount: 210.5, percent: 1.9 }
worst_mover: { symbol: BHP.ASX, amount: -95.0, percent: -0.8 }
dividends_ttm: 4380.22
currency: AUD
```

### `sharesight.get_holdings`

The portfolio's holdings as a sortable, limitable list (no extra API call).

```yaml
action: sharesight.get_holdings
data:
  sort_by: capital_gain      # value | capital_gain | capital_gain_percent | symbol (default value)
  limit: 5                   # optional
response_variable: holdings
```

Response:

```yaml
holdings:
  - symbol: CBA.ASX
    market: ASX
    value: 18240.0
    quantity: 120
    capital_gain: 2100.0
    capital_gain_percent: 13.0
    currency: AUD
count: 5
```

### `sharesight.get_income`

Trailing-twelve-month, calendar-year-to-date and next-30-day dividend income, plus announced upcoming payouts (no extra API call).

```yaml
action: sharesight.get_income
response_variable: income
```

Response:

```yaml
ttm: 4380.22
ytd: 2110.00
next_30d: 305.40
upcoming:
  - symbol: VAS.ASX
    amount: 142.10
    ex_date: "2026-07-25"
    pay_date: "2026-08-14"
```

### `sharesight.generate_performance_report`

The only service that hits the API — one request per call. Fetches an on-demand performance report for an arbitrary date range and grouping.

```yaml
action: sharesight.generate_performance_report
data:
  start_date: "2024-07-01"
  end_date: "2025-06-30"
  grouping: market          # optional (market / industry_classification / investment_type / ...)
  consolidated: false       # optional
  include_sales: true       # optional
response_variable: report
```

Response: the raw Sharesight performance report — `value`, `capital_gain(_percent)`, `payout_gain(_percent)`, `currency_gain(_percent)`, `total_gain(_percent)`, `start_date` / `end_date`, plus grouped `holdings` / `sub_totals`. On an API failure the response is `{ error: "..." }` rather than raising.

### `sharesight.get_instrument_fundamentals`

Sharechecker fundamentals plus the official average purchase price and cost base for one held instrument, identified by its symbol. Makes a few on-demand API requests each time it is called (some are mobile-scoped and may be unavailable to standard API tokens).

```yaml
action: sharesight.get_instrument_fundamentals
data:
  symbol: AAPL            # must be a symbol you hold in this portfolio
response_variable: fundamentals
```

Response:

```yaml
symbol: AAPL
instrument_id: 12345
holding_id: 67890
sharechecker:
  instrument: { code: AAPL, market_code: NASDAQ, name: Apple Inc., sector_name: Technology }
  performance: { capital_gain: 1200.0, capital_gain_percent: 18.4, total_return_gain_percent: 21.0 }
  price: { value: 224.5, currency: USD }
average_purchase_price: { value: 142.10, currency: USD }
cost_base: { total_value: 14210.0, value_per_share: 142.10, currency: USD }
```

Each block is returned only if its call succeeded; a gated or unreachable call comes back as `{ error: "..." }` in that block's place rather than failing the whole service.

### `sharesight.get_login_link`

Returns a **single-sign-on URL** that logs straight into the Sharesight account — no email/password prompt. The link is valid for about one minute, and this endpoint is exempt from the API rate limit.

```yaml
action: sharesight.get_login_link
response_variable: login
```

Response:

```yaml
login_url: "https://api.sharesight.com/users/sign_in?signon-token=..."
```

> ⚠️ **Treat the returned URL like a password.** Anyone who opens it lands in a fully logged-in Sharesight session. The integration never logs it at any level, and neither should your automation — if you surface it (e.g. in a mobile notification or a dashboard button) do so knowingly and rely on its ~one-minute expiry. On failure the response is `{ login_url: null, error: "..." }`.

---

## Activity events & device triggers

Each portfolio gets an **activity event entity** — `event.sharesight_activity_<portfolio_id>` ("Sharesight Activity", on the Portfolio device). Every poll the coordinator diffs the new data against the previous poll (zero extra API cost) and fires an event when something changes. Event types:

`dividend_announced`, `dividend_paid`, `trade_confirmed`, `holding_opened`, `holding_closed`, `cash_transaction`, `daily_close`, `news_published`

The fired `event_type`, the triggering record's fields, and the full same-poll batch (under `items`) are all carried on the event's attributes.

`news_published` fires when Sharesight's instrument-news feed surfaces a new headline for one of your instruments (only the title, link, source, published time and symbol — never the article body). That feed is mobile-scoped, so it starts firing once the optional endpoint becomes reachable for your token; the same headlines also populate the **Latest News** sensor on the Portfolio device. The remaining event types are also exposed as pick-from-the-UI device triggers below; `news_published` is available as a state trigger on the event entity only.

Example automation (state trigger on the event entity):

```yaml
automation:
  - alias: Notify on confirmed trade
    triggers:
      - trigger: state
        entity_id: event.sharesight_activity_123456
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'trade_confirmed' }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.to_state.attributes.symbol }} trade confirmed"
```

**Device triggers** — the Portfolio device also advertises triggers you can pick straight from the UI (**Settings → Automations → Add → Device → your Sharesight portfolio**):

| Trigger | Fires when |
|---------|-----------|
| Dividend announced / Dividend paid | A dividend is announced or paid |
| Trade confirmed | A trade is confirmed |
| Holding opened / Holding closed | A position is opened or fully closed |
| Cash transaction | A cash-account transaction appears |
| Daily close (end-of-day rollover) | The daily performance window rolls over |
| Portfolio value | Portfolio value crosses an above/below threshold you set |
| Daily change amount | Today's change crosses an above/below threshold you set |

The last two delegate to Home Assistant's numeric-state trigger against the Portfolio Value / Daily Change Amount sensors, so you get the standard above/below fields. Event and numeric triggers only appear when their backing entity exists.

---

## Buttons

Two buttons per portfolio:

| Button | Device | Action |
|--------|--------|--------|
| Refresh | Portfolio | Forces an immediate (debounced) coordinator poll |
| Rebuild Value History | Account | Re-runs the inception-to-today long-term-statistics backfill on demand (e.g. after the value-data endpoint becomes reachable) — idempotent, safe to press repeatedly |

Entity IDs: `button.sharesight_refresh_<portfolio_id>` and `button.sharesight_rebuild_value_history_<portfolio_id>`.

---

## Polling, performance & the recorder

- **Tiered polling.** The headline value and the day/week windows refresh on **every** poll; the slower financial-year, year-to-date and one-month performance windows only re-fetch **every 12th poll** (≈ hourly at the 5-minute default), on a cold start, or when the financial-year bounds roll over. Skipped windows are carried forward so their sensors never flap. This keeps the integration comfortably inside Sharesight's 360-requests/minute budget.
- **Recorder exclude (optional).** The activity event entity carries the whole same-poll batch under its `items` attribute, and a few anchor sensors (e.g. Portfolio Value) expose capped rich-list attributes (top holdings / movers, ≤ 25 items) that are handy in templates but verbose in history. If you want to keep the recorder database lean, exclude the entities whose attribute history you don't need — the event entity is safe to drop entirely as it has no meaningful numeric history:
  ```yaml
  recorder:
    exclude:
      entities:
        - event.sharesight_activity_123456
  ```

---

## Troubleshooting

- **Sensors showing "Unknown"** — Some sensors (Trades, Contributions, Income details) depend on optional API endpoints that may not be available on all Sharesight plans. These will show as `unknown` if the API returns an error.
- **"OAuth authentication failed"** — Double-check your Redirect URI matches exactly what's configured in your Sharesight API application. The most common issue is a trailing slash mismatch.
- **Missing markets or cash accounts** — New markets and cash accounts are auto-discovered on the next poll cycle (every 5 minutes by default, or whatever you set in Options). If you've just added a new holding on a new exchange, give it a refresh cycle or two.
- **Debug logging** — To see detailed API response data, enable debug logging for the integration:
  ```yaml
  logger:
    logs:
      custom_components.sharesight: debug
  ```

---

## Links

- [Sharesight API Documentation](https://portfolio.sharesight.com/api/)
- [Report Issues](https://github.com/Poshy163/HomeAssistant-Sharesight/issues)
