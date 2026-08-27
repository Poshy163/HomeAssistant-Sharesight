# Home Assistant Sharesight Integration

![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)

Monitor your [Sharesight](https://www.sharesight.com/) investment portfolio directly from Home Assistant. Track portfolio value, daily/weekly/financial-year performance, per-market breakdowns, holdings, dividends, trades, contributions, and more.

**Key features:**
- OAuth2 authentication — no API keys stored in YAML
- Automatic portfolio discovery — select your portfolio from a dropdown during setup
- Per-market devices — each exchange in the performance report's market
  sub-totals (ASX, NYSE, LSE, etc.) gets its own HA device
- Cash account tracking — including Xero-linked accounts
- Auto-discovery of new market-allocation groups and cash accounts from refreshed portfolio data
- Dividend calendar — received and announced dividends as a native HA calendar entity
- Capital gains tax (CGT) sensors for Australian portfolios — realised FY + unrealised positions
- Benchmark comparison — track your excess return against the benchmark configured in Sharesight
- Per-holding fundamentals (P/E, EPS, NTA, sector, industry), dividend income, yield-on-cost and average buy price
- Sector & industry allocation breakdown — a diversification lens beyond markets
- Account device — plan tier, member-since, and a subscription-lapse alert (binary sensor)
- Watchlist overview plus per-instrument live price and day-change, where your Sharesight API access exposes it
- Long-term statistics backfill — imports your full portfolio value history so HA charts show years, not just days since install (opt-out in options)
- Response services — pull a portfolio summary, holdings or income into scripts/templates, generate an on-demand performance report, fetch per-instrument fundamentals, or mint a one-minute single-sign-on login link
- Activity events & device triggers — automate on new trades, dividends, holdings opened/closed, cash transactions and the daily-close rollover
- Portfolio analytics — concentration (HHI), effective number of holdings, weighted yield/PE, foreign-currency exposure, cash drag and stale-price count
- All-time performance & forward income — lifetime value/return including sold positions, plus projected forward dividend income and yield
- Value-trend & label sensors — 7-day / 30-day portfolio value change (with a sparkline series), plus value and portfolio share per Sharesight label
- Supports both standard and Edge (developer) API accounts
- Risk metrics — portfolio maximum drawdown, current drawdown, high-water mark, days since high and annualised volatility, all derived locally from the daily value series
- Allocation by currency and by asset type, alongside the existing market, sector, industry and label breakdowns
- Tax-loss harvesting figures for Australian portfolios — harvestable unrealised loss, loss-parcel count, claimable loss and the CGT concession rate
- Optional longer performance windows — 3 month, 6 month, 1 / 3 / 5 year
- Honest staleness reporting — a **Data Stale** binary sensor and an accurate "Last Successful Update", so you can tell held-over numbers from live ones
- Multiple portfolio support — add the integration once per portfolio

---

## Upgrading to 2.1

**2.1 is a correctness and resilience release. No entity IDs change and no
reconfiguration is needed — but several sensors will start reporting different
(correct) numbers, and a few that were permanently Unknown will come alive.**

### Numbers that change because they were wrong

| Sensor | Was | Now |
|--------|-----|-----|
| Financial Year * | Only correct for a 30 June financial year. A calendar-year (`12-31`) portfolio got a window **entirely in the future** for seven months of every year | The window containing today, for any financial year end |
| Total Sell Value, Average Sell Value | Negative — Sharesight signs a sale's value negatively and it was summed as-is | The magnitude |
| Net Trade Flow | Buys **plus** sells | Buys **minus** sells |
| Largest Trade Value / Symbol | Could never be a sale | Ranks by magnitude, so a sale can win |
| Total Brokerage, per-holding Brokerage Paid | Mixed AUD/USD/GBP/… added together and labelled as one currency | Converted with each trade's own exchange rate |
| Total Dividend Income and every payout total | Same problem: foreign dividends summed unconverted | Converted with each payout's own exchange rate |
| Total Tax Credits | Always Unknown — read a field the API has never returned | Franking credits |
| Total Capital Gains Distributions | Always Unknown — same reason | Discounted + non-discounted distributions |
| Benchmark Capital Gain % | Always Unknown — read the *documented* field name, not the one the API returns | The live field |
| Total / Net Contributions | Silently dropped broker-synced deposits whose type is null | Classified by the sign of the amount when untyped, and trade/dividend settlements excluded |
| Dividends Received (Cash) | Always $0 — matched a transaction type Sharesight does not emit | Rows linked to a payout |
| Forward Annual Income, Income Next 30/90 Days | Projected income from positions **sold years ago**, and an announced payer lost the rest of its year | Only currently-held payers, and an announcement no longer cancels the remaining run rate |
| Weighted Dividend Yield | Non-payers dropped from the denominator, inflating the figure | Non-payers count as 0% |
| Weighted P/E | Arithmetic mean, often over a sliver of the portfolio | Harmonic mean, suppressed below 50% coverage (with a P/E Coverage diagnostic) |
| Foreign Currency Exposure | 0% whenever an optional endpoint was unavailable | Read from the holding rows, which always carry the currency |
| Stale Price Count | A confident 0 when there was no price data at all | Unknown, with a coverage diagnostic |
| Holding price / average buy price | Labelled in the *portfolio* currency | Labelled in the *instrument's* currency |
| Every monetary sensor, on a multi-portfolio account | Took the currency of whichever portfolio the API listed first | This portfolio's currency |
| Top Market 1–5 | Actually reported **industry** groups — the diversity call omitted `grouping`, whose API default is industry | Market groups |
| Last Successful Update | Stamped "now" even on polls that served held-over data | When the data was really fetched |

### New

- **Risk:** Maximum Drawdown, Current Drawdown, High Water Mark, Days Since
  High, Volatility (annualised), plus Benchmark Maximum Drawdown and Benchmark
  Return Over Drawdown — the last two were arriving on every poll and being
  thrown away.
- **Allocation:** Top Currency 1–3 and Top Asset Type 1–3 (name + percent) with
  counts, on the existing Sector Allocation device.
- **Tax (AU):** Harvestable Unrealised Loss, Harvestable Loss Parcels, CGT
  Claimable Loss, CGT Short/Long Term Losses, CGT Concession Rate.
- **Diagnostics:** Dividend Yield Coverage, P/E Coverage, and a **Data Stale**
  binary sensor.
- **Per item:** holding currency, watchlist absolute day change, per-label
  holding count.
- **All-Time (incl. sold)** no longer depends on Sharesight's internal totals
  route. The four sensors use a public performance report with
  `include_sales=true`, preferring V3 and falling back to the equivalent V2
  route only for a route/version mismatch.
- **Options:** *Create per-holding entities* (on by default — turn it off to
  keep only portfolio-level figures) and *Add 3/6 month and 1/3/5 year windows*
  (off by default).
- **Portfolio identity is protected:** switching an existing entry to another
  portfolio would mint different device/entity identities and risk duplicate
  history, so Reconfigure explains that the other portfolio must be added as a
  separate Sharesight entry.

### Behaviour

- **Failures are now bounded.** The integration still holds the last good
  numbers through a blip, but after roughly 30 minutes (or four poll intervals,
  whichever is longer) it gives up and marks entities unavailable rather than
  presenting hours-old figures as current. The **Data Stale** binary sensor
  says which is happening, and carries `fetched_at` / `age_seconds`.
- **Reauthentication actually fires.** A revoked token was previously mistaken
  for a network blip and retried forever. It now prompts you to re-authenticate
  — and reauthentication refuses a token from a Sharesight account that cannot
  see the portfolio.
- **Logs are bounded and actionable.** A failure is logged once at WARNING with
  `endpoint=…, status=…`; identical repeats drop to DEBUG until it recovers,
  which is then logged once at INFO. Per-row sensor calculation traces are not
  emitted on every refresh.
- **Diagnostics are compact and actually redacted.** The download used to
  reproduce the entire payload, including the account holder's name; it now
  reports bounded summaries instead of copying raw financial records.

**Minimum Home Assistant version is now 2026.7.4.** This is the first Home
Assistant release whose `aiohttp` 3.14.3 requirement matches SharesightAPI
1.4.0.

---

## Upgrading to 2.0

**Version 2.0 modernises how devices and entities are presented in Home Assistant. Your data, history and automations are preserved — only display names and device layout change.**

- **Entity IDs are unchanged.** Every `sensor.…`, `binary_sensor.…`, `button.…`, `event.…` and `calendar.…` entity keeps the exact ID it had on 1.9.x, so dashboards, automations, templates and long-term statistics keep working with no edits. Only the human-readable *friendly names* change.
- **Names now derive from device + entity.** Entities use Home Assistant's modern naming: the displayed name is the **device name plus the entity name** (e.g. the *Sharesight Portfolio 123* device's value sensor now reads as "Sharesight Portfolio 123 Portfolio value"). This makes names consistent and translatable, and is why friendly names look different after upgrading even though the entity IDs did not move.
- **Nested device tree.** The per-category devices (Daily Performance, Holdings, Income, each market, each holding, Watchlist, Analytics, …) now nest **under the portfolio hub device** using Home Assistant's device-parent link. Open the portfolio device and you'll see the whole fleet as a tree instead of ~25 flat, separately-listed devices.
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
5. Choose the account type before authorising: **Standard** is correct for most
   users; choose **Developer** only if Sharesight has provisioned a sandbox
   account and you added its separate Edge application credential
