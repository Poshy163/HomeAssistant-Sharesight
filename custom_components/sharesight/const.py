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

PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"

CONF_PORTFOLIO_ID = "portfolio_id"
CONF_USE_EDGE = "use_edge_url"
CONF_SCAN_INTERVAL = "scan_interval"

APP_VERSION = "v2"

AUTHORIZATION_URL = "https://api.sharesight.com/oauth2/authorize"
TOKEN_URL = "https://api.sharesight.com/oauth2/token"
API_URL_BASE = "https://api.sharesight.com/api/"

EDGE_TOKEN_URL = "https://edge-api.sharesight.com/oauth2/token"
EDGE_API_URL_BASE = "https://edge-api.sharesight.com/api/"

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
