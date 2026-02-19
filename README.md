# Home Assistant Sharesight Integration

![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)

Monitor your [Sharesight](https://www.sharesight.com/) investment portfolio directly from Home Assistant. Track portfolio value, daily/weekly/financial-year performance, per-market breakdowns, holdings, dividends, trades, contributions, and more.

**Key features:**
- OAuth2 authentication — no API keys stored in YAML
- Automatic portfolio discovery — select your portfolio from a dropdown during setup
- Per-market devices — each exchange (ASX, NYSE, LSE, etc.) gets its own HA device
- Cash account tracking — including Xero-linked accounts
- Auto-discovery of new markets and cash accounts (checked every 10 minutes)
- Supports both standard and Edge (developer) API accounts
- Multiple portfolio support — add the integration once per portfolio

---

## Prerequisites

Before installing, you need to create an API application on Sharesight:

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

All sensors are organized into separate HA devices by category. Data refreshes every **5 minutes**.

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
| Top Gain (Symbol / Amount / Percent) | Best performing holding |
| Worst Gain (Symbol / Amount / Percent) | Worst performing holding |
| Positive / Negative Holdings Count | How many holdings are green vs red |
| Unconfirmed Transactions | Trades awaiting confirmation |

### Cash Accounts
| Sensor | Description |
|--------|-------------|
| Cash Balance | Balance per cash account (including Xero) |

### Income
| Sensor | Description |
|--------|-------------|
| Total Dividend Income | Total dividends received |
| Number of Dividends | Count of dividend payments |
| Last Dividend Date | Date of most recent dividend |

### Diversity
| Sensor | Description |
|--------|-------------|
| Top Market 1/2/3 (Name / Percent / Value) | Your three largest market exposures |

### Trades
| Sensor | Description |
|--------|-------------|
| Last Trade Date / Symbol / Type / Value | Details of your most recent trade |
| Trades Last 30 Days | Number of trades in the last month |
| Total Trades | All-time trade count |

### Contributions
| Sensor | Description |
|--------|-------------|
| Total Contributions | Total cash deposited |
| Total Withdrawals | Total cash withdrawn |
| Net Contributions | Deposits minus withdrawals |
| Last Contribution Date / Amount | Most recent cash movement |

---

## Troubleshooting

- **Sensors showing "Unknown"** — Some sensors (Trades, Contributions, Income details) depend on optional API endpoints that may not be available on all Sharesight plans. These will show as `unknown` if the API returns an error.
- **"OAuth authentication failed"** — Double-check your Redirect URI matches exactly what's configured in your Sharesight API application. The most common issue is a trailing slash mismatch.
- **Missing markets or cash accounts** — New markets and cash accounts are auto-discovered every 10 minutes. If you've just added a new holding on a new exchange, give it a couple of refresh cycles.
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