6. You'll be redirected to the matching Sharesight host to **authorise** the connection — log in and click **Allow**
7. After returning to Home Assistant, **select your portfolio** from the dropdown list
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

These devices and the market-allocation figures come from the combined
performance report's market-grouped `sub_totals`; the integration does not call
the separate market-metadata endpoint.

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

### Currency & Asset-Type Allocation
| Sensor | Description |
|--------|-------------|
| Top Currency 1–3 Name / Percent | Value-weighted share of the portfolio priced in each currency |
| Currency Count | Distinct currencies held |
| Top Asset Type 1–3 Name / Percent | Share by instrument type (ordinary share, ETF, managed fund, …) |
| Asset Type Count | Distinct instrument types held |

> Both are derived from the holdings rows, so they work regardless of which
> optional endpoints your API access can reach. They live on the **Sector
> Allocation** device.

### Labels (only when your holdings carry Sharesight labels)
| Sensor | Description |
|--------|-------------|
| `<Label>` value | Total portfolio value carrying this label |
| `<Label>` percent | That value as a share of the whole portfolio |

> Labels are **non-exclusive** — a holding can carry several — so these percentages can sum to **more than 100%**. Each figure is the share of portfolio value carrying that label, not a slice of a mutually-exclusive pie. The Labels device (and its sensors) appear only when at least one holding has a label; if none do, nothing is created. The figures are derived in memory from already-fetched holdings.

