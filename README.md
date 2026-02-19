# Home Assistant Sharesight integration
![Project Stage](https://img.shields.io/badge/project%20stage-in%20production-green.svg?style=for-the-badge)
![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)


- Supports both normal sharesight accounts, and edge accounts (developer accounts)
- Supports automatically adding additional markets and/or cash accounts (set to check every 10 minutes)

## Services
This allows you to monitor your sharesight portfolio value. Sensors are organized into separate HA devices by category:

### Portfolio
- Portfolio Value
- Capital Gain / Capital Gain Percent
- Total Gain / Total Gain Percent
- Currency Gain / Currency Gain Percent
- Dividend Gain / Dividend Gain Percent
- Cost Basis
- Unrealised Gain / Unrealised Gain Percent
- Annualised Return Percent
- Portfolio Start Value
- Portfolio ID, User ID, Primary Currency, Portfolio Name, Financial Year End

### Daily Performance
- Daily Change Amount / Daily Change Percent
- Daily Capital Gain / Daily Capital Gain Percent
- Daily Currency Gain / Daily Currency Gain Percent
- Daily Dividend Gain / Daily Dividend Gain Percent
- Daily Start Value / Daily End Value

### Weekly Performance
- Weekly Change Amount / Weekly Change Percent
- Weekly Capital Gain / Weekly Capital Gain Percent
- Weekly Currency Gain / Weekly Currency Gain Percent
- Weekly Dividend Gain / Weekly Dividend Gain Percent
- Weekly Start Value / Weekly End Value

### Financial Year
- FY Change Amount / FY Change Percent
- FY Capital Gain / FY Capital Gain Percent
- FY Currency Gain / FY Currency Gain Percent
- FY Dividend Gain / FY Dividend Gain Percent
- FY Annualised Return Percent
- FY Start Value / FY End Value

### Per-Market (one device per exchange, e.g. ASX, NYSE, LSE)
- Value
- Capital Gain / Capital Gain Percent
- Total Gain / Total Gain Percent
- Currency Gain / Currency Gain Percent
- Dividend Gain / Dividend Gain Percent
- Cost Basis
- Annualised Return Percent
- Holding Count

### Holdings
- Number of Holdings
- Largest Holding (Symbol / Value / Percent)
- Top Gain (Symbol / Amount / Percent)
- Worst Gain (Symbol / Amount / Percent)
- Positive Holdings Count / Negative Holdings Count
- Unconfirmed Transactions

### Cash Accounts
- Cash Balance (one per cash account)

### Income
- Total Dividend Income
- Number of Dividends
- Last Dividend Date

### Diversity
- Top 3 Markets (Name / Percent / Value each)

### Trades
- Last Trade Date / Symbol / Type / Value
- Trades Last 30 Days
- Total Trades

### Contributions
- Total Contributions
- Total Withdrawals
- Net Contributions
- Last Contribution Date / Amount




Get API details by following these steps here: https://portfolio.sharesight.com/api/

## Installation using HACS

1. Use [HACS](https://hacs.xyz/docs/setup/download), in `HACS > Integrations > Hamburger Menu > Custom Repositories add https://github.com/Poshy163/HomeAssistant-Sharesight with category set to integration.
2. in `HACS > Integrations > Explore & Add Repositories` search for "Sharesight". 
3. Restart Home Assistant.
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Sharesight".
7. You will be prompted for the API details to your Sharesight Account

## Manual Installation

1. Make a custom_components/sharesight folder in your Home Assistant file system.
2. Copy all the files and folders from this repository into that custom_components/sharesight folder
3. Restart Home Assistant
4. Enable Advanced Mode using Profile (click on your username at the bottom of the navigation column) -> Advanced Mode -> On
5. Log out of HomeAssistant and back in again
6. Setup this integration via `Configuration -> Integrations -> Add -> Sharesight`
7. You will be prompted for the API details to your Sharesight Account