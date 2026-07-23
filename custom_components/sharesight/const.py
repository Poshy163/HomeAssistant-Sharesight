from datetime import timedelta

from homeassistant.const import Platform

# Default coordinator poll interval — users can override via the options flow
# (CONF_SCAN_INTERVAL).  Five minutes balances freshness with the 360/minute
# Sharesight API rate limit and avoids hammering the heavier report endpoints.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
SCAN_INTERVAL = DEFAULT_SCAN_INTERVAL

# Acceptable bounds when setting CONF_SCAN_INTERVAL via options flow (seconds).
MIN_SCAN_INTERVAL_SECONDS = 60
MAX_SCAN_INTERVAL_SECONDS = 60 * 60

# Slow-moving performance windows (financial-year / YTD / one-month) only need
# refreshing occasionally, so the coordinator re-fetches them every Nth poll
# instead of every poll.  12 polls ≈ hourly at the 5-minute default interval.
SLOW_PERIOD_REFRESH_EVERY = 12

# Days of daily portfolio value history requested for the value-trend sensors.
# The sensors only need 30 days; the extra fortnight covers weekends, market
# holidays and any lag in the series without pulling the whole inception-to-
# today history the long-term-statistics backfill uses.
VALUE_TREND_LOOKBACK_DAYS = 45

PLATFORMS = [
    Platform.SENSOR,
    Platform.CALENDAR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.BUTTON,
]
DOMAIN = "sharesight"

CONF_PORTFOLIO_ID = "portfolio_id"
CONF_ACCOUNT_TYPE = "account_type"
# Legacy key from entry VERSION <= 2, read only by async_migrate_entry.
CONF_USE_EDGE = "use_edge_url"
CONF_SCAN_INTERVAL = "scan_interval"
# Backfill the portfolio-value long-term statistics from inception on startup.
CONF_ENABLE_LTS_BACKFILL = "enable_lts_backfill"
DEFAULT_ENABLE_LTS_BACKFILL = True

APP_VERSION = "v2"

# Sharesight serves standard accounts and developer (tester) accounts from two
# independent deployments that do not share an OAuth app registry: a standard
# client_id presented to the developer host is rejected with invalid_client,
# and vice versa.  Developer access is granted by Sharesight, not self-serve.
# Because the account type selects the authorize URL, it must be chosen
# *before* the OAuth redirect is generated, then pinned for the life of the
# entry — it cannot be a post-hoc toggle.
ACCOUNT_STANDARD = "standard"
ACCOUNT_DEVELOPER = "developer"
DEFAULT_ACCOUNT_TYPE = ACCOUNT_STANDARD
ACCOUNT_TYPES = (ACCOUNT_STANDARD, ACCOUNT_DEVELOPER)

AUTHORIZATION_URL = {
    ACCOUNT_STANDARD: "https://api.sharesight.com/oauth2/authorize",
    ACCOUNT_DEVELOPER: "https://edge-api.sharesight.com/oauth2/authorize",
}
TOKEN_URL = {
    ACCOUNT_STANDARD: "https://api.sharesight.com/oauth2/token",
    ACCOUNT_DEVELOPER: "https://edge-api.sharesight.com/oauth2/token",
}
API_URL_BASE = {
    ACCOUNT_STANDARD: "https://api.sharesight.com/api/",
    ACCOUNT_DEVELOPER: "https://edge-api.sharesight.com/api/",
}

# Sharesight documented limits:
#   - 360 requests/minute per consumer app
#   - 3 concurrent "heavy" report endpoints (performance/diversity/valuation)
#   - brute-force lockout for ~10 min after repeated invalid tokens
SHARESIGHT_MAX_REQUESTS_PER_MINUTE = 360
SHARESIGHT_HEAVY_CONCURRENCY = 3
SHARESIGHT_LOCKOUT_COOLDOWN = timedelta(minutes=10)

# Retry the same "optional" endpoint after this cooldown rather than disabling
# it for the lifetime of the process.  Users on plans that briefly return 5xx
# will recover without restarting HA.
OPTIONAL_ENDPOINT_COOLDOWN = timedelta(hours=1)
OPTIONAL_ENDPOINT_MAX_BACKOFF = timedelta(hours=6)