### Analytics (concentration & quality, derived locally)
| Sensor | Description |
|--------|-------------|
| Concentration (HHI) | Herfindahl-Hirschman index of holding weights (higher = more concentrated) |
| Effective Number of Holdings | 1 / HHI — how many equally-weighted holdings your concentration is equivalent to |
| Weighted Dividend Yield | Value-weighted trailing dividend yield across holdings |
| Weighted P/E | Value-weighted price/earnings ratio (holdings that report a P/E) |
| Foreign Currency Exposure | Share of portfolio value held in non-base currencies |
| Cash Drag | Cash as a percent of total portfolio value |
| Stale Price Count | Holdings whose Sharesight price hasn't refreshed recently |

### Risk (derived locally from the daily value series)
| Sensor | Description |
|--------|-------------|
| Maximum Drawdown | Largest peak-to-trough fall in the window (%) |
| Current Drawdown | How far below the peak the portfolio sits right now (0% at a new high) |
| High Water Mark | The peak value in the window |
| Days Since High | Days since that peak was set |
| Volatility (annualised) | Standard deviation of the period returns, annualised from the series' own spacing. Disabled by default |

> The window is whatever the value series covers — about 45 days by default.
> The **Benchmark** device carries Sharesight's own *Benchmark Maximum
> Drawdown* and *Benchmark Return Over Drawdown* alongside these, so you can
> compare like for like.

