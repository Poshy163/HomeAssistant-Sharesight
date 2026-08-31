"""Data update coordinator for the Sharesight integration.

One coordinator serves one portfolio.  Every entity in every platform reads
from its ``data`` dict, so a poll is the only thing that ever talks to
Sharesight - there is no per-entity I/O anywhere in the integration.

The poll is organised in three tiers so the 360-requests-per-minute budget and
the 3-concurrent-report cap are respected without starving the numbers users
actually watch:

* **frequent** - the combined V3 performance report, portfolio list and the
  day/week windows. The first two are critical; a failed period window retains
  its own last good value and does not freeze the otherwise-fresh portfolio.
* **slow** - financial-year / month / YTD windows, the daily value series and
  (opt-in) the 3m/6m/1y/3y/5y windows.  Re-fetched every
  ``SLOW_PERIOD_REFRESH_EVERY`` polls, on a cold start, or when the financial
  year rolls over.  Skipped windows are carried forward.
* **optional** - everything a given API plan or token scope may simply not be
  entitled to (payouts, cash accounts, instruments, benchmark,
  watchlist and tax reports). Each backs off independently on failure, and its
  last good payload is replayed for at most twelve hours.

Degradation is bounded.  When a poll cannot produce fresh data the previous
payload is served, but only for ``MAX_STALE_DATA_POLLS`` polls (with a
``MIN_STALE_DATA_GRACE`` floor); past that the poll raises ``UpdateFailed`` so
entities go unavailable rather than presenting stale numbers as current.
``data_timestamp`` records when the data was really fetched, as distinct from
``last_update_success_time``, which the base class re-stamps on every poll
that returns without raising - including the degraded ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from copy import deepcopy
from datetime import date, datetime, timedelta
import itertools
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from . import analytics
from .api import (
    Endpoint,
    SharesightApiError,
    SharesightRequestGate,
    async_request,
    is_heavy_path,
)
from .const import (
    CONF_ENABLE_EXTENDED_PERFORMANCE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_EXTENDED_PERFORMANCE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CARRY_FORWARD_AGE,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_STALE_DATA_POLLS,
    MIN_SCAN_INTERVAL_SECONDS,
    MIN_STALE_DATA_GRACE,
    OPTIONAL_ENDPOINT_COOLDOWN,
    OPTIONAL_ENDPOINT_MAX_BACKOFF,
    SHARESIGHT_LOCKOUT_COOLDOWN,
    SHARESIGHT_RATE_LIMIT_COOLDOWN,
    SLOW_PERIOD_REFRESH_EVERY,
    VALUE_TREND_LOOKBACK_DAYS,
)
from .dates import (
    financial_year_bounds,
    months_ago,
    trailing_window,
    week_to_date_bounds,
    year_to_date_bounds,
    years_ago,
)

# Home Assistant grew dedicated OAuth token-request exceptions; they subclass
# aiohttp.ClientResponseError, so they MUST be caught ahead of any ClientError
# clause or a permanently revoked refresh token is mistaken for a network blip
# and retried forever instead of prompting reauthentication.
try:  # pragma: no cover - depends on the running core version
    from homeassistant.exceptions import (
        OAuth2TokenRequestError,
        OAuth2TokenRequestReauthError,
        OAuth2TokenRequestTransientError,
    )
except ImportError:  # pragma: no cover - older cores raise ClientResponseError

    class OAuth2TokenRequestError(Exception):  # type: ignore[no-redef]
        """Placeholder so the except clauses stay well-formed."""

    class OAuth2TokenRequestReauthError(OAuth2TokenRequestError):  # type: ignore[no-redef]
        """Placeholder."""

    class OAuth2TokenRequestTransientError(OAuth2TokenRequestError):  # type: ignore[no-redef]
        """Placeholder."""


_LOGGER = logging.getLogger(__name__)


def oauth_response_requires_reauth(error: BaseException) -> bool:
    """Whether an OAuth HTTP response is a permanent credential rejection.

    Home Assistant versions before the dedicated token-request exceptions
    expose these as bare ``aiohttp.ClientResponseError`` instances. Ordinary
    OAuth 4xx responses are permanent, except the explicitly transient timeout
    and rate-limit statuses.
    """
    status = getattr(error, "status", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429)


#: Longer performance windows, as (data key, months back) or (key, years back).
_EXTENDED_MONTH_WINDOWS = (("three-month", 3), ("six-month", 6))
_EXTENDED_YEAR_WINDOWS = (("one-year", 1), ("three-year", 3), ("five-year", 5))

#: Every performance window key, so consumers can enumerate them.
PERIOD_KEYS = (
    "one-day",
    "one-week",
    "one-month",
    "ytd",
    "financial-year",
    "three-month",
    "six-month",
    "one-year",
    "three-year",
    "five-year",
)


def merge_dicts(d1: dict[Any, Any], d2: dict[Any, Any]) -> dict[Any, Any]:
    """Recursively merge d2 into d1, mutating d1 in-place and returning it.

    For overlapping keys with dict values the merge recurses; otherwise d2's
    value wins.  Pure function - does not perform I/O, so it is synchronous.
    """
    for key in set(itertools.chain(d1.keys(), d2.keys())):
        if key in d1 and key in d2 and isinstance(d1[key], dict) and isinstance(d2[key], dict):
            d1[key] = merge_dicts(d1[key], d2[key])
        elif key in d2:
            d1[key] = d2[key]
    return d1


def get_financial_year_dates(
    end_date_str: str | None, today: date | None = None
) -> tuple[str, str]:
    """The financial year containing today, as ``(start, end)`` ISO dates.

    Thin wrapper over :func:`.dates.financial_year_bounds` so the arithmetic
    stays testable without Home Assistant.  ``today`` defaults to the local
    date, which is what the portfolio's own timezone reports against.
    """
    return financial_year_bounds(end_date_str, today or dt_util.now().date())


def _get_scan_interval(entry: ConfigEntry | None) -> timedelta:
    """Pick coordinator scan interval from options, clamped to sane bounds."""
    if entry is None:
        return DEFAULT_SCAN_INTERVAL
    raw = entry.options.get(CONF_SCAN_INTERVAL)
    if raw is None:
        return DEFAULT_SCAN_INTERVAL
    try:
        seconds = int(raw)
    except TypeError, ValueError:
        return DEFAULT_SCAN_INTERVAL
    seconds = max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, seconds))
    return timedelta(seconds=seconds)


class SharesightCoordinator(TimestampDataUpdateCoordinator[dict[str, Any]]):
    """Coordinate polling of the Sharesight API for a single portfolio."""

    # Per-endpoint timeout (seconds).
    _ENDPOINT_TIMEOUT: int = 60

    # Retries for a *transient* token refresh failure before giving up.  A
    # permanent one (revoked grant, wrong client) is never retried.
    _TOKEN_RETRIES: int = 2
    _TOKEN_RETRY_DELAY: float = 3.0

    # Cap on activity events emitted per type per poll, so the first poll
    # after a long outage (which sees a large backlog of "new" records) can
    # never produce an unbounded event payload.  Records beyond the cap stay
    # unseen and surface on a later poll rather than being lost.
    _ACTIVITY_EVENT_CAP: int = 20

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        portfolio_id: Any,
        client: Any,
        oauth_session: Any,
        request_gate: SharesightRequestGate | None = None,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=_get_scan_interval(entry),
        )
        self.entry = entry
        self.sharesight = client
        self.oauth_session = oauth_session
        self.data: dict[str, Any] = {}
        # Preserve successful source payloads independently of ``data``.
        # ``_post_process`` deliberately filters/derives values for entities;
        # the response service needs the pre-derived shapes when diagnosing an
        # upstream contract without issuing another request.
        self._raw_responses: dict[str, Any] = {}
        self.portfolio_id = portfolio_id
        self.current_date: date = dt_util.now().date()

        # {platform: {translation_key: icon}} resolved from icons.json by
        # __init__.async_setup_entry before the platforms are forwarded (see
        # icons.py).  Empty until then, and harmlessly empty if the load fails.
        self.entity_icons: dict[str, dict[str, str]] = {}

        # Cooldowns (monotonic timestamps) for optional endpoints, keyed by
        # Endpoint.cooldown_key.  {"next_retry": float, "backoff": timedelta}.
        self._optional_endpoint_cooldowns: dict[str, dict[str, Any]] = {}
        # Slow-moving optional endpoints that failed transiently before ever
        # producing (or while lacking) a usable carried-forward source. These
        # bypass their normal refresh modulus on the next eligible poll.
        self._optional_retry_keys: set[str] = set()
        self._cash_tx_account_cooldowns: dict[int, dict[str, Any]] = {}
        self._unsupported_endpoints: set[str] = set()
        # V3 route -> equivalent V2 route, learned only from Sharesight's
        # explicit "Version ... not supported" response. This avoids probing
        # the same rejected version on every subsequent poll.
        self._fallback_routes: set[str] = set()

        # Per-account transaction snapshots let one failed cash account retain
        # its own last good rows while other accounts continue updating.
        self._cash_transactions_by_account: dict[int, list[dict[str, Any]]] = {}

        # Last good payload per optional key, so a parked endpoint's sensors
        # hold their reading instead of dropping out.  {key: (payload, when)}.
        self._carry_forward: dict[str, tuple[Any, float]] = {}

        # Official concurrency/rate limits are scoped to the OAuth consumer
        # app, so every portfolio using that credential shares this gate.
        self._request_gate = request_gate or SharesightRequestGate()
        self._heavy_request_semaphore = self._request_gate.heavy_semaphore
        self._request_semaphore = self._request_gate.request_semaphore

        # Financial year caching - seeded on first successful startup fetch.
        self.start_financial_year: str = ""
        self.end_financial_year: str = ""
        self._portfolio_detail: dict[str, Any] = {}

        # Tiered polling.  Incremented once per genuinely successful poll.
        self._poll_count: int = 0
        self._slow_window_fy_bounds: tuple[str, str] | None = None
        # Transient slow-tier failures with no usable carried-forward payload.
        # Retry only these keys on the next eligible poll instead of waiting
        # for the next hourly cadence or repeating every successful sibling.
        self._slow_retry_keys: set[str] = set()

        # Degradation tracking.  ``data_timestamp`` is when the payload was
        # really fetched; the base class's last_update_success_time is stamped
        # on every non-raising poll, degraded ones included, so it cannot
        # answer "how old is this number?".
        self.data_timestamp: datetime | None = None
        self._degraded_polls: int = 0
        self.degraded_reason: str | None = None

        # Log de-duplication: {key: last message}.  The first (or a changed)
        # failure logs at WARNING, identical repeats at DEBUG, and recovery
        # logs once at INFO.  Without this a sustained outage wrote ~20
        # identical WARNING lines per poll, forever.
        self._logged_failures: dict[str, str] = {}
        # Latest plan holding-limit metadata observed on a report response.
        # None means Sharesight has not supplied the header pair this run.
        self.holding_limit: dict[str, int] | None = None

        # Activity diff.  "Seen" keys per record type so only genuinely new
        # records fire events; seeded silently on the first successful poll.
        self._seen_trade_ids: set[Any] = set()
        self._seen_payout_ids: set[Any] = set()
        self._seen_upcoming_ids: set[Any] = set()
        self._seen_cash_tx_ids: set[Any] = set()
        # Optional activity feeds can be absent independently on the first
        # otherwise-successful poll.  Each source therefore needs its own
        # baseline: when a missing feed first recovers, seed its existing
        # records silently instead of announcing the entire history as new.
        self._activity_sources_seeded: set[str] = set()
        self._seen_holding_symbols: set[str] = set()
        self._holdings_snapshot_seeded: bool = False
        self._seen_daily_close_date: str | None = None
        self._activity_seeded: bool = False
        # Monotonic id stamped on every staged activity_events batch so the
        # event entity can distinguish a freshly-diffed poll from a
        # keep-last-good cached return.
        self._activity_seq: int = 0

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    @property
    def portfolio_currency(self) -> str:
        """The currency this portfolio's figures are denominated in.

        Single source of truth for every monetary unit in the integration.
        Resolution order matters:

        1. ``report.currency.code`` - the currency the numbers being published
           are actually rendered in (it follows the user's report-currency
           setting, which can differ from the portfolio's own currency).
        2. The portfolio detail fetched for *this* entry.
        3. The matching entry in the account-wide portfolio list.

        The old code read ``portfolios[0].currency_code``, i.e. whichever
        portfolio the account listed first - the wrong currency for every
        entry but the first on a multi-portfolio account.
        """
        return self._portfolio_currency_for(self.data or {})

    def _portfolio_currency_for(self, data: dict[str, Any]) -> str:
        """Resolve the currency for a specific in-flight coordinator payload."""
        report = data.get("report")
        if isinstance(report, dict):
            currency = report.get("currency")
            if isinstance(currency, dict) and currency.get("code"):
                return str(currency["code"])
        if self._portfolio_detail.get("currency_code"):
            return str(self._portfolio_detail["currency_code"])
        entry = self._own_portfolio_entry(data.get("portfolios"))
        if entry and entry.get("currency_code"):
            return str(entry["currency_code"])
        # Never guess a currency for statistics-bearing sensors. The first
        # numeric sample fixes recorder metadata permanently; publishing an
        # AUD portfolio as USD during a malformed/cold payload would suppress
        # statistics as soon as the real currency arrives. A normal poll has
        # both the required report and portfolio list, so absence here is a
        # malformed critical response and should degrade the poll.
        raise ValueError("Sharesight payload did not identify the report currency")

    def _own_portfolio_entry(self, portfolios: Any) -> dict[str, Any] | None:
        """This entry's row in the account-wide ``GET /portfolios`` list.

        The list covers every portfolio the account can see, so indexing it at
        [0] - as the financial-year refresh and the currency lookup both used
        to - picks an arbitrary other portfolio's settings.
        """
        if not isinstance(portfolios, list):
            return None
        for portfolio in portfolios:
            if isinstance(portfolio, dict) and str(portfolio.get("id")) == str(self.portfolio_id):
                return portfolio
        return None

    @staticmethod
    def _normalise_portfolio_detail(detail: dict[str, Any]) -> dict[str, Any]:
        """Canonicalise the legacy V2 inception date used in later calls.

        V3 uses ISO ``YYYY-MM-DD``. V2's documented response instead uses
        ``DD Mon YYYY`` (for example ``01 Jan 2009``). The inception date later
        becomes a performance/value-series query parameter, so preserve all
        other fields but convert this one when its format is recognised.
        """
        normalised = dict(detail)
        raw_inception = normalised.get("inception_date")
        if not isinstance(raw_inception, str) or not raw_inception:
            return normalised

        parsed = dt_util.parse_date(raw_inception)
        if parsed is None:
            try:
                parsed = datetime.strptime(raw_inception, "%d %b %Y").date()
            except ValueError:
                return normalised
        normalised["inception_date"] = parsed.isoformat()
        return normalised

    def _portfolio_today(self) -> date:
        """Current calendar day in the portfolio's reporting timezone."""
        timezone_name = self._portfolio_detail.get("tz_name") or self._portfolio_detail.get(
            "portfolio_tz_name"
        )
        if timezone_name:
            try:
                return dt_util.utcnow().astimezone(ZoneInfo(str(timezone_name))).date()
            except ZoneInfoNotFoundError, ValueError, TypeError:
                self._log_failure(
                    "portfolio_timezone",
                    "Sharesight portfolio %s returned unknown timezone %r; using Home Assistant time",
                    self.portfolio_id,
                    timezone_name,
                )
        return dt_util.now().date()

    def portfolio_start_of_day(self, day: date) -> datetime:
        """Return portfolio-local midnight for a report cycle boundary."""
        timezone_name = self._portfolio_detail.get("tz_name") or self._portfolio_detail.get(
            "portfolio_tz_name"
        )
        if timezone_name:
            try:
                return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(str(timezone_name)))
            except ZoneInfoNotFoundError, ValueError, TypeError:
                pass
        return dt_util.start_of_local_day(day)

    @property
    def data_age(self) -> timedelta | None:
        """How long ago the current payload was actually fetched."""
        if self.data_timestamp is None:
            return None
        return dt_util.utcnow() - self.data_timestamp

    @property
    def is_degraded(self) -> bool:
        """Whether the last poll served carried-over rather than fresh data."""
        return self._degraded_polls > 0

    @property
    def _lockout_until(self) -> float:
        return self._request_gate.lockout_until

    @_lockout_until.setter
    def _lockout_until(self, value: float) -> None:
        # Tests that construct with __new__ still get a complete local gate.
        if not hasattr(self, "_request_gate"):
            self._request_gate = SharesightRequestGate()
        self._request_gate.lockout_until = value

    @property
    def _lockout_reason(self) -> str | None:
        return self._request_gate.lockout_reason

    @_lockout_reason.setter
    def _lockout_reason(self, value: str | None) -> None:
        if not hasattr(self, "_request_gate"):
            self._request_gate = SharesightRequestGate()
        self._request_gate.lockout_reason = value

    @property
    def lockout_seconds_remaining(self) -> int:
        """Seconds left on the global API cooldown (0 when not in one)."""
        return max(0, int(self._lockout_until - time.monotonic()))

    def _stale_data_limit(self) -> timedelta:
        """How long degraded operation may continue before giving up."""
        interval = self.update_interval or DEFAULT_SCAN_INTERVAL
        return max(interval * MAX_STALE_DATA_POLLS, MIN_STALE_DATA_GRACE)

    # ------------------------------------------------------------------
    # De-duplicated logging
    # ------------------------------------------------------------------

    def _log_failure(self, key: str, message: str, *args: Any) -> None:
        """Log a failure once at WARNING; identical repeats go to DEBUG."""
        rendered = message % args if args else message
        if self._logged_failures.get(key) == rendered:
            _LOGGER.debug(message, *args)
            return
        self._logged_failures[key] = rendered
        _LOGGER.warning(message, *args)

    def _log_recovery(self, key: str, message: str, *args: Any) -> None:
        """Log a recovery at INFO, but only if a failure was logged for it."""
        if self._logged_failures.pop(key, None) is not None:
            _LOGGER.info(message, *args)

    # ------------------------------------------------------------------
    # OAuth token handling
    # ------------------------------------------------------------------

    async def _refresh_token_with_retries(self) -> str:
        """Ensure a valid access token, retrying only transient failures.

        Home Assistant raises ``OAuth2TokenRequestReauthError`` for a 4xx from
        the token endpoint (a revoked grant, a deleted application credential)
        and ``OAuth2TokenRequestTransientError`` for a 429/5xx.  Both subclass
        ``aiohttp.ClientResponseError``, so a generic ``except ClientError``
        catches the permanent one too - which is exactly what used to happen,
        retrying a revoked token forever and never prompting reauth.  The
        permanent case is therefore matched first and converted straight to
        ``ConfigEntryAuthFailed``.
        """
        last_error: Exception | None = None
        for attempt in range(self._TOKEN_RETRIES + 1):
            try:
                await self.oauth_session.async_ensure_token_valid()
                token = self.oauth_session.token or {}
                access_token = token.get("access_token")
                if not access_token:
                    raise ConfigEntryAuthFailed(
                        "Sharesight returned no access token; re-authentication is required"
                    )
                return str(access_token)
            except ConfigEntryAuthFailed:
                raise
            except OAuth2TokenRequestReauthError as err:
                raise ConfigEntryAuthFailed(
                    f"Sharesight rejected the stored credentials: {err}"
                ) from err
            except OAuth2TokenRequestTransientError as err:
                last_error = err
                if attempt < self._TOKEN_RETRIES:
                    _LOGGER.debug(
                        "Sharesight token refresh attempt %s/%s failed (%s: %s), retrying in %ss",
                        attempt + 1,
                        self._TOKEN_RETRIES + 1,
                        type(err).__name__,
                        err,
                        self._TOKEN_RETRY_DELAY,
                    )
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise
            except (OAuth2TokenRequestError, aiohttp.ClientResponseError) as err:
                if oauth_response_requires_reauth(err):
                    raise ConfigEntryAuthFailed(
                        f"Sharesight rejected the stored credentials: {err}"
                    ) from err
                last_error = err
                if attempt < self._TOKEN_RETRIES:
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise
            except (aiohttp.ClientError, OSError, TimeoutError) as err:
                last_error = err
                if attempt < self._TOKEN_RETRIES:
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise
            except HomeAssistantError as err:
                # Older cores surface everything as a bare HomeAssistantError,
                # so fall back to recognising the permanent OAuth error codes
                # in the message.
                message = str(err).lower()
                if any(
                    marker in message
                    for marker in ("invalid_grant", "invalid_client", "access_denied")
                ):
                    raise ConfigEntryAuthFailed(f"Sharesight authentication failed: {err}") from err
                last_error = err
                if attempt < self._TOKEN_RETRIES:
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise

        raise UpdateFailed(f"Exhausted Sharesight token refresh retries: {last_error}")

    # ------------------------------------------------------------------
    # Low-level request plumbing
    # ------------------------------------------------------------------

    def _register_lockout(self, duration: timedelta, reason: str) -> None:
        """Suppress further API calls until ``duration`` from now."""
        deadline = time.monotonic() + duration.total_seconds()
        if deadline <= self._lockout_until:
            return
        self._lockout_until = deadline
        self._lockout_reason = reason
        self._log_failure(
            "lockout",
            "Sharesight API cooldown active for %s: %s",
            duration,
            reason,
        )

    def _in_lockout(self) -> bool:
        """Whether we are currently inside a global cooldown window."""
        if time.monotonic() < self._lockout_until:
            return True
        if self._lockout_reason is not None:
            self._lockout_reason = None
            self._log_recovery("lockout", "Sharesight API cooldown has expired")
        return False

    def _note_api_error(self, err: SharesightApiError) -> None:
        """Apply the global back-pressure an API error calls for."""
        if err.is_rate_limited:
            duration = SHARESIGHT_RATE_LIMIT_COOLDOWN
            if err.retry_after:
                duration = max(duration, timedelta(seconds=float(err.retry_after)))
            self._register_lockout(duration, err.detail)
        elif err.is_lockout:
            # Sharesight reuses the plain 401 body for a merely-expired token,
            # so only escalate to the ten-minute lockout once the token has
            # already been refreshed this poll - which it always has, since
            # every request path refreshes first.
            self._register_lockout(SHARESIGHT_LOCKOUT_COOLDOWN, err.detail)

    async def _call(self, endpoint: Endpoint, access_token: str) -> Any:
        """One request with the concurrency controls and lockout guard applied."""
        async with self._request_semaphore:
            if endpoint.heavy or is_heavy_path(endpoint.path):
                async with self._heavy_request_semaphore:
                    return await self._guarded_dispatch(endpoint, access_token)
            return await self._guarded_dispatch(endpoint, access_token)

    async def _guarded_dispatch(self, endpoint: Endpoint, access_token: str) -> Any:
        """Re-check shared cooldown/budget after queued semaphore waits."""
        if self._in_lockout():
            raise SharesightApiError(
                endpoint,
                reason=(
                    f"suppressed by shared cooldown, {self.lockout_seconds_remaining}s remaining"
                ),
                transport=True,
            )
        self._reserve_request(endpoint)
        return await self._dispatch(endpoint, access_token)

    def _reserve_request(self, endpoint: Endpoint) -> None:
        """Reserve one real HTTP call against the shared minute budget."""
        if retry_after := self._request_gate.reserve():
            reason = "shared Sharesight request safety budget exhausted"
            self._register_lockout(timedelta(seconds=retry_after), reason)
            raise SharesightApiError(
                endpoint,
                status=429,
                code="local_budget",
                reason=reason,
                retry_after=retry_after,
            )

    @staticmethod
    def _fallback_route_key(endpoint: Endpoint) -> str:
        """Stable identity for a version fallback, independent of parameters."""
        return f"{endpoint.version}/{endpoint.path}->{endpoint.fallback_version}"

    @staticmethod
    def _is_version_mismatch(error: SharesightApiError) -> bool:
        """Whether Sharesight explicitly rejected the requested API version."""
        reason = (error.reason or "").lower()
        return error.status == 406 and "version" in reason and "not supported" in reason

    @staticmethod
    def _fallback_endpoint(endpoint: Endpoint) -> Endpoint:
        """Equivalent endpoint using the configured fallback API version."""
        fallback_version = str(endpoint.fallback_version)
        path = endpoint.path
        params = dict(endpoint.params) if endpoint.params is not None else None
        # Every public V2 read route used by this integration is documented
        # with the Rails ``.json`` suffix. Sharesight currently accepts the
        # extensionless aliases, but using the canonical spelling prevents a
        # proxy/version deployment from rejecting an otherwise valid fallback.
        if fallback_version == "v2":
            canonical_path = path.removesuffix(".json")
            if canonical_path == "portfolios" or (
                canonical_path.startswith("portfolios/") and canonical_path.count("/") == 1
            ):
                # The public V2 list/show routes document no query controls.
                params = None
            elif canonical_path.endswith("/performance") and params is not None:
                # V3-only report controls are rejected or ignored by V2. Keep
                # only parameters explicitly documented for both generations.
                v2_performance_params = {
                    "start_date",
                    "end_date",
                    "consolidated",
                    "include_sales",
                    "grouping",
                    "custom_group_id",
                }
                params = {
                    name: value for name, value in params.items() if name in v2_performance_params
                }
            if not path.endswith(".json"):
                path = f"{path}.json"
        return Endpoint(
            fallback_version,
            path,
            params,
            endpoint.key,
            heavy=endpoint.heavy,
            refresh_every=endpoint.refresh_every,
        )

    async def _dispatch(self, endpoint: Endpoint, access_token: str) -> Any:
        fallback_key = self._fallback_route_key(endpoint)
        if endpoint.fallback_version and fallback_key in self._fallback_routes:
            endpoint = self._fallback_endpoint(endpoint)

        try:
            result = await async_request(
                self.sharesight, endpoint, access_token, self._ENDPOINT_TIMEOUT
            )
        except SharesightApiError as err:
            self._observe_response_headers(err.headers)
            if (
                endpoint.fallback_version
                and self._is_version_mismatch(err)
                and endpoint.fallback_version != endpoint.version
            ):
                fallback = self._fallback_endpoint(endpoint)
                # The fallback is a second real HTTP call and must count
                # against the same application-scoped minute budget.
                self._reserve_request(fallback)
                try:
                    fallback_result = await async_request(
                        self.sharesight,
                        fallback,
                        access_token,
                        self._ENDPOINT_TIMEOUT,
                    )
                except SharesightApiError as fallback_err:
                    self._observe_response_headers(fallback_err.headers)
                    self._note_api_error(fallback_err)
                    raise
                self._observe_response_headers(fallback_result.headers)
                self._fallback_routes.add(fallback_key)
                return fallback_result.data
            self._note_api_error(err)
            raise
        self._observe_response_headers(result.headers)
        return result.data

    def _observe_response_headers(self, headers: dict[str, str]) -> None:
        """Apply request-budget and plan-limit response metadata."""
        self._request_gate.observe_headers(headers)
        normalised = {str(key).lower(): str(value) for key, value in headers.items()}
        limit_raw = normalised.get("x-holdinglimit-limit")
        total_raw = normalised.get("x-holdinglimit-total")
        if limit_raw is None or total_raw is None:
            return
        try:
            limit = int(limit_raw)
            total = int(total_raw)
        except ValueError:
            return
        if limit < 0 or total < 0:
            return

        self.holding_limit = {"limit": limit, "total": total}
        issue_id = f"holding_limit_{self.entry.entry_id}"
        if total > limit:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                learn_more_url=(
                    "https://github.com/Poshy163/HomeAssistant-Sharesight#known-limitations"
                ),
                severity=ir.IssueSeverity.WARNING,
                translation_key="holding_limit",
                translation_placeholders={"limit": str(limit), "total": str(total)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _gather(
        self, endpoints: Iterable[Endpoint], access_token: str
    ) -> list[tuple[Endpoint, Any]]:
        """Run endpoints concurrently, pairing each with its result or error."""
        endpoints = list(endpoints)
        results = await asyncio.gather(
            *(self._call(endpoint, access_token) for endpoint in endpoints),
            return_exceptions=True,
        )
        return list(zip(endpoints, results, strict=True))

    # ------------------------------------------------------------------
    # Optional endpoint cooldown bookkeeping
    # ------------------------------------------------------------------

    def _endpoint_on_cooldown(self, key: str) -> bool:
        info = self._optional_endpoint_cooldowns.get(key)
        return bool(info) and time.monotonic() < info["next_retry"]

    def _note_optional_failure(self, key: str, error: SharesightApiError | None = None) -> None:
        """Apply capability-aware retry policy to one optional endpoint."""
        if error is not None:
            reason = (error.reason or "").lower()
            if error.status == 404 or (
                error.status == 406 and "version" in reason and "not supported" in reason
            ):
                self._unsupported_endpoints.add(key)
                self._optional_endpoint_cooldowns.pop(key, None)
                self._optional_retry_keys.discard(key)
                self._log_failure(
                    f"unsupported:{key}",
                    "Sharesight endpoint permanently disabled for this entry: %s",
                    error.detail,
                )
                return
            # Rate limits and lockouts use the shared gate. Transport and server
            # failures remain eligible for the missing-source fast-retry policy
            # instead of being hidden behind the one-hour endpoint backoff.
            if error.is_rate_limited or error.transport or error.is_retryable or error.is_lockout:
                return
        self._optional_retry_keys.discard(key)
        info = self._optional_endpoint_cooldowns.get(key)
        backoff = (
            OPTIONAL_ENDPOINT_COOLDOWN
            if info is None
            else min(info["backoff"] * 2, OPTIONAL_ENDPOINT_MAX_BACKOFF)
        )
        self._optional_endpoint_cooldowns[key] = {
            "next_retry": time.monotonic() + backoff.total_seconds(),
            "backoff": backoff,
        }

    def _note_optional_success(self, key: str) -> None:
        self._optional_endpoint_cooldowns.pop(key, None)
        self._unsupported_endpoints.discard(key)
        self._optional_retry_keys.discard(key)

    def _cash_tx_on_cooldown(self, account_id: int) -> bool:
        info = self._cash_tx_account_cooldowns.get(account_id)
        return bool(info) and time.monotonic() < info["next_retry"]

    def _note_cash_tx_failure(self, account_id: int) -> None:
        info = self._cash_tx_account_cooldowns.get(account_id)
        backoff = (
            OPTIONAL_ENDPOINT_COOLDOWN
            if info is None
            else min(info["backoff"] * 2, OPTIONAL_ENDPOINT_MAX_BACKOFF)
        )
        self._cash_tx_account_cooldowns[account_id] = {
            "next_retry": time.monotonic() + backoff.total_seconds(),
            "backoff": backoff,
        }

    def _note_cash_tx_success(self, account_id: int) -> None:
        self._cash_tx_account_cooldowns.pop(account_id, None)

    # ------------------------------------------------------------------
    # Carry-forward of parked optional payloads
    # ------------------------------------------------------------------

    def _remember(self, key: str, payload: Any) -> None:
        self._carry_forward[key] = (payload, time.monotonic())

    def _replay_missing(self, combined: dict[str, Any]) -> list[str]:
        """Restore any remembered payload the poll did not produce.

        Without this, every optional endpoint's key simply vanished from the
        payload the moment it parked on its 1-6 hour backoff, dropping its
        sensors to Unknown while the integration reported a perfectly healthy
        update. Cached values older than ``MAX_CARRY_FORWARD_AGE`` are dropped
        instead; stale optional data is worse than an honest unknown.
        """
        now = time.monotonic()
        replayed: list[str] = []
        for key, (payload, when) in list(self._carry_forward.items()):
            if now - when > MAX_CARRY_FORWARD_AGE.total_seconds():
                del self._carry_forward[key]
                continue
            if key not in combined:
                combined[key] = payload
                replayed.append(key)
        return replayed

    # ------------------------------------------------------------------
    # Holdings hygiene
    # ------------------------------------------------------------------

    @staticmethod
    def _open_positions(holdings: Any) -> list[dict[str, Any]]:
        """Drop holdings the user has sold out of from a holdings list.

        Sharesight asks the performance report for open positions only, but a
        sale that doesn't net to exactly zero leaves the holding behind as a
        dust row (a quantity like -4e-05, no value, ``valid_position: false``).
        Left in, that row keeps a sold holding's device looking live.
        """
        open_holdings: list[dict[str, Any]] = []
        closed: list[str] = []
        for holding in holdings or []:
            if not isinstance(holding, dict):
                continue
            if analytics.is_open_position(holding):
                open_holdings.append(holding)
            else:
                closed.append(str(analytics.holding_symbol(holding) or "?"))
        if closed:
            _LOGGER.debug(
                "Ignoring %d sold-out holding(s) still listed by Sharesight: %s",
                len(closed),
                ", ".join(sorted(closed)),
            )
        return open_holdings

    # ------------------------------------------------------------------
    # Endpoint plan
    # ------------------------------------------------------------------

    def _performance_params(self, **extra: Any) -> dict[str, Any]:
        """Shared parameters for every performance report.

        ``grouping=market`` is pinned rather than left to the server, which
        otherwise falls back to the account's saved report preference - so the
        per-market devices this integration builds would silently become
        per-industry devices if the user changed a setting in the Sharesight
        web app.
        """
        params: dict[str, Any] = {"grouping": "market"}
        params.update(extra)
        return params

    def _required_endpoints(self, today: date) -> list[Endpoint]:
        """Endpoints whose failure degrades the poll."""
        pid = self.portfolio_id
        week_start, week_end = week_to_date_bounds(today)
        return [
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    start_date=today.isoformat(),
                    end_date=today.isoformat(),
                    include_sales="true",
                ),
                "one-day",
                heavy=True,
                fallback_version="v2",
            ),
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    start_date=week_start, end_date=week_end, include_sales="true"
                ),
                "one-week",
                heavy=True,
                fallback_version="v2",
            ),
            Endpoint("v3", "portfolios", None, None, fallback_version="v2"),
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(include_limited="true", report_combined="true"),
                None,
                heavy=True,
            ),
        ]

    def _slow_endpoints(self, today: date) -> list[Endpoint]:
        """Windows and series that move slowly enough to refresh hourly."""
        pid = self.portfolio_id
        month_start, month_end = trailing_window(today, 30)
        year_start, year_end = year_to_date_bounds(today)
        inception = self._portfolio_detail.get("inception_date")

        endpoints = [
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    start_date=self.start_financial_year,
                    end_date=min(self.end_financial_year, today.isoformat()),
                    include_sales="true",
                ),
                "financial-year",
                heavy=True,
                fallback_version="v2",
            ),
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    start_date=month_start, end_date=month_end, include_sales="true"
                ),
                "one-month",
                heavy=True,
                fallback_version="v2",
            ),
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    start_date=year_start, end_date=year_end, include_sales="true"
                ),
                "ytd",
                heavy=True,
                fallback_version="v2",
            ),
            # Lifetime performance INCLUDING fully-sold positions.  The V3
            # combined report omits include_sales, so realised gains from
            # exited holdings are missing from it.  This is the public-tier
            # replacement for the internal-scoped /totals endpoint, which a
            # standard API token generally cannot reach at all.
            Endpoint(
                "v3",
                f"portfolios/{pid}/performance",
                self._performance_params(
                    include_sales="true",
                    include_limited="true",
                    start_date=inception or year_start,
                    end_date=today.isoformat(),
                ),
                "all_time",
                heavy=True,
                fallback_version="v2",
            ),
            # Daily portfolio value series feeding the trend, drawdown and
            # volatility sensors.  One point per day, so 5-minute resolution
            # would be wasted; bounded to keep the payload small.
            Endpoint(
                "v3",
                f"portfolios/{pid}/portfolio_value_data.json",
                {"start_date": trailing_window(today, VALUE_TREND_LOOKBACK_DAYS)[0]},
                "value_series",
            ),
        ]

        if self.entry.options.get(
            CONF_ENABLE_EXTENDED_PERFORMANCE, DEFAULT_ENABLE_EXTENDED_PERFORMANCE
        ):
            for key, months in _EXTENDED_MONTH_WINDOWS:
                endpoints.append(
                    Endpoint(
                        "v3",
                        f"portfolios/{pid}/performance",
                        self._performance_params(
                            start_date=months_ago(today, months),
                            end_date=today.isoformat(),
                            include_sales="true",
                        ),
                        key,
                        heavy=True,
                        fallback_version="v2",
                    )
                )
            for key, years in _EXTENDED_YEAR_WINDOWS:
                start = years_ago(today, years)
                if inception and start < str(inception):
                    start = str(inception)
                endpoints.append(
                    Endpoint(
                        "v3",
                        f"portfolios/{pid}/performance",
                        self._performance_params(
                            start_date=start,
                            end_date=today.isoformat(),
                            include_sales="true",
                        ),
                        key,
                        heavy=True,
                        fallback_version="v2",
                    )
                )
        return endpoints

    def _optional_endpoints(self, today: date) -> list[Endpoint]:
        """Endpoints a given plan or token scope may not be entitled to."""
        pid = self.portfolio_id
        inception = self._portfolio_detail.get("inception_date")
        endpoints = [
            Endpoint("v2", f"portfolios/{pid}/payouts.json", None, "payouts", refresh_every=12),
            # Announced-but-not-yet-paid dividends.  The default payouts call
            # only covers inception -> today, so future payouts never appear
            # in it; this forward window feeds the next-dividend sensors and
            # the dividend calendar.
            Endpoint(
                "v2",
                f"portfolios/{pid}/payouts.json",
                {
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=365)).isoformat(),
                    "use_date": "ex_date",
                },
                "upcoming_payouts",
                refresh_every=12,
            ),
            Endpoint("v2", f"portfolios/{pid}/trades.json", None, "trades"),
            Endpoint("v2", "cash_accounts.json", None, "cash_accounts_v2", refresh_every=12),
            Endpoint(
                "v3", f"portfolios/{pid}/user_setting", None, "user_setting", refresh_every=12
            ),
            Endpoint("v2", "user_instruments.json", None, "user_instruments", refresh_every=12),
            # Benchmark performance.  Only returns data when the user has set a
            # benchmark on the portfolio, and only this endpoint carries the
            # maximum-drawdown figures.  interest_method is matched to the
            # portfolio's own setting so the benchmark's percentages are
            # computed on the same basis as the portfolio's.
            Endpoint(
                "v3",
                f"portfolios/{pid}/benchmark.json",
                {
                    "start_date": inception or year_to_date_bounds(today)[0],
                    "end_date": today.isoformat(),
                    "interest_method": self._portfolio_detail.get("interest_method") or "simple",
                },
                None,
                heavy=True,
                refresh_every=12,
            ),
            Endpoint("v2", "my_user.json", None, "my_user", refresh_every=12),
            # The mobile watchlist route is retained because the supplied token
            # successfully served it. Markets/news/FX are intentionally absent:
            # the same live token proved their advertised versions are rejected
            # with a permanent 406 on every retry.
            Endpoint("v3", "watchlist.json", None, "watchlist", refresh_every=12),
        ]

        # Capital gains tax reports are only available for Australian
        # portfolios (the API rejects them otherwise).
        if str(self._portfolio_detail.get("country_code", "")).upper() == "AU":
            endpoints.extend(
                [
                    Endpoint(
                        "v2",
                        f"portfolios/{pid}/capital_gains.json",
                        {
                            "start_date": self.start_financial_year,
                            "end_date": min(self.end_financial_year, today.isoformat()),
                        },
                        "capital_gains",
                        refresh_every=12,
                    ),
                    Endpoint(
                        "v2",
                        f"portfolios/{pid}/unrealised_cgt.json",
                        {"balance_date": today.isoformat()},
                        "unrealised_cgt",
                        refresh_every=12,
                    ),
                ]
            )
        return endpoints

    # ------------------------------------------------------------------
    # One-time setup
    # ------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Seed portfolio detail and financial-year bounds once at setup.

        DataUpdateCoordinator calls this exactly once, inside
        ``async_config_entry_first_refresh`` and before the first
        ``_async_update_data``.  A transient failure raises ``UpdateFailed`` so
        the base surfaces a retriable ``ConfigEntryNotReady``; a 404 (portfolio
        deleted or access lost) raises ``ConfigEntryError`` with replacement
        guidance rather than treating a missing portfolio as bad OAuth.
        """
        access_token = await self._refresh_token_with_retries()
        endpoint = Endpoint(
            "v3",
            f"portfolios/{self.portfolio_id}",
            None,
            None,
            fallback_version="v2",
        )
        try:
            local_data = await self._call(endpoint, access_token)
        except SharesightApiError as err:
            if err.is_not_found:
                raise ConfigEntryError(
                    f"Sharesight portfolio {self.portfolio_id} is no longer "
                    "accessible. Add the replacement portfolio as a new entry."
                ) from err
            if err.is_unauthorised and not err.is_lockout:
                raise ConfigEntryAuthFailed(
                    f"Sharesight rejected the access token: {err.detail}"
                ) from err
            raise UpdateFailed(f"Sharesight startup fetch failed: {err.detail}") from err

        if not isinstance(local_data, dict):
            raise UpdateFailed(
                f"Sharesight startup fetch returned {type(local_data).__name__}, expected an object"
            )

        # V3 wraps the result in {"portfolio": {...}}; the documented V2
        # fallback returns the portfolio object bare.
        detail = local_data.get("portfolio")
        if not isinstance(detail, dict):
            detail = local_data
        detail = self._normalise_portfolio_detail(detail)
        if str(detail.get("id")) != str(self.portfolio_id):
            raise UpdateFailed(
                "Sharesight startup fetch did not identify the configured "
                f"portfolio {self.portfolio_id}"
            )
        self._portfolio_detail = detail
        self.current_date = self._portfolio_today()
        self.start_financial_year, self.end_financial_year = get_financial_year_dates(
            self._portfolio_detail.get("financial_year_end"), self.current_date
        )
        _LOGGER.debug(
            "Sharesight portfolio %s: currency=%s country=%s financial year %s..%s",
            self.portfolio_id,
            self._portfolio_detail.get("currency_code"),
            self._portfolio_detail.get("country_code"),
            self.start_financial_year,
            self.end_financial_year,
        )

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    def _degrade(self, reason: str) -> dict[str, Any]:
        """Serve the previous payload, or give up once it is too stale.

        Returning stale data indefinitely is what let the integration look
        healthy through a multi-hour outage.  Past the staleness limit this
        raises so ``last_update_success`` finally flips and every entity goes
        unavailable.
        """
        limit = self._stale_data_limit()
        age = self.data_age
        if self.data and (age is None or age <= limit):
            self._degraded_polls += 1
            self.degraded_reason = reason
            self._log_failure(
                "degraded",
                "Sharesight poll degraded (%s); serving data fetched %s ago (giving up after %s)",
                reason,
                age or timedelta(0),
                limit,
            )
            return self.data
        raise UpdateFailed(reason)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest data from Sharesight."""
        if self._in_lockout():
            return self._degrade(
                f"API cooldown active, {self.lockout_seconds_remaining}s remaining"
            )

        try:
            access_token = await self._refresh_token_with_retries()
        except ConfigEntryAuthFailed:
            raise
        except (
            aiohttp.ClientError,
            OSError,
            TimeoutError,
            HomeAssistantError,
        ) as token_error:
            return self._degrade(f"token refresh failed: {token_error}")

        today = self._portfolio_today()
        self.current_date = today
        new_fy_bounds = get_financial_year_dates(
            self._portfolio_detail.get("financial_year_end"), today
        )
        if new_fy_bounds != (self.start_financial_year, self.end_financial_year):
            self.start_financial_year, self.end_financial_year = new_fy_bounds

        combined: dict[str, Any] = {}
        failures: list[str] = []
        critical_failed = False
        auth_failure: SharesightApiError | None = None

        # --- Required tier --------------------------------------------
        required = self._required_endpoints(today)
        _LOGGER.debug("Requesting %s required Sharesight endpoints", len(required))
        for endpoint, result in await self._gather(required, access_token):
            is_critical = endpoint.key is None
            if isinstance(result, SharesightApiError):
                failures.append(result.detail)
                if result.is_not_found and is_critical:
                    raise ConfigEntryError(
                        f"Sharesight portfolio {self.portfolio_id} is no longer "
                        "accessible. Add the replacement portfolio as a new entry."
                    )
                if result.is_unauthorised and not result.is_lockout:
                    auth_failure = result
                self._log_failure(
                    f"required:{endpoint.cooldown_key}",
                    "Sharesight request failed: %s",
                    result.detail,
                )
                critical_failed = critical_failed or is_critical
                continue
            if isinstance(result, BaseException):
                failures.append(f"endpoint={endpoint}, error={result}")
                critical_failed = critical_failed or is_critical
                continue
            self._log_recovery(
                f"required:{endpoint.cooldown_key}",
                "Sharesight endpoint %s recovered",
                endpoint,
            )
            self._merge(combined, endpoint, result)

        if auth_failure is not None:
            raise ConfigEntryAuthFailed(
                "Sharesight returned an authentication error "
                f"({auth_failure.detail}) - re-authentication required"
            )

        if critical_failed:
            return self._degrade(
                f"{len(failures)} required endpoint(s) failed: {'; '.join(failures[:3])}"
            )
        if failures:
            _LOGGER.debug(
                "Sharesight poll continued with %s non-critical failure(s): %s",
                len(failures),
                "; ".join(failures[:3]),
            )

        # --- Slow tier -------------------------------------------------
        current_fy_bounds = (self.start_financial_year, self.end_financial_year)
        slow_endpoints = self._slow_endpoints(today)
        slow_keys = {endpoint.key for endpoint in slow_endpoints if endpoint.key is not None}
        self._slow_retry_keys.intersection_update(slow_keys)
        refresh_all_slow = (
            self._poll_count % SLOW_PERIOD_REFRESH_EVERY == 0
            or self._slow_window_fy_bounds != current_fy_bounds
        )
        if refresh_all_slow:
            self._slow_window_fy_bounds = current_fy_bounds
            active_slow = slow_endpoints
        else:
            active_slow = [
                endpoint for endpoint in slow_endpoints if endpoint.key in self._slow_retry_keys
            ]

        attempted_slow_keys: set[str] = set()
        retryable_slow_failures: set[str] = set()
        if active_slow:
            for endpoint, result in await self._gather(active_slow, access_token):
                if endpoint.key is not None:
                    attempted_slow_keys.add(endpoint.key)
                if isinstance(result, BaseException):
                    detail = (
                        result.detail
                        if isinstance(result, SharesightApiError)
                        else f"endpoint={endpoint}, error={result}"
                    )
                    self._log_failure(
                        f"slow:{endpoint.cooldown_key}",
                        "Sharesight slow-tier request failed: %s",
                        detail,
                    )
                    if endpoint.key is not None and (
                        not isinstance(result, SharesightApiError)
                        or result.transport
                        or result.is_retryable
                        or result.is_rate_limited
                        or result.is_lockout
                    ):
                        retryable_slow_failures.add(endpoint.key)
                    continue
                self._log_recovery(
                    f"slow:{endpoint.cooldown_key}",
                    "Sharesight endpoint %s recovered",
                    endpoint,
                )
                self._merge(combined, endpoint, result)

        # Every attempted key now has a definitive outcome for this poll.
        # Successful and permanent failures leave the fast-retry set; transient
        # failures are added back below only if carry-forward cannot fill them.
        self._slow_retry_keys.difference_update(attempted_slow_keys)

        # --- Optional tier ---------------------------------------------
        optional = self._optional_endpoints(today)
        optional_keys = {endpoint.cooldown_key for endpoint in optional}
        self._optional_retry_keys.intersection_update(optional_keys)
        active = [
            endpoint
            for endpoint in optional
            if endpoint.cooldown_key not in self._unsupported_endpoints
            and not self._endpoint_on_cooldown(endpoint.cooldown_key)
            and (
                self._poll_count % max(1, endpoint.refresh_every) == 0
                or endpoint.cooldown_key in self._optional_retry_keys
            )
        ]
        deferred = len(optional) - len(active)
        _LOGGER.debug(
            "Requesting %s optional Sharesight endpoints (%s deferred by cadence, cooldown, or capability)",
            len(active),
            deferred,
        )
        attempted_optional_keys: set[str] = set()
        retryable_optional_failures: dict[str, str] = {}
        for endpoint, result in await self._gather(active, access_token):
            attempted_optional_keys.add(endpoint.cooldown_key)
            if isinstance(result, BaseException):
                detail = (
                    result.detail
                    if isinstance(result, SharesightApiError)
                    else f"endpoint={endpoint}, error={result}"
                )
                _LOGGER.debug("Optional Sharesight endpoint parked: %s (retrying later)", detail)
                self._note_optional_failure(
                    endpoint.cooldown_key,
                    result if isinstance(result, SharesightApiError) else None,
                )
                source_key = endpoint.key or (
                    "benchmark"
                    if endpoint.path.removesuffix(".json").endswith("/benchmark")
                    else None
                )
                if (
                    endpoint.refresh_every > 1
                    and source_key is not None
                    and isinstance(result, SharesightApiError)
                    and (
                        result.is_rate_limited
                        or result.transport
                        or result.is_retryable
                        or result.is_lockout
                    )
                ):
                    retryable_optional_failures[endpoint.cooldown_key] = source_key
                continue
            self._note_optional_success(endpoint.cooldown_key)
            self._merge(combined, endpoint, result)

        # As with the slow tier, only transient failures that remain genuinely
        # source-less after carry-forward are re-added below.
        self._optional_retry_keys.difference_update(attempted_optional_keys)

        # --- Per-account cash transactions (optional) ------------------
        await self._fetch_cash_transactions(combined, access_token)

        # --- Replay anything the poll could not fetch ------------------
        replayed = self._replay_missing(combined)
        self._slow_retry_keys.update(key for key in retryable_slow_failures if key not in combined)
        self._optional_retry_keys.update(
            cooldown_key
            for cooldown_key, source_key in retryable_optional_failures.items()
            if source_key not in combined
        )
        if replayed:
            _LOGGER.debug(
                "Serving carried-forward Sharesight data for: %s",
                ", ".join(sorted(replayed)),
            )

        # --- Derive everything else ------------------------------------
        try:
            self._post_process(combined, today)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            IndexError,
            ZeroDivisionError,
        ) as err:
            _LOGGER.exception("Sharesight post-processing failed: %s", err)
            return self._degrade(f"post-processing failed: {err}")

        self._poll_count += 1
        self.data = combined
        self.data_timestamp = dt_util.utcnow()
        if self._degraded_polls:
            _LOGGER.info("Sharesight data is fresh again")
        self._degraded_polls = 0
        self.degraded_reason = None
        self._logged_failures.pop("degraded", None)
        return self.data

    def _remember_raw_response(self, key: str, payload: Any) -> None:
        """Keep an immutable diagnostic copy of a successful source response."""
        raw_responses = getattr(self, "_raw_responses", None)
        if not isinstance(raw_responses, dict):
            raw_responses = {}
            self._raw_responses = raw_responses
        raw_responses[key] = deepcopy(payload)

    def _merge(self, combined: dict[str, Any], endpoint: Endpoint, response: Any) -> None:
        """File a successful response under its key and remember it."""
        payload = response
        if (
            endpoint.path.removesuffix(".json") == "portfolios"
            and isinstance(payload, dict)
            and isinstance(payload.get("portfolios"), list)
        ):
            payload = dict(payload)
            payload["portfolios"] = [
                self._normalise_portfolio_detail(portfolio)
                if isinstance(portfolio, dict)
                else portfolio
                for portfolio in payload["portfolios"]
            ]
        if (
            endpoint.key
            and endpoint.path.endswith("/performance")
            and isinstance(payload, dict)
            and isinstance(payload.get("report"), dict)
        ):
            # Public V3 performance wraps every window in {"report": {...}};
            # sensors consume the keyed report itself.
            payload = payload["report"]
        if endpoint.key == "value_series" and isinstance(payload, list):
            # The value-data series can answer with a bare top-level array.
            payload = {"data": payload}
        raw_key = endpoint.key
        if raw_key is None:
            path = endpoint.path.removesuffix(".json")
            if path == "portfolios":
                raw_key = "portfolios"
            elif path.endswith("/performance"):
                raw_key = "report"
            elif path.endswith("/benchmark"):
                raw_key = "benchmark"
        if raw_key is not None:
            # A deep copy prevents later entity-only transformations (such as
            # filtering sold holdings) changing the diagnostic source body.
            self._remember_raw_response(raw_key, payload)
        if endpoint.key:
            merge_dicts(combined, {endpoint.key: payload})
            self._remember(endpoint.key, payload)
        elif isinstance(payload, dict):
            merge_dicts(combined, payload)
            # Un-keyed V3 responses namespace themselves; remember the parts
            # that carry real data so they can be carried forward too.
            for name in ("report", "portfolios", "benchmark"):
                if name in payload:
                    self._remember(name, payload[name])

    async def _fetch_cash_transactions(self, combined: dict[str, Any], access_token: str) -> None:
        """Fetch each cash account's transactions, tolerating a parked account.

        Each account has its own snapshot, so one failed account retains its
        rows while the others continue updating. Writing an empty list merely
        because an endpoint is parked would publish $0 contributions and a
        bogus net-investment gain, so no aggregate is emitted until at least
        one account has supplied a valid list.
        """
        if not hasattr(self, "_cash_transactions_by_account"):
            # Compatibility for unit tests and a coordinator instance restored
            # across a hot reload while this release is being developed.
            self._cash_transactions_by_account = {}

        cash_accounts_fresh = "cash_accounts_v2" in combined
        cash_accounts_data = combined.get("cash_accounts_v2") or self.data.get("cash_accounts_v2")
        accounts = (
            cash_accounts_data.get("cash_accounts") or []
            if isinstance(cash_accounts_data, dict)
            else []
        )

        work: list[tuple[int, Endpoint]] = []
        active_account_ids: set[int] = set()
        for account in accounts:
            if not isinstance(account, dict):
                continue
            raw_account_id = account.get("id")
            if raw_account_id is None or str(account.get("portfolio_id")) != str(self.portfolio_id):
                continue
            try:
                account_id = int(raw_account_id)
            except TypeError, ValueError:
                continue
            active_account_ids.add(account_id)
            if self._cash_tx_on_cooldown(account_id):
                continue
            work.append(
                (
                    account_id,
                    Endpoint(
                        "v2",
                        f"cash_accounts/{account_id}/cash_account_transactions.json",
                        None,
                        None,
                    ),
                )
            )

        if cash_accounts_fresh:
            for account_id in set(self._cash_transactions_by_account) - active_account_ids:
                del self._cash_transactions_by_account[account_id]

        if not work:
            if self._cash_transactions_by_account and active_account_ids <= set(
                self._cash_transactions_by_account
            ):
                transactions = [
                    row
                    for account_id in sorted(self._cash_transactions_by_account)
                    for row in self._cash_transactions_by_account[account_id]
                ]
                payload = {"cash_account_transactions": transactions}
                combined["cash_account_transactions"] = payload
                self._remember_raw_response("cash_account_transactions", payload)
                self._remember("cash_account_transactions", payload)
            elif "cash_account_transactions" not in combined:
                cached = self.data.get("cash_account_transactions")
                if cached is not None:
                    combined["cash_account_transactions"] = cached
            return

        results = await asyncio.gather(
            *(self._call(endpoint, access_token) for _, endpoint in work),
            return_exceptions=True,
        )
        for (account_id, endpoint), result in zip(work, results, strict=True):
            if isinstance(result, BaseException):
                detail = (
                    result.detail
                    if isinstance(result, SharesightApiError)
                    else f"endpoint={endpoint}, error={result}"
                )
                _LOGGER.debug("Cash-account transactions unavailable: %s", detail)
                self._note_cash_tx_failure(account_id)
                continue
            rows = result.get("cash_account_transactions") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                _LOGGER.debug(
                    "Cash-account transactions returned an invalid payload: endpoint=%s",
                    endpoint,
                )
                self._note_cash_tx_failure(account_id)
                continue
            self._note_cash_tx_success(account_id)
            self._cash_transactions_by_account[account_id] = [
                row for row in rows if isinstance(row, dict)
            ]

        complete_snapshot = active_account_ids <= set(self._cash_transactions_by_account)
        if self._cash_transactions_by_account and complete_snapshot:
            transactions = [
                row
                for account_id in sorted(self._cash_transactions_by_account)
                for row in self._cash_transactions_by_account[account_id]
            ]
            payload = {"cash_account_transactions": transactions}
            combined["cash_account_transactions"] = payload
            self._remember_raw_response("cash_account_transactions", payload)
            self._remember("cash_account_transactions", payload)
        elif "cash_account_transactions" not in combined:
            cached = self.data.get("cash_account_transactions")
            if cached is not None:
                combined["cash_account_transactions"] = cached

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def _post_process(self, combined: dict[str, Any], today: date) -> None:
        """Normalise the merged payload and derive everything computable."""
        if self._portfolio_detail:
            combined["portfolio_detail"] = self._portfolio_detail

        report = combined.get("report")
        if not isinstance(report, dict):
            report = {}

        raw_report_holdings = report.get("holdings")
        holdings_field_valid = isinstance(raw_report_holdings, list)
        report_holdings = self._open_positions(raw_report_holdings) if holdings_field_valid else []
        if holdings_field_valid:
            report["holdings"] = report_holdings

        self._dedupe_named(report, "sub_totals", "group_name")
        self._dedupe_named(report, "cash_accounts", "name")

        # The report's holdings list is richer than the standalone /holdings
        # endpoint's (which carries no quantity or value at all), so it is the
        # only source used. A present empty list is authoritative (the final
        # holding was sold); only a missing/malformed field retains old data.
        if holdings_field_valid:
            combined["holdings"] = {
                "holdings": report_holdings,
                "value": report.get("value", 0),
            }
        elif "holdings" in self.data:
            combined["holdings"] = self.data["holdings"]
        else:
            combined["holdings"] = {"holdings": [], "value": 0}

        holdings_list = combined["holdings"].get("holdings") or []
        held_symbols = {
            symbol for holding in holdings_list if (symbol := analytics.holding_symbol(holding))
        }

        # --- Income ----------------------------------------------------
        payouts_data = combined.get("payouts")
        payouts = payouts_data.get("payouts") or [] if isinstance(payouts_data, dict) else []
        payouts = [
            payout
            for payout in payouts
            if isinstance(payout, dict)
            and str(payout.get("state") or payout.get("status") or "").lower() != "rejected"
        ]

        income_report: dict[str, Any] = {
            "payouts": payouts,
            "payouts_available": isinstance(payouts_data, dict),
        }
        if payouts:
            # Amounts are converted with each payout's own exchange rate, so a
            # portfolio holding both AUD and USD payers totals in one currency.
            income_report["total_income"] = round(
                sum(
                    analytics.to_portfolio_currency(payout, payout.get("amount")) or 0.0
                    for payout in payouts
                ),
                2,
            )
        else:
            income_report["payout_gain"] = report.get("payout_gain")

        upcoming_data = combined.get("upcoming_payouts")
        upcoming = upcoming_data.get("payouts") or [] if isinstance(upcoming_data, dict) else []
        income_report["upcoming_payouts"] = [
            payout
            for payout in upcoming
            if isinstance(payout, dict)
            and str(payout.get("state") or payout.get("status") or "").lower() != "rejected"
        ]
        income_report["upcoming_payouts_available"] = isinstance(upcoming_data, dict)
        combined["income_report"] = income_report

        # --- Diversity -------------------------------------------------
        combined["diversity"] = self._build_diversity(combined, report)

        # --- Activity events (no extra API calls) ----------------------
        try:
            self._build_activity_events(
                combined,
                today,
                holdings_snapshot_valid=holdings_field_valid,
            )
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as err:
            _LOGGER.debug("Sharesight activity diff failed: %s", err)

        # --- Derived analytics (no extra API calls) --------------------
        instrument_lookup = analytics.build_instrument_lookup(
            combined.get("user_instruments") or {}
        )
        combined["instrument_lookup"] = instrument_lookup

        # Resolve against the payload being built, not ``self.data`` from the
        # previous poll. Report-currency changes then affect every derivation
        # and entity unit in the same update.
        currency = self._portfolio_currency_for(combined)
        combined["holding_income"] = analytics.build_holding_income(payouts, holdings_list, today)
        combined["holding_trades"] = analytics.build_holding_trades(
            (combined.get("trades") or {}).get("trades") or [],
            holdings_list,
            currency,
        )
        combined["sector_allocation"] = analytics.build_sector_allocation(
            holdings_list, instrument_lookup, axis="sector"
        )
        combined["industry_allocation"] = analytics.build_sector_allocation(
            holdings_list, instrument_lookup, axis="industry"
        )
        combined["type_allocation"] = analytics.build_sector_allocation(
            holdings_list, instrument_lookup, axis="instrument_type"
        )
        combined["currency_allocation"] = analytics.build_currency_allocation(
            holdings_list, currency
        )
        combined["portfolio_analytics"] = analytics.build_portfolio_analytics(
            holdings_list,
            instrument_lookup,
            report,
            today,
            combined.get("holding_income"),
            currency,
        )
        if "value_series" in combined:
            combined["value_trend"] = analytics.build_value_trend(combined["value_series"])
            combined["value_analytics"] = analytics.build_value_analytics(combined["value_series"])
        combined["label_allocation"] = analytics.build_label_allocation(holdings_list)
        combined["cgt_analytics"] = analytics.build_cgt_analytics(
            combined.get("capital_gains"), combined.get("unrealised_cgt")
        )

        forecast = analytics.build_income_forecast(
            income_report.get("upcoming_payouts"),
            combined.get("holding_income") or {},
            report.get("value"),
            today,
            held_symbols,
        )
        for key, value in forecast.items():
            income_report.setdefault(key, value)

        # --- Financial-year rollover -----------------------------------
        own = self._own_portfolio_entry(combined.get("portfolios"))
        if own:
            # Keep the cached detail current so country/inception/currency
            # follow a change made in the Sharesight web app.
            for field in (
                "financial_year_end",
                "country_code",
                "currency_code",
                "inception_date",
                "interest_method",
                "tz_name",
            ):
                if own.get(field) is not None:
                    self._portfolio_detail[field] = own[field]
            start, end = get_financial_year_dates(own.get("financial_year_end"), today)
            if (start, end) != (self.start_financial_year, self.end_financial_year):
                _LOGGER.info(
                    "Sharesight financial year for portfolio %s is now %s..%s",
                    self.portfolio_id,
                    start,
                    end,
                )
                self.start_financial_year, self.end_financial_year = start, end

    @staticmethod
    def _dedupe_named(report: dict[str, Any], field: str, name_key: str) -> None:
        """Drop duplicate rows from a report list, keeping the first of each."""
        rows = report.get(field)
        if not isinstance(rows, list) or not rows:
            return
        seen: set[Any] = set()
        deduped: list[Any] = []
        for row in rows:
            name = row.get(name_key) if isinstance(row, dict) else None
            if name in seen:
                continue
            seen.add(name)
            deduped.append(row)
        if len(deduped) < len(rows):
            report[field] = deduped

    def _build_diversity(self, combined: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        """Build the market ranking from the market-grouped performance report.

        The V2 diversity endpoint defaults to industry classification. Polling
        it and labelling the result "Top Market" was both wrong and one extra
        heavy API call. ``_performance_params`` pins the combined report to
        ``grouping=market``, making its subtotals the authoritative source.
        A present empty list is authoritative (the portfolio has no market
        positions); only a missing/malformed field retains the prior result.
        """
        breakdown: list[dict[str, Any]] = []
        sub_totals = report.get("sub_totals")
        if isinstance(sub_totals, list):
            total_value = analytics._f(report.get("value")) or 0.0
            for sub_total in sub_totals:
                if not isinstance(sub_total, dict):
                    continue
                value = analytics._f(sub_total.get("value")) or 0.0
                breakdown.append(
                    {
                        "group_name": sub_total.get("group_name", ""),
                        "percentage": round(value / total_value * 100, 2) if total_value else 0,
                        "value": value,
                    }
                )
            return {"breakdown": breakdown}
        previous = self.data.get("diversity")
        if isinstance(previous, dict) and previous.get("breakdown"):
            return previous
        return {"breakdown": []}

    # ------------------------------------------------------------------
    # Activity diff
    # ------------------------------------------------------------------

    @staticmethod
    def _activity_key(record: dict[str, Any], *fallback_fields: str) -> Any:
        """Stable key for an activity record.

        The record id when present, else a synthetic tuple over the given
        fields.  Announced payouts carry a null id until they are confirmed,
        so the fallback fields have to be ones that really exist on the
        payload - which is why the ex-date is read through
        ``analytics.payout_ex_date`` rather than a literal ``ex_date`` key
        that no live payout has ever had.
        """
        record_id = record.get("id")
        if record_id is not None:
            return f"id:{record_id}"
        return tuple(str(record.get(field)) for field in fallback_fields)

    @staticmethod
    def _upcoming_key(payout: dict[str, Any]) -> Any:
        """De-duplication key for an announced (id-less) payout."""
        payout_id = payout.get("id")
        if payout_id is not None:
            return f"id:{payout_id}"
        return (
            str(payout.get("symbol")),
            str(analytics.payout_ex_date(payout)),
            str(payout.get("amount")),
        )

    @staticmethod
    def _cash_transaction_type(txn: dict[str, Any]) -> str | None:
        """The transaction type name, from wherever Sharesight put it.

        The live payload nests it as ``cash_account_transaction_type.name``;
        the flat ``trade_type`` / ``type`` keys the diff used to read do not
        exist, so every emitted cash event carried ``type: None``.
        """
        type_obj = txn.get("cash_account_transaction_type")
        if isinstance(type_obj, dict) and type_obj.get("name"):
            return str(type_obj["name"])
        for field in ("type_name", "trade_type", "type"):
            if txn.get(field):
                return str(txn[field])
        return None

    def _build_activity_events(
        self,
        combined_dict: dict[str, Any],
        today: date,
        *,
        holdings_snapshot_valid: bool,
    ) -> None:
        """Diff this poll's records against the last poll and stage HA events.

        The first successful poll seeds the baselines silently; every later
        poll writes ``activity_events`` as ``{event_type: [compact dicts]}``
        for the event platform to emit.  Each list is capped, and only the
        records actually emitted are marked as seen - marking the whole batch
        (as this used to) silently discarded everything past the cap forever.
        """
        holdings_list = (combined_dict.get("holdings") or {}).get("holdings") or []
        income_report = combined_dict.get("income_report") or {}
        payouts = income_report.get("payouts") or []
        upcoming = income_report.get("upcoming_payouts") or []

        trades_source = combined_dict.get("trades")
        trades_valid = isinstance(trades_source, dict) and isinstance(
            trades_source.get("trades"), list
        )
        trades = trades_source["trades"] if trades_valid else []

        payouts_valid = bool(income_report.get("payouts_available")) and isinstance(payouts, list)
        upcoming_valid = bool(income_report.get("upcoming_payouts_available")) and isinstance(
            upcoming, list
        )

        cash_source = combined_dict.get("cash_account_transactions")
        cash_valid = isinstance(cash_source, dict) and isinstance(
            cash_source.get("cash_account_transactions"), list
        )
        cash_txns = cash_source["cash_account_transactions"] if cash_valid else []
        portfolio_currency = self._portfolio_currency_for(combined_dict)

        id_to_symbol: dict[str, str] = {}
        holding_id_to_currency: dict[str, str] = {}
        symbol_to_value: dict[str, Any] = {}
        current_symbols: set[str] = set()
        for holding in holdings_list:
            if not isinstance(holding, dict):
                continue
            symbol = analytics.holding_symbol(holding)
            if not symbol:
                continue
            current_symbols.add(symbol)
            symbol_to_value[symbol] = holding.get("value")
            holding_id = holding.get("id")
            if holding_id is not None:
                id_to_symbol[str(holding_id)] = symbol
                if currency := analytics.holding_currency(holding):
                    holding_id_to_currency[str(holding_id)] = currency

        cash_currency_by_id: dict[str, str] = {}
        cash_accounts = combined_dict.get("cash_accounts_v2")
        if isinstance(cash_accounts, dict):
            for account in cash_accounts.get("cash_accounts") or []:
                if not isinstance(account, dict) or account.get("id") is None:
                    continue
                if currency := analytics.record_currency_code(account):
                    cash_currency_by_id[str(account["id"])] = currency

        def _symbol_of(record: dict[str, Any]) -> str | None:
            if symbol := record.get("symbol"):
                return str(symbol)
            holding_id = record.get("holding_id")
            if holding_id is not None:
                return id_to_symbol.get(str(holding_id))
            return None

        one_day = combined_dict.get("one-day") or {}
        daily_close_date = None
        if isinstance(one_day, dict):
            daily_close_date = (
                one_day.get("end_date") or one_day.get("date") or one_day.get("as_at")
            )
        daily_close_date = str(daily_close_date)[:10] if daily_close_date else today.isoformat()

        def _trade_key(trade: dict[str, Any]) -> Any:
            return self._activity_key(trade, "symbol", "transaction_date", "quantity")

        def _payout_key(payout: dict[str, Any]) -> Any:
            return self._activity_key(payout, "symbol", "paid_on", "amount")

        def _cash_key(txn: dict[str, Any]) -> Any:
            return self._activity_key(txn, "date_time", "amount", "description")

        def _trade_event(trade: dict[str, Any]) -> dict[str, Any]:
            holding_id = trade.get("holding_id")
            price_currency = trade.get("price_currency_code")
            if not price_currency and holding_id is not None:
                price_currency = holding_id_to_currency.get(str(holding_id))
            return {
                "symbol": _symbol_of(trade),
                "market": trade.get("market"),
                "type": trade.get("transaction_type"),
                "quantity": trade.get("quantity"),
                "price": trade.get("price"),
                "price_currency": price_currency,
                # Sharesight documents trades[].value in portfolio currency.
                "value": trade.get("value"),
                "value_currency": portfolio_currency,
                "currency": portfolio_currency,
                "date": trade.get("transaction_date") or trade.get("date"),
            }

        def _payout_event(payout: dict[str, Any], *, announced: bool) -> dict[str, Any]:
            details = analytics.monetary_amount_details(
                payout,
                payout.get("amount") or payout.get("gross_amount"),
                portfolio_currency,
            )
            payload: dict[str, Any] = {
                "symbol": _symbol_of(payout),
                **details,
            }
            if announced:
                payload.update(
                    {
                        "ex_date": analytics.payout_ex_date(payout),
                        "pay_date": analytics.payout_pay_date(payout),
                        "date": analytics.payout_ex_date(payout)
                        or analytics.payout_pay_date(payout),
                        "reinvested": bool(
                            (payout.get("drp_trade_attributes") or {}).get("dividend_reinvested")
                        ),
                    }
                )
            else:
                payload.update(
                    {
                        "date": analytics.payout_pay_date(payout),
                        "franking_credits": payout.get("franking_credits"),
                        "franking_credits_currency": portfolio_currency,
                    }
                )
            return payload

        def _cash_event(txn: dict[str, Any]) -> dict[str, Any]:
            account_id = txn.get("cash_account_id")
            native_currency = analytics.record_currency_code(txn)
            if not native_currency and account_id is not None:
                native_currency = cash_currency_by_id.get(str(account_id))
            native_amount = analytics._f(txn.get("amount"))
            native_balance = analytics._f(txn.get("balance"))
            same_currency = (
                native_currency is not None
                and native_currency.upper() == portfolio_currency.upper()
            )
            return {
                # There is no historical FX rate on a cash transaction. Only
                # expose a portfolio amount when its account already uses the
                # portfolio currency; otherwise fail closed and retain native.
                "amount": native_amount if same_currency else None,
                "currency": portfolio_currency,
                "native_amount": native_amount,
                "native_currency": native_currency,
                "date": txn.get("date_time") or txn.get("date"),
                "description": txn.get("description"),
                "type": self._cash_transaction_type(txn),
                "balance": native_balance if same_currency else None,
                "balance_currency": portfolio_currency,
                "native_balance": native_balance,
            }

        if not hasattr(self, "_activity_sources_seeded"):
            # Compatibility with coordinators constructed before this release
            # during a development hot reload.  Failing closed here can at
            # worst suppress one real event; treating unknown baselines as
            # empty would replay every historical record instead.
            self._activity_sources_seeded = set()

        def _source_ready(
            source: str,
            valid: bool,
            records: list[dict[str, Any]],
            seen: set[Any],
            key_of: Any,
        ) -> bool:
            """Whether a source has a prior valid baseline and can be diffed."""
            if not valid:
                return False
            if source in self._activity_sources_seeded:
                return True
            seen.update(key_of(record) for record in records if isinstance(record, dict))
            self._activity_sources_seeded.add(source)
            return False

        trades_ready = _source_ready(
            "trades", trades_valid, trades, self._seen_trade_ids, _trade_key
        )
        payouts_ready = _source_ready(
            "payouts", payouts_valid, payouts, self._seen_payout_ids, _payout_key
        )
        upcoming_ready = _source_ready(
            "upcoming_payouts",
            upcoming_valid,
            upcoming,
            self._seen_upcoming_ids,
            self._upcoming_key,
        )
        cash_ready = _source_ready(
            "cash_account_transactions",
            cash_valid,
            cash_txns,
            self._seen_cash_tx_ids,
            _cash_key,
        )

        if not self._activity_seeded:
            if holdings_snapshot_valid:
                self._seen_holding_symbols = set(current_symbols)
                self._holdings_snapshot_seeded = True
            self._seen_daily_close_date = daily_close_date
            self._activity_seeded = True
            # Stage an empty batch rather than leaving the key absent, so the
            # sequence number is monotonic from the very first poll and
            # consumers never have to distinguish "no events" from "the diff
            # did not run".
            self._activity_seq += 1
            combined_dict["activity_events"] = {}
            combined_dict["activity_events_seq"] = self._activity_seq
            return

        cap = self._ACTIVITY_EVENT_CAP
        events: dict[str, list[dict[str, Any]]] = {}

        def _stage(
            event_type: str,
            records: list[dict[str, Any]],
            seen: set[Any],
            key_of: Any,
            compact: Any,
            limit: int,
        ) -> None:
            """Emit up to ``limit`` unseen records, marking only those seen."""
            fresh: list[tuple[Any, dict[str, Any]]] = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                key = key_of(record)
                if key in seen:
                    continue
                fresh.append((key, compact(record)))
            if not fresh:
                return
            events[event_type] = [payload for _, payload in fresh[:limit]]
            for key, _ in fresh[:limit]:
                seen.add(key)

        if trades_ready:
            _stage(
                "trade_confirmed",
                trades,
                self._seen_trade_ids,
                _trade_key,
                _trade_event,
                cap,
            )
        if payouts_ready:
            _stage(
                "dividend_paid",
                payouts,
                self._seen_payout_ids,
                _payout_key,
                lambda payout: _payout_event(payout, announced=False),
                cap,
            )
        if upcoming_ready:
            _stage(
                "dividend_announced",
                upcoming,
                self._seen_upcoming_ids,
                self._upcoming_key,
                lambda payout: _payout_event(payout, announced=True),
                cap,
            )
        if cash_ready:
            _stage(
                "cash_transaction",
                cash_txns,
                self._seen_cash_tx_ids,
                _cash_key,
                _cash_event,
                cap,
            )

        # holding_opened / holding_closed. A valid empty holdings list is an
        # authoritative "the final position was sold" snapshot; a missing or
        # malformed list is carried forward by _post_process and must not be
        # mistaken for every holding closing at once.
        if holdings_snapshot_valid and getattr(self, "_holdings_snapshot_seeded", False):
            opened = current_symbols - self._seen_holding_symbols
            closed = self._seen_holding_symbols - current_symbols
            if opened:
                events["holding_opened"] = [
                    {
                        "symbol": symbol,
                        "value": symbol_to_value.get(symbol),
                        "currency": portfolio_currency,
                    }
                    for symbol in sorted(opened)
                ][:cap]
            if closed:
                events["holding_closed"] = [{"symbol": symbol} for symbol in sorted(closed)][:cap]
            self._seen_holding_symbols = set(current_symbols)
        elif holdings_snapshot_valid:
            # If activity was first seeded from a partial report, silently
            # establish the holdings baseline on the first valid snapshot.
            self._seen_holding_symbols = set(current_symbols)
            self._holdings_snapshot_seeded = True

        if (
            daily_close_date
            and self._seen_daily_close_date
            and daily_close_date != self._seen_daily_close_date
        ):
            events["daily_close"] = [
                {
                    "date": daily_close_date,
                    "value": one_day.get("value") if isinstance(one_day, dict) else None,
                    "change": one_day.get("total_gain") if isinstance(one_day, dict) else None,
                    "change_percent": one_day.get("total_gain_percent")
                    if isinstance(one_day, dict)
                    else None,
                    "currency": portfolio_currency,
                }
            ]
        self._seen_daily_close_date = daily_close_date

        self._activity_seq += 1
        combined_dict["activity_events"] = events
        combined_dict["activity_events_seq"] = self._activity_seq

    # ------------------------------------------------------------------
    # On-demand (service / button) calls
    # ------------------------------------------------------------------

    async def _one_shot(self, endpoint: Endpoint) -> Any:
        """Run a single off-cadence request, normalising failure to a dict.

        Service handlers surface whichever leg succeeded rather than raising,
        so an endpoint the token's scope cannot reach degrades to an
        ``{"error": ...}`` block the caller can report.
        """
        token = await self._refresh_token_with_retries()
        try:
            return await self._call(endpoint, token)
        except SharesightApiError as err:
            return {"error": err.detail, "status": err.status}

    async def async_get_value_history(self) -> Any:
        """Fetch the inception-to-today portfolio value series."""
        params: dict[str, Any] | None = None
        if inception := self._portfolio_detail.get("inception_date"):
            params = {"start_date": inception}
        return await self._one_shot(
            Endpoint(
                "v3",
                f"portfolios/{self.portfolio_id}/portfolio_value_data.json",
                params,
                None,
            )
        )

    async def async_generate_performance_report(
        self,
        start_date: str,
        end_date: str,
        grouping: str | None = None,
        consolidated: bool | None = None,
        include_sales: bool | None = None,
    ) -> Any:
        """Generate an on-demand performance report for an arbitrary window."""
        params: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
        if grouping:
            params["grouping"] = grouping
        if consolidated is not None:
            params["consolidated"] = "true" if consolidated else "false"
        if include_sales is not None:
            params["include_sales"] = "true" if include_sales else "false"
        response = await self._one_shot(
            Endpoint(
                "v3",
                f"portfolios/{self.portfolio_id}/performance",
                params,
                None,
                heavy=True,
                fallback_version="v2",
            )
        )
        # Public V3 wraps the report while the documented V2 equivalent is
        # flat. Keep the service response stable regardless of which version
        # served it; error blocks deliberately pass through unchanged.
        if isinstance(response, dict) and isinstance(response.get("report"), dict):
            return response["report"]
        return response

    async def async_get_sharechecker(self, instrument_id: Any) -> Any:
        """One-shot V3 sharechecker fetch for an instrument."""
        return await self._one_shot(
            Endpoint("v3", f"instruments/{instrument_id}/sharechecker", None, None)
        )

    async def async_get_official_costs(self, holding_id: Any) -> dict[str, Any]:
        """A holding's official average purchase price and cost base.

        Uses the public-tier ``GET /v3/holdings/{id}`` with both expansion
        flags, which returns everything the two separate mobile-tagged
        ``average_purchase_price.json`` / ``cost_base.json`` calls did (and
        more) in one request that a standard token can actually reach.  Those
        two remain the fallback when the combined route is version-unavailable
        or omits the requested cost fields.
        """
        combined = await self._one_shot(
            Endpoint(
                "v3",
                f"holdings/{holding_id}",
                {"average_purchase_price": "true", "cost_base": "true"},
                None,
            )
        )
        if isinstance(combined, dict) and "error" in combined:
            # A token/rate/network failure affects both mobile fallbacks too;
            # multiplying requests only worsens it. Retry split routes solely
            # for a genuine route/version mismatch.
            if combined.get("status") not in (404, 406):
                return {
                    "average_purchase_price": combined,
                    "cost_base": combined,
                }
        holding: Any = None
        if isinstance(combined, dict) and "error" not in combined:
            holding = combined.get("holding")
            if not isinstance(holding, dict):
                holding = combined
        if isinstance(holding, dict) and holding.get("cost_base") is not None:
            # Normalise to the shape the two split endpoints return, so the
            # service's extractors do not need to know which route answered.
            # ``holding.average_purchase_price`` is a bare number in the
            # instrument's currency; ``holding.cost_base`` is already an object.
            currency = (holding.get("instrument_currency") or {}).get("code") or (
                holding.get("instrument") or {}
            ).get("currency_code")
            return {
                "average_purchase_price": {
                    "average_purchase_price": {
                        "value": holding.get("average_purchase_price"),
                        "currency": currency,
                    }
                },
                "cost_base": {"cost_base": holding.get("cost_base")},
            }

        app_result, cost_result = await asyncio.gather(
            self._one_shot(
                Endpoint(
                    "v3",
                    f"holdings/{holding_id}/average_purchase_price.json",
                    None,
                    None,
                )
            ),
            self._one_shot(Endpoint("v3", f"holdings/{holding_id}/cost_base.json", None, None)),
        )
        return {"average_purchase_price": app_result, "cost_base": cost_result}

    async def async_get_sso_link(self) -> Any:
        """One-shot Single Sign-On login-link fetch.

        SECURITY: the ``login_url`` this returns grants a logged-in Sharesight
        session and must be treated like a password.  This method therefore
        NEVER logs the URL (or the response) at any level, and the caller must
        not either.  The endpoint is documented rate-limit exempt.
        """
        return await self._one_shot(Endpoint("v2", "single_sign_on.json", None, None))
