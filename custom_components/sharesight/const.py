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

# Longer-horizon performance windows (3m / 6m / 1y / 3y / 5y).  Each is one
# calculation-heavy V3 report (with a semantically equivalent V2 fallback), so
# they are opt-in and only ever fetched on the
# slow tier; enabling them adds five requests per hour at the default interval,
# which is negligible against the 360/minute budget but does add ~30 entities.
CONF_ENABLE_EXTENDED_PERFORMANCE = "enable_extended_performance"
DEFAULT_ENABLE_EXTENDED_PERFORMANCE = False

# Per-holding entities are by far the biggest contributor to the entity count
# (roughly 31 per holding). Keep the family opt-in for new entries so adding a
# 25-holding portfolio does not silently create ~775 recorder-backed entities.
# Entry minor-version migration 3.2 stores True for every pre-3.2 entry that
# had no explicit option, preserving the behaviour and history of existing
# installations.
CONF_ENABLE_HOLDING_ENTITIES = "enable_holding_entities"
DEFAULT_ENABLE_HOLDING_ENTITIES = False

# How long the coordinator may keep serving the previous payload before it
# admits defeat.  Below this, a blip is smoothed over and sensors hold their
# last reading; beyond it the poll raises UpdateFailed so entities go
# unavailable rather than displaying hours-old numbers as though they were
# current.  Expressed as a multiple of the poll interval with a floor, so a
# 60-second interval does not give up after four minutes.
MAX_STALE_DATA_POLLS = 4
MIN_STALE_DATA_GRACE = timedelta(minutes=30)

# An optional endpoint's last good payload is replayed while the endpoint is
# parked on its backoff, so its sensors hold instead of dropping to Unknown for
# up to six hours.  Past this age the cached value is dropped: a day-old
# watchlist value is worse than an honest "unknown".
MAX_CARRY_FORWARD_AGE = timedelta(hours=12)

# Delete a per-item device (a holding, market or cash account) automatically
# once its item is gone from the portfolio.  Opt-in, because removing a device
# also removes its entities and their recorded history — the alternative is the
# manual three-dot > Delete, which stays available either way.
CONF_AUTO_REMOVE_STALE_DEVICES = "auto_remove_stale_devices"
DEFAULT_AUTO_REMOVE_STALE_DEVICES = False

# Consecutive successful polls an item must be absent for before its device is
# auto-removed.  Three (≈15 minutes at the default interval) rides out a
# Sharesight blip that returns a short payload without erroring.
STALE_DEVICE_POLL_CONFIRMATIONS = 3

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
# Leave room for config-flow validation, user-triggered services, and requests
# already in flight when the most recent response header was sampled.
SHARESIGHT_REQUESTS_PER_MINUTE_TARGET = 330
SHARESIGHT_HEAVY_CONCURRENCY = 3
SHARESIGHT_LOCKOUT_COOLDOWN = timedelta(minutes=10)

# Sharesight answers 403 with "Too many parallel requests" when the
# 3-concurrent report cap is exceeded.  That is a momentary condition, not an
# outage, so it earns a short pause rather than the lockout cooldown.
SHARESIGHT_RATE_LIMIT_COOLDOWN = timedelta(minutes=1)

# Retry the same "optional" endpoint after this cooldown rather than disabling
# it for the lifetime of the process.  Users on plans that briefly return 5xx
# will recover without restarting HA.
OPTIONAL_ENDPOINT_COOLDOWN = timedelta(hours=1)
OPTIONAL_ENDPOINT_MAX_BACKOFF = timedelta(hours=6)


def portfolio_resource_id(portfolio_id: object, account_type: str) -> str:
    """Stable HA registry namespace for one deployment's portfolio.

    Standard IDs stay byte-identical for backwards compatibility.  Sharesight
    allocates developer-sandbox IDs independently, so those need a prefix to
    avoid merging with a same-number standard portfolio.
    """
    raw = str(portfolio_id)
    return raw if account_type == ACCOUNT_STANDARD else f"developer_{raw}"