### All-Time Performance (including sold positions)
| Sensor | Description |
|--------|-------------|
| All-Time Value (incl. sold) | Lifetime portfolio value from a V3 performance report requested from inception with sold positions included |
| All-Time Return (incl. sold) | Lifetime return including realised gains from exited holdings |
| All-Time Return Percent (incl. sold) | Lifetime return as a percentage |
| Return Is Annualised | Whether Sharesight annualised the above percentage (diagnostic) |

> The headline performance report omits fully-sold positions. This device uses
> a separate inception-to-today public performance window with
> `include_sales=true` to restore lifetime P&L.

### Account (from `my_user.json`)
| Sensor | Description |
|--------|-------------|
| Sharesight Plan / Plan Code | Your subscription tier (explains why plan-gated data may be absent) |
| Sharesight Member Since / Account Name | Profile info |
| Sharesight Subscription Status | Active / Expired / Cancelled |
| Subscription Problem (binary sensor) | **Turns on if your subscription lapses** — data silently goes stale otherwise; alert on this |

### Status Flags (binary sensors)
Derived from already-fetched data, these flags give you something concrete to automate against.

| Binary sensor | On when | Device |
|--------------|---------|--------|
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

### Watchlist (availability depends on your Sharesight API access)
| Sensor | Description |
|--------|-------------|
| Watchlist Count / Up Today / Down Today / Average Change | Overview of instruments you watch but don't hold |
| Watchlist Top Gainer / Top Loser (+ percent) | Biggest daily movers on your watchlist |
| Watchlist `<CODE>` price / day change percent | Live price and today's percent change for each instrument on your watchlist (up to 50, all on the single Watchlist device; auto-discovered on each poll) |

> The watchlist uses a mobile-scoped Sharesight route. If the loaded account
> cannot reach it, the endpoint is parked without affecting the portfolio's
> public performance data.

### Diversity
| Sensor | Description |
|--------|-------------|
| Top Market 1–5 (Name / Percent / Value) | Your five largest market exposures |
| Diversity Group Count | Number of distinct market groups |
| Top 3 / Top 5 Markets Percent | Concentration of portfolio in largest markets |

These rankings use the same market-grouped performance `sub_totals` as the
per-market devices; they do not depend on the internal market-metadata route.

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
| Last Successful Update | When the data currently being served was actually fetched (not when a poll last returned) |
| Update Interval (s) | Current coordinator polling interval |
| Optional Endpoints On Cooldown | Count of endpoints temporarily parked after a failure |
| Dividend Yield Coverage / P/E Coverage | How much of the portfolio's value backs the weighted yield / P&nbsp;E figure |
| Portfolio Inception Date / Country / Owner / Access Level | Portfolio metadata |
| Portfolio Age (days) | Days since portfolio inception |
| Performance Calculation Method | How returns are calculated |

---

## Options

**Settings → Devices & Services → Sharesight → Configure.** Defaults suit most
installations; nothing here needs to be understood to use the integration.

| Option | Default | What it does |
|--------|---------|--------------|
| **Poll interval (seconds)** | 300 | How often the portfolio is refreshed. Clamped to 60–3600; the shared request gate coordinates loaded portfolios against Sharesight's documented rate and concurrency limits |
| **Backfill portfolio value history** | On | On startup, imports your whole inception-to-today daily value series into the Portfolio Value sensor's long-term statistics, so charts show years rather than days-since-install. Needs the value-data endpoint to be reachable for your API access |
| **Automatically delete devices for sold holdings** | Off | See [Deleting stale devices](#deleting-stale-devices) |
| **Create per-holding entities** | On | Per-holding entities dominate the entity count. Turning this off keeps only portfolio-level figures; existing entities are hidden rather than deleted, so turning it back on restores them with their history |
| **Add 3/6 month and 1/3/5 year windows** | Off | Adds locally named performance sensors for five additional windows on the slow tier |

### Extended performance windows

With the option on, five more devices appear. Each carries change amount and
percent, capital gain and percent, and dividend gain and percent.

| Device | Window |
|--------|--------|
| 3 Month / 6 Month | Calendar months back from today, day-clamped |
| 1 / 3 / 5 Year | Calendar years back, clamped to the portfolio's inception date so you never ask for data that cannot exist |

---

## Which API version is used, and why

The integration deliberately mixes V2 and V3 rather than preferring the newer
one. What decides it is not the version number but the **access tier**: many
V3 endpoints are scoped to Sharesight's own web or mobile apps and may return
`403`, `404` or `406` for an ordinary API token or an unsupported version
prefix.

| Data | Source | Why |
|------|--------|-----|
| The combined report (value, gains, holdings, market sub-totals, cash) | **V3** `portfolios/{id}/performance` | Public tier; its response combines holdings with quantity, value and the full gain family |
| Period windows (day, week, month, YTD, FY) | **V3** `portfolios/{id}/performance`, with V2 fallback for a route/version mismatch | Keeps one response shape where V3 is available while retaining a public V2 equivalent |
| All-time including sold positions | **V3** `performance` with `include_sales=true`, with V2 fallback for a route/version mismatch | Public User API routes that include realised gains from exited positions |
| Payouts, trades, cash accounts, instruments, user | **V2** | No V3 equivalent at the portfolio level |
| Benchmark | **V3** | V2 has none — and this is the only source of the drawdown figures |
| Value series and watchlist | **V3** | The watchlist route is mobile-scoped and parks quietly when unavailable |
| Per-market allocation | Combined performance `sub_totals` | No separate markets or exchange-rates route is polled |

**Fallbacks.** Only where the two sources are genuinely equivalent:

- All-time figures use the public inception-to-today performance window. The
  internal totals route is deliberately not called.
- Per-instrument cost figures prefer the public `holdings/{id}` route and fall back
  to the two mobile-scoped `average_purchase_price.json` / `cost_base.json`
  calls only when the combined route is missing or version-incompatible.
- Every optional endpoint falls back to **its own last good payload** while it
  is parked, for up to 12 hours, after which its sensors go unavailable rather
  than reporting something stale as current. A permanent `406` version mismatch
  is capability-gated for the loaded entry instead of being retried forever.

Fallbacks never hide an authentication or configuration error. A `401` or
revoked grant starts re-authentication; a permanently missing portfolio stops
setup and asks you to add its replacement as a new entry.

Full detail, including the exact parameters and the documented-vs-actual field
discrepancies, is in
[docs/sharesight-api-reference.md](docs/sharesight-api-reference.md).

---

## Services

The integration registers six **response services** — they return data rather than change state. Call them with `response_variable` in a script/automation, or tick **Return response** in **Developer Tools → Actions**. Each targets a portfolio via `config_entry_id` or `device_id`; both are optional when only one portfolio is configured.

### `sharesight.get_portfolio_summary`

Headline value, period gains, top/worst movers, cash and trailing dividend
income, read from the coordinator's existing data.

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

The portfolio's holdings as a sortable, limitable list, read from the
coordinator's existing data.

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

Trailing-twelve-month, calendar-year-to-date and next-30-day dividend income,
plus announced upcoming payouts, derived from the coordinator's existing data.

```yaml
action: sharesight.get_income
response_variable: income
```

Response:

```yaml
ttm: 4380.22
ytd: 2110.00
next_30d: 305.40
currency: AUD
upcoming:
  - symbol: VAS.ASX
    amount: 142.10
    currency: AUD
    native_amount: 142.10
    native_currency: AUD
    exchange_rate: 1.0
    ex_date: "2026-07-25"
    pay_date: "2026-08-14"
```

The aggregate values and each upcoming `amount` are denominated in the
portfolio `currency`. Upcoming rows also retain the API's `native_amount`,
`native_currency` and `exchange_rate`. If a foreign payout has no usable
exchange rate, `amount` is `null` rather than being silently relabelled as the
portfolio currency.

### `sharesight.generate_performance_report`

Fetches an on-demand performance report for an arbitrary date range and
grouping.

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

Sharechecker fundamentals plus the official average purchase price and cost
base for one held instrument, identified by its symbol. The service prefers the
public combined holding route and uses split mobile fallbacks only for a
route/version mismatch; some results may therefore be unavailable to a standard
token.

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

Each portfolio gets an **activity event entity** — `event.sharesight_activity_<portfolio_id>` ("Sharesight Activity", on the Portfolio device). The coordinator diffs refreshed data against the previous poll and fires an event when something changes. Event types:

`dividend_announced`, `dividend_paid`, `trade_confirmed`, `holding_opened`, `holding_closed`, `cash_transaction`, `daily_close`

The fired `event_type`, the triggering record's fields, and the full same-poll batch (under `items`) are all carried on the event's attributes.

Monetary event fields are explicit about denomination. Dividend events expose
`amount` in portfolio `currency` plus the native amount/currency and exchange
rate. Trade `value` is in `value_currency` (the portfolio currency), while
`price` is in `price_currency` (the instrument currency). Cash-account events
retain `native_amount`, `native_currency` and `native_balance`; when the cash
account is foreign, the portfolio `amount` and `balance` are `null` because the
transaction feed has no historical FX rate with which to convert them safely.

The event types are also exposed as pick-from-the-UI device triggers below.

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

## Polling, resilience & the recorder

- **One coordinator, three tiers.** Every entity reads from a single shared
  poll — no sensor ever talks to Sharesight on its own. The headline value and
  the day/week windows refresh on **every** poll; the financial-year,
  year-to-date, one-month, all-time and value-series requests refresh **every
  12th poll** (≈ hourly at the 5-minute default), on a cold start, or when the
  financial year rolls over; and optional endpoints back off independently.
  A shared application-level gate coordinates the documented request and
  report-concurrency budgets across loaded portfolios.
- **Nothing is fetched twice.** The performance report already carries the
  holdings with quantity, value and gains, so the separate holdings endpoint is
  not part of the polling plan.
- **Failures are bounded, and visible.** A failed poll serves the previous
  payload so sensors hold rather than flap, but only for four poll intervals (a
  30-minute floor). Past that the entities go unavailable. The **Data Stale**
  binary sensor is on for the whole degraded window and carries `fetched_at`,
  `age_seconds` and `reason`.
- **Parked endpoints keep their last value.** When an optional endpoint backs
  off, its last good payload is replayed for up to 12 hours so its sensors hold;
  after that they go unavailable rather than reporting a stale number as
  current.
- **Logs do not flood.** Each distinct failure logs once at WARNING with
  `endpoint=…, status=…`; identical repeats go to DEBUG until it recovers, and
  recovery logs once at INFO.
- **Recorder-friendly attributes.** The bulky, fast-changing attributes (ranked
  holdings, top movers, the value sparkline series, allocation
  breakdowns) are declared *unrecorded*: they are live in the state machine for
  templates and dashboards, but are no longer written to the database on every
  state change. The activity event entity still carries the whole same-poll
  batch under `items`, so if you want to keep the database leaner still,
  exclude the entities whose history you don't need — the event entity is safe
  to drop entirely as it has no meaningful numeric history:
  ```yaml
  recorder:
    exclude:
      entities:
        - event.sharesight_activity_123456
  ```

---

## Troubleshooting

**Start with diagnostics.** *Settings → Devices & Services → Sharesight → the
three-dot menu → **Download diagnostics***. The file is deliberately small and
answers most questions directly: how old the data is, whether the API is in a
cooldown, how many endpoint families are parked or unsupported, and whether a
token is present. It contains no tokens, account identifiers, email address or
account-holder name. Financial payloads are represented only by redacted
structural summaries, never raw rows.

- **Entities are unavailable and the *Data Stale* sensor is on** — the last few
  polls could not fetch fresh data and the grace period has run out. The
  `degraded_reason` in diagnostics says why. It recovers on its own once
  Sharesight responds.
- **Watchlist sensors show "Unknown"** — the watchlist comes from a
  mobile-scoped route that an ordinary API token may not be entitled to. A
  temporary entitlement failure parks with exponential backoff; a permanent
  version mismatch is not retried every hour. Diagnostics reports the parked
  and unsupported counts without reproducing the endpoint payload.
- **"Re-authentication required"** — the stored grant has been revoked or the
  application credential was deleted. Follow the prompt; the portfolio
  selection, entities and history are all preserved. If reauthentication aborts
  with *wrong account*, you authorised a Sharesight login that cannot see this
  portfolio — sign out of Sharesight in your browser and try again.
- **"OAuth authentication failed"** — the Redirect URI must match your
  Sharesight API application exactly. A trailing slash is the usual culprit.
  Also check that the application credential you picked matches the account
  type you chose: a standard client ID is not valid on the developer sandbox.
- **Missing a new market-allocation group or cash account** — dynamic entities
  are discovered from refreshed portfolio data, so give it a poll after adding
  a holding on a new exchange or changing cash accounts.
- **Debug logging** — per module, so you can turn on just the part you are
  chasing:
  ```yaml
  logger:
    logs:
      custom_components.sharesight: debug            # everything
      custom_components.sharesight.coordinator: debug # requests and tiers only
      custom_components.sharesight.sensor: debug      # entity value resolution
  ```
  Or use *Enable debug logging* on the integration page, which covers all of it.

---

## Known limitations

- **Rate-limit observations are response-based.** When Sharesight supplies
  remaining-budget headers, the shared request gate and diagnostics consume
  them; between responses they are not a live server-side counter.
- **The watchlist is plan/scoped.** Whether a token can reach Sharesight's
  mobile watchlist route is decided by Sharesight, not by this integration.
- **No live FX-rate, market-hours or instrument-news feed.** Their advertised
  internal/mobile routes were rejected by the supplied standard token, so the
  integration does not poll them or create entities/events from them. Market
  allocation still comes from performance `sub_totals`, and foreign-currency
  exposure comes from the currencies on holding rows.
- **Volatility is indicative.** The value series is thinned by Sharesight
  (points can be several days apart), so the annualisation scales by the
  observed spacing rather than assuming trading days. It is a dashboard figure,
  not a risk-model input.
- **"1 week" is week-to-date**, Monday through today — not a trailing seven
  days. On a Monday it is therefore close to zero.
- **Per-holding average buy price is an approximation.** It is a
  volume-weighted average of purchases in the instrument's currency, replayed
  through splits, consolidations and capital returns. Sharesight's own official
  average purchase price (which restates historic buys at the exchange rate of
  the day) is available on demand via the `get_instrument_fundamentals` service.
- **Consolidated portfolio views are not supported.** Sharesight allocates
  consolidated portfolios their own ids in a separate namespace, and the
  integration does not list or address them.

---

## Removing the integration

1. **Settings → Devices & Services → Sharesight → the three-dot menu →
   Delete.** This removes the config entry, all its devices and all its
   entities. Repeat for each configured portfolio.
2. **Remove the application credential** (optional):
   **Settings → Devices & Services → the three-dot menu at the top right →
   Application Credentials → Sharesight → Delete.**
3. **Long-term statistics.** The value-history backfill writes into the
   Portfolio Value sensor's own statistics, so deleting the entity removes them
   with it. If any orphaned statistics remain, clear them in
   **Developer Tools → Statistics** (they are listed with a "no longer being
   recorded" warning and a *Delete* action).
4. **Uninstall** via HACS, or delete `custom_components/sharesight/`, and
   restart Home Assistant.
5. Optionally revoke the integration's access in your Sharesight account
   settings.

---

## Diagnostics

*Settings → Devices & Services → Sharesight → the three-dot menu →
**Download diagnostics***. The compact, redacted file contains:

| Block | What is in it |
|-------|---------------|
| `entry` | Version, source, state, and the entry data with the token redacted |
| `auth` | Account type, whether an OAuth implementation and access/refresh tokens are present, and token timing metadata — never the credential or tokens themselves |
| `coordinator` | Whether it is loaded, when the data was really fetched and how old it is, whether it is degraded and why, the poll interval, the resolved portfolio currency and the financial-year bounds |
| `api` | Base URL, versions in use, whether a cooldown is active and why, and the documented rate limits |
| `endpoints` | Parked/unsupported counts, carried-forward cache ages and a logged-failure count |
| `options_effective` | Every option with its effective value, defaults filled in |
| `entities` | Entity counts per platform and how many you have disabled |
| `data_summary` | Each top-level payload described by type, field/list counts, item field names and serialized size rather than reproduced |

**Redacted or omitted:** access and refresh tokens, client id and secret, the
authorization code, OAuth implementation, redirect URI, portfolio/config-entry
identifiers, email addresses, the account holder's name and the portfolio name
in the entry title. Raw holdings, trades, payouts/payments, tax records and news
payloads are never copied; summaries expose their structure and row counts, not
symbols, arbitrary mapping keys, amounts, comments or attachment filenames.

There is also a **System Health** entry (*Settings → System → Repairs →
three-dot menu → System information*) showing how many portfolios are
configured, whether both Sharesight hosts are reachable, and the most recent
successful update across them.

---

## Development

```bash
python -m pip install -r requirements_test.txt
python -m pytest             # everything, including the Home Assistant tests
ruff check .
ruff format --check .
```

The suite is split so most of it runs anywhere:

- The portable tests directly exercise the reporting-window date maths,
  analytics, endpoint plan, a full simulated poll and structural checks over
  all ~390 sensor descriptions. They still import Home Assistant types, but do
  not need the Home Assistant pytest plugin at runtime. On Windows, where that
  plugin imports POSIX-only modules, run them with plugin autoload disabled:

  ```powershell
  $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
  python -m pytest --ignore=tests/ha
  ```

  On POSIX shells, the equivalent is
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --ignore=tests/ha`.
- `tests/ha/` — the real thing: config, reauth, immutable-portfolio and options flows,
  entry setup, migration from v1 and v2 entries, and diagnostics redaction.
  These need `pytest-homeassistant-custom-component`, which cannot import on
  Windows (it pulls in `fcntl`), so they run on Linux and in CI.

CI reports line coverage for the complete Linux suite and enforces a
conservative floor. Raise that floor as entity-platform coverage expands.

`tests/fixtures.py` holds synthetic payloads shaped field-for-field like the
real API responses, including the awkward cases: a sold-out holding left behind
as dust, a foreign-currency holding, a stale price, a SELL whose value is
negative, a null-typed cash transaction, and a portfolio on a calendar
financial year.

---

## Links

- [Sharesight API Documentation](https://portfolio.sharesight.com/api/)
- [Report Issues](https://github.com/Poshy163/HomeAssistant-Sharesight/issues)
