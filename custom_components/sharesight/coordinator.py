"""Data update coordinator for the Sharesight integration."""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from . import analytics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    OPTIONAL_ENDPOINT_COOLDOWN,
    OPTIONAL_ENDPOINT_MAX_BACKOFF,
    SHARESIGHT_HEAVY_CONCURRENCY,
    SHARESIGHT_LOCKOUT_COOLDOWN,
    SLOW_PERIOD_REFRESH_EVERY,
)

_LOGGER = logging.getLogger(__name__)


def merge_dicts(d1: dict[Any, Any], d2: dict[Any, Any]) -> dict[Any, Any]:
    """Recursively merge d2 into d1, mutating d1 in-place and returning it.

    For overlapping keys with dict values the merge recurses; otherwise d2's
    value wins.  Pure function — does not perform I/O, so it is synchronous.
    """
    for key in set(itertools.chain(d1.keys(), d2.keys())):
        if key in d1 and key in d2 and isinstance(d1[key], dict) and isinstance(d2[key], dict):
            d1[key] = merge_dicts(d1[key], d2[key])
        elif key in d2:
            d1[key] = d2[key]
    return d1


def get_financial_year_dates(end_date_str: str | None) -> tuple[str, str]:
    """Compute the current financial year start/end dates (YYYY-MM-DD)."""
    today = dt_util.now()

    if not end_date_str:
        end_year = today.year if today.month <= 6 else today.year + 1
        return f"{end_year - 1}-07-01", f"{end_year}-06-30"

    end_date = datetime.strptime(end_date_str, "%m-%d")
    end_year = today.year if today.month <= 6 else today.year + 1
    end_date = end_date.replace(year=end_year)
    start_date = end_date.replace(year=end_year - 1) + timedelta(days=1)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def _get_scan_interval(entry: ConfigEntry | None) -> timedelta:
    """Pick coordinator scan interval from options, clamped to sane bounds."""
    if entry is None:
        return DEFAULT_SCAN_INTERVAL
    raw = entry.options.get(CONF_SCAN_INTERVAL)
    if raw is None:
        return DEFAULT_SCAN_INTERVAL
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SCAN_INTERVAL
    seconds = max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, seconds))
    return timedelta(seconds=seconds)


class SharesightCoordinator(TimestampDataUpdateCoordinator[dict[str, Any]]):
    """Coordinate polling of the Sharesight API for a single portfolio.

    Subclasses ``TimestampDataUpdateCoordinator`` rather than the plain
    ``DataUpdateCoordinator`` purely for ``last_update_success_time``: the
    "Last Successful Update" diagnostic sensor reads that attribute, and the
    plain base class never defines it, so the sensor could only ever report
    Unknown.  The timestamp variant stamps it in ``_async_refresh_finished``
    after every successful poll and is otherwise identical.
    """

    # Per-endpoint timeout (seconds).
    _ENDPOINT_TIMEOUT: int = 60

    # Number of retries for token validation before giving up.
    _TOKEN_RETRIES: int = 2
    _TOKEN_RETRY_DELAY: float = 3.0

    # Proactively refresh the access token when it has this many seconds or
    # fewer remaining before expiry.  Sharesight's OAuth token lifetime is
    # ~30 minutes; refreshing early avoids racing a poll against expiry,
    # which is what caused entities to flap "unavailable" for ~10s every
    # ~31 minutes.
    _TOKEN_REFRESH_MARGIN: float = 300.0

    # Cap on activity events emitted per type per poll, so the first poll
    # after a long outage (which sees a large backlog of "new" records) can
    # never produce an unbounded event payload.
    _ACTIVITY_EVENT_CAP: int = 20

    # News (W2) comes from an optional, mobile-scoped endpoint that can appear
    # mid-life after its backoff clears, so its first real diff may surface a
    # large batch; cap it tighter than the general activity cap.
    _NEWS_EVENT_CAP: int = 10

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        portfolio_id: Any,
        client: Any,
        oauth_session: Any,
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
        self.portfolio_id = portfolio_id
        self.startup_endpoint = ["v3", f"portfolios/{self.portfolio_id}", None, False]

        # {platform: {translation_key: icon}} resolved from icons.json by
        # __init__.async_setup_entry before the platforms are forwarded, so
        # every entity can publish its icon in attributes.icon (see icons.py
        # and SharesightBaseEntity.icon).  Empty until then, and harmlessly
        # empty if the load fails — entities just fall back to no icon
        # attribute, exactly as before.
        self.entity_icons: dict[str, dict[str, str]] = {}

        # Cooldowns (monotonic timestamps) for optional endpoints.  Each entry
        # maps endpoint path -> { "next_retry": float, "backoff": timedelta }.
        self._optional_endpoint_cooldowns: dict[str, dict[str, Any]] = {}
        self._cash_tx_account_cooldowns: dict[int, dict[str, Any]] = {}

        # Global "don't hit the API" deadline, used when Sharesight returns a
        # 10-minute brute-force lockout or a 403 parallel-request error.
        self._lockout_until: float = 0.0

        # Sharesight limits intensive report endpoints to 3 concurrent requests.
        self._heavy_request_semaphore = asyncio.Semaphore(SHARESIGHT_HEAVY_CONCURRENCY)
        # General cap to avoid request bursts across many portfolios.
        self._request_semaphore = asyncio.Semaphore(8)

        # Financial year caching - seeded on first successful startup fetch.
        self.start_financial_year: str = ""
        self.end_financial_year: str = ""
        self._portfolio_detail: dict[str, Any] = {}

        # Tiered polling (Feature 4).  Incremented once per successful poll;
        # the slow performance windows only re-fetch every
        # SLOW_PERIOD_REFRESH_EVERY polls.  _slow_window_fy_bounds records the
        # financial-year bounds used the last time they were fetched so a FY
        # rollover forces an immediate refresh.
        self._poll_count: int = 0
        self._slow_window_fy_bounds: tuple[str, str] | None = None

        # Activity diff (Feature 2).  "Seen" keys per record type so only
        # genuinely new records fire events; seeded silently on the first
        # successful poll via the _activity_seeded guard.
        self._seen_trade_ids: set[Any] = set()
        self._seen_payout_ids: set[Any] = set()
        self._seen_upcoming_ids: set[Any] = set()
        self._seen_cash_tx_ids: set[Any] = set()
        self._seen_holding_symbols: set[str] = set()
        self._seen_daily_close_date: str | None = None
        self._activity_seeded: bool = False
        # Monotonic id stamped on every staged activity_events batch so the
        # event entity can distinguish a freshly-diffed poll from a
        # keep-last-good cached return (which replays the same self.data and
        # would otherwise re-fire the batch on every degraded cycle).
        self._activity_seq: int = 0

        # News diff (W2) — instrument_news is an OPTIONAL, mobile-scoped
        # endpoint that may only come online mid-life (after its backoff
        # clears), long after poll 1 already seeded the other activity
        # families.  It therefore gets its OWN seed-on-first-sight guard,
        # tripped the first poll the instrument_news key actually appears in
        # the merged data — seeding silently then rather than on poll 1 — so a
        # mid-life arrival never replays its whole backlog as "new" events.
        self._seen_news_ids: set[Any] = set()
        self._news_seeded: bool = False

    # ------------------------------------------------------------------
    # OAuth token handling
    # ------------------------------------------------------------------

    async def _refresh_token_with_retries(self) -> str:
        """Ensure a valid access token, retrying transient refresh failures.

        Home Assistant's ``OAuth2Session`` surfaces any failure from the OAuth
        token endpoint as ``ConfigEntryAuthFailed``, even when the underlying
        cause is a transient 5xx/400 from Sharesight's token service.  We
        therefore retry a handful of times with backoff and only propagate
        ``ConfigEntryAuthFailed`` once we're confident the credentials really
        have been revoked.

        Returns the access token string on success.
        """
        last_error: Exception | None = None
        for attempt in range(self._TOKEN_RETRIES + 1):
            try:
                token = self.oauth_session.token or {}
                expires_at = token.get("expires_at")
                needs_refresh = True
                if expires_at is not None:
                    try:
                        needs_refresh = (
                            float(expires_at) - time.time()
                        ) <= self._TOKEN_REFRESH_MARGIN
                    except (TypeError, ValueError):
                        needs_refresh = True

                if needs_refresh:
                    _LOGGER.debug(
                        "Proactively refreshing Sharesight token (attempt %s/%s)",
                        attempt + 1,
                        self._TOKEN_RETRIES + 1,
                    )
                await self.oauth_session.async_ensure_token_valid()
                return self.oauth_session.token["access_token"]
            except ConfigEntryAuthFailed as auth_err:
                last_error = auth_err
                if attempt < self._TOKEN_RETRIES:
                    _LOGGER.debug(
                        "Token refresh attempt %s failed (%s), retrying in %ss",
                        attempt + 1,
                        auth_err,
                        self._TOKEN_RETRY_DELAY,
                    )
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as transient_err:
                last_error = transient_err
                if attempt < self._TOKEN_RETRIES:
                    _LOGGER.debug(
                        "Token refresh transient error on attempt %s (%s: %s), retrying in %ss",
                        attempt + 1,
                        type(transient_err).__name__,
                        transient_err,
                        self._TOKEN_RETRY_DELAY,
                    )
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise
            except HomeAssistantError as ha_err:
                last_error = ha_err
                err_msg = str(ha_err).lower()
                is_permanent_auth = any(
                    kw in err_msg
                    for kw in ("invalid_grant", "invalid_client", "access_denied")
                )
                if is_permanent_auth:
                    raise ConfigEntryAuthFailed(
                        f"Sharesight authentication failed: {ha_err}"
                    ) from ha_err
                if attempt < self._TOKEN_RETRIES:
                    _LOGGER.debug(
                        "Token refresh HA error on attempt %s (%s), retrying in %ss",
                        attempt + 1,
                        ha_err,
                        self._TOKEN_RETRY_DELAY,
                    )
                    await asyncio.sleep(self._TOKEN_RETRY_DELAY)
                    continue
                raise

        raise UpdateFailed(f"Exhausted token refresh retries: {last_error}")

    # ------------------------------------------------------------------
    # Low-level request plumbing
    # ------------------------------------------------------------------

    @staticmethod
    def _is_heavy_endpoint(path: str) -> bool:
        """Whether this endpoint is constrained by Sharesight's 3-concurrent limit."""
        heavy_markers = ("/performance", "/diversity", "/valuation")
        return any(marker in path for marker in heavy_markers)

    @staticmethod
    def _response_status(response: Any) -> int | None:
        """Best-effort extraction of an HTTP status code from a response dict."""
        if not isinstance(response, dict):
            return None
        status = response.get("status_code") or response.get("status")
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_rate_limited(response: Any) -> bool:
        """Detect Sharesight's 'too many parallel requests' 403."""
        if not isinstance(response, dict):
            return False
        status = SharesightCoordinator._response_status(response)
        reason = str(response.get("reason") or response.get("error") or "").lower()
        return status == 403 and ("parallel" in reason or "minute" in reason)

    @staticmethod
    def _is_lockout(response: Any) -> bool:
        """Detect Sharesight's 10-minute brute-force lockout 401."""
        if not isinstance(response, dict):
            return False
        status = SharesightCoordinator._response_status(response)
        reason = str(response.get("reason") or response.get("error") or "").lower()
        return status == 401 and "locked out" in reason

    def _register_lockout(self, duration: timedelta) -> None:
        """Suppress further API calls until ``duration`` from now."""
        self._lockout_until = max(self._lockout_until, time.monotonic() + duration.total_seconds())
        _LOGGER.warning(
            "Sharesight API cooldown active — suppressing requests for %s",
            duration,
        )

    def _in_lockout(self) -> bool:
        """Whether we are currently inside a global cooldown window."""
        return time.monotonic() < self._lockout_until

    async def _call_endpoint(self, endpoint: list[Any], access_token: str) -> Any:
        """Call one API endpoint with concurrency controls and a timeout."""
        version, path, params, _ = endpoint

        try:
            async with self._request_semaphore:
                if self._is_heavy_endpoint(path):
                    async with self._heavy_request_semaphore:
                        async with asyncio.timeout(self._ENDPOINT_TIMEOUT):
                            return await self.sharesight.get_api_request(
                                [version, path, params, False], access_token
                            )
                async with asyncio.timeout(self._ENDPOINT_TIMEOUT):
                    return await self.sharesight.get_api_request(
                        [version, path, params, False], access_token
                    )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Endpoint %s timed out after %ss", path, self._ENDPOINT_TIMEOUT
            )
            raise
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.warning(
                "Endpoint %s connection error: %s: %s",
                path,
                type(err).__name__,
                err,
            )
            raise

    async def async_get_value_history(self) -> Any:
        """Fetch the inception-to-today portfolio value series.

        Used by the long-term statistics backfill.  Reuses the coordinator's
        token refresh + concurrency/timeout controls.  Returns the raw API
        response (or an ``{"error": ...}`` dict on failure) — the caller must
        tolerate a gated/absent endpoint.
        """
        token = await self._refresh_token_with_retries()
        params: dict[str, Any] | None = None
        inception = self._portfolio_detail.get("inception_date")
        if inception:
            params = {"start_date": inception}
        endpoint = [
            "v3",
            f"portfolios/{self.portfolio_id}/portfolio_value_data.json",
            params,
            False,
        ]
        return await self._call_endpoint(endpoint, token)

    async def async_generate_performance_report(
        self,
        start_date: str,
        end_date: str,
        grouping: str | None = None,
        consolidated: bool | None = None,
        include_sales: bool | None = None,
    ) -> Any:
        """Generate an on-demand performance report for an arbitrary window.

        Modelled on ``async_get_value_history``: refresh the token then call
        the V3 performance endpoint directly, bypassing the poll cadence.
        Returns the raw API response (including an ``{"error": ...}`` dict on
        an API-level failure) — the caller must tolerate a gated/absent
        endpoint and never assume success.
        """
        token = await self._refresh_token_with_retries()
        params: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
        }
        if grouping:
            params["grouping"] = grouping
        if consolidated is not None:
            params["consolidated"] = "true" if consolidated else "false"
        if include_sales is not None:
            params["include_sales"] = "true" if include_sales else "false"
        endpoint = [
            "v3",
            f"portfolios/{self.portfolio_id}/performance",
            params,
            False,
        ]
        return await self._call_endpoint(endpoint, token)

    async def async_get_sharechecker(self, instrument_id: Any) -> Any:
        """One-shot V3 sharechecker fetch for an instrument (W3 fundamentals).

        Modelled on ``async_get_value_history``: refresh the token then call
        the V3 sharechecker endpoint directly, bypassing the poll cadence.
        Returns the raw API response (including an ``{"error": ...}`` dict on an
        API-level failure) — the caller must tolerate a gated/absent endpoint
        (this endpoint is mobile-scoped and may 403) and never assume success.
        """
        token = await self._refresh_token_with_retries()
        endpoint = ["v3", f"instruments/{instrument_id}/sharechecker", None, False]
        return await self._call_endpoint(endpoint, token)

    async def async_get_official_costs(self, holding_id: Any) -> dict[str, Any]:
        """One-shot fetch of a holding's official cost figures (W3 fundamentals).

        Issues the two light V3 per-holding calls — average purchase price and
        cost base — in parallel, each tolerant of a 403/error, and returns
        ``{"average_purchase_price": <resp>, "cost_base": <resp>}`` where each
        value is the raw API response (or an ``{"error": ...}`` dict on
        failure).  Never raises for an API-level error, so the caller can
        surface whichever leg succeeded.
        """
        token = await self._refresh_token_with_retries()
        app_endpoint = [
            "v3",
            f"holdings/{holding_id}/average_purchase_price.json",
            None,
            False,
        ]
        cost_endpoint = [
            "v3",
            f"holdings/{holding_id}/cost_base.json",
            None,
            False,
        ]
        app_result, cost_result = await asyncio.gather(
            self._call_endpoint(app_endpoint, token),
            self._call_endpoint(cost_endpoint, token),
            return_exceptions=True,
        )

        def _normalise(result: Any) -> Any:
            if isinstance(result, Exception):
                return {"error": str(result)}
            return result

        return {
            "average_purchase_price": _normalise(app_result),
            "cost_base": _normalise(cost_result),
        }

    async def async_get_sso_link(self) -> Any:
        """One-shot Single Sign-On login-link fetch (W4).

        SECURITY: the ``login_url`` this returns grants a logged-in Sharesight
        session and must be treated like a password.  This method therefore
        NEVER logs the URL (or the response) at any level, and the caller must
        not either.  Returns the raw API response (an ``{"error": ...}`` dict on
        failure); this endpoint is documented rate-limit exempt.
        """
        token = await self._refresh_token_with_retries()
        endpoint = ["v2", "single_sign_on.json", None, False]
        return await self._call_endpoint(endpoint, token)

    # ------------------------------------------------------------------
    # Optional endpoint cooldown bookkeeping
    # ------------------------------------------------------------------

    def _endpoint_on_cooldown(self, path: str) -> bool:
        info = self._optional_endpoint_cooldowns.get(path)
        if not info:
            return False
        return time.monotonic() < info["next_retry"]

    def _note_optional_failure(self, path: str) -> None:
        """Schedule exponential backoff before retrying this optional endpoint."""
        info = self._optional_endpoint_cooldowns.get(path)
        if info is None:
            backoff = OPTIONAL_ENDPOINT_COOLDOWN
        else:
            backoff = min(info["backoff"] * 2, OPTIONAL_ENDPOINT_MAX_BACKOFF)
        self._optional_endpoint_cooldowns[path] = {
            "next_retry": time.monotonic() + backoff.total_seconds(),
            "backoff": backoff,
        }

    def _note_optional_success(self, path: str) -> None:
        self._optional_endpoint_cooldowns.pop(path, None)

    def _cash_tx_on_cooldown(self, account_id: int) -> bool:
        info = self._cash_tx_account_cooldowns.get(account_id)
        if not info:
            return False
        return time.monotonic() < info["next_retry"]

    def _note_cash_tx_failure(self, account_id: int) -> None:
        info = self._cash_tx_account_cooldowns.get(account_id)
        if info is None:
            backoff = OPTIONAL_ENDPOINT_COOLDOWN
        else:
            backoff = min(info["backoff"] * 2, OPTIONAL_ENDPOINT_MAX_BACKOFF)
        self._cash_tx_account_cooldowns[account_id] = {
            "next_retry": time.monotonic() + backoff.total_seconds(),
            "backoff": backoff,
        }

    def _note_cash_tx_success(self, account_id: int) -> None:
        self._cash_tx_account_cooldowns.pop(account_id, None)

    # ------------------------------------------------------------------
    # Activity diff (Feature 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _activity_key(record: dict[str, Any], *fallback_fields: str) -> Any:
        """Stable key for an activity record: the record id when present,
        else a synthetic tuple over the given fields."""
        record_id = record.get("id")
        if record_id is not None:
            return f"id:{record_id}"
        return tuple(str(record.get(field)) for field in fallback_fields)

    def _build_activity_events(
        self, combined_dict: dict[str, Any], today: date
    ) -> None:
        """Diff this poll's records against the last poll and stage HA events.

        Maintains per-record-type "seen" sets so only genuinely new
        trades/payouts/holdings/cash transactions fire.  The first successful
        poll seeds the baselines silently (no fire); every later poll writes
        ``combined_dict["activity_events"]`` as ``{event_type: [compact attr
        dicts]}`` for the event platform to emit.  Each list is capped so a
        large first-poll backlog can never balloon the event payload.
        """
        holdings_list = (combined_dict.get("holdings") or {}).get("holdings") or []
        income_report = combined_dict.get("income_report") or {}
        payouts = income_report.get("payouts") or []
        upcoming = income_report.get("upcoming_payouts") or []
        trades = (combined_dict.get("trades") or {}).get("trades") or []
        cash_txns = (
            combined_dict.get("cash_account_transactions") or {}
        ).get("cash_account_transactions") or []

        # holding_id -> symbol so payout/trade compact dicts can show a symbol
        # even when the record only carries a holding_id.
        id_to_symbol: dict[str, str] = {}
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

        def _payout_symbol(record: dict[str, Any]) -> str | None:
            symbol = record.get("symbol")
            if symbol:
                return symbol
            holding_id = record.get("holding_id")
            if holding_id is not None:
                return id_to_symbol.get(str(holding_id))
            return None

        # The one-day report's date drives the daily_close signal; fall back
        # to today when the report doesn't expose a usable date field.
        one_day = combined_dict.get("one-day") or {}
        daily_close_date = None
        if isinstance(one_day, dict):
            daily_close_date = (
                one_day.get("end_date")
                or one_day.get("date")
                or one_day.get("as_at")
            )
        daily_close_date = (
            str(daily_close_date)[:10] if daily_close_date else today.isoformat()
        )

        # News (W2).  The instrument_news key is only present on polls where the
        # optional endpoint actually returned data; its absence (parked/backed
        # off) must leave the news seed set untouched.  Presence is the signal
        # that drives per-family seed-on-first-sight below.
        news_container = combined_dict.get("instrument_news")
        news_present = isinstance(news_container, dict)
        news_items: list[dict[str, Any]] = []
        if news_present:
            raw_news = news_container.get("instrument_news")
            if isinstance(raw_news, list):
                news_items = [item for item in raw_news if isinstance(item, dict)]

        def _news_key(article: dict[str, Any]) -> Any:
            return self._activity_key(article, "instrument_id", "published_at", "title")

        # Seed silently on the first successful poll — no events fire.
        if not self._activity_seeded:
            for trade in trades:
                if isinstance(trade, dict):
                    self._seen_trade_ids.add(
                        self._activity_key(trade, "symbol", "transaction_date", "quantity")
                    )
            for payout in payouts:
                if isinstance(payout, dict):
                    self._seen_payout_ids.add(
                        self._activity_key(payout, "symbol", "paid_on", "amount")
                    )
            for payout in upcoming:
                if isinstance(payout, dict):
                    self._seen_upcoming_ids.add(
                        self._activity_key(payout, "symbol", "ex_date", "amount")
                    )
            for txn in cash_txns:
                if isinstance(txn, dict):
                    self._seen_cash_tx_ids.add(
                        self._activity_key(txn, "date_time", "amount", "description")
                    )
            self._seen_holding_symbols = set(current_symbols)
            self._seen_daily_close_date = daily_close_date
            # Seed news too, but only if the endpoint was reachable this poll;
            # otherwise leave _news_seeded False so its own first-sight seed
            # (below) fires whenever the key later appears mid-life.
            if news_present:
                for article in news_items:
                    self._seen_news_ids.add(_news_key(article))
                self._news_seeded = True
            self._activity_seeded = True
            return

        cap = self._ACTIVITY_EVENT_CAP
        events: dict[str, list[dict[str, Any]]] = {}

        new_trades: list[dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            key = self._activity_key(trade, "symbol", "transaction_date", "quantity")
            if key in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(key)
            new_trades.append(
                {
                    "symbol": _payout_symbol(trade),
                    "market": trade.get("market"),
                    "type": trade.get("transaction_type"),
                    "quantity": trade.get("quantity"),
                    "price": trade.get("price"),
                    "value": trade.get("value"),
                    "date": trade.get("transaction_date") or trade.get("date"),
                }
            )
        if new_trades:
            events["trade_confirmed"] = new_trades[:cap]

        new_payouts: list[dict[str, Any]] = []
        for payout in payouts:
            if not isinstance(payout, dict):
                continue
            key = self._activity_key(payout, "symbol", "paid_on", "amount")
            if key in self._seen_payout_ids:
                continue
            self._seen_payout_ids.add(key)
            new_payouts.append(
                {
                    "symbol": _payout_symbol(payout),
                    "amount": payout.get("amount"),
                    "date": payout.get("paid_on") or payout.get("date"),
                    "franking_credits": payout.get("franking_credits"),
                }
            )
        if new_payouts:
            events["dividend_paid"] = new_payouts[:cap]

        new_upcoming: list[dict[str, Any]] = []
        for payout in upcoming:
            if not isinstance(payout, dict):
                continue
            key = self._activity_key(payout, "symbol", "ex_date", "amount")
            if key in self._seen_upcoming_ids:
                continue
            self._seen_upcoming_ids.add(key)
            new_upcoming.append(
                {
                    "symbol": _payout_symbol(payout),
                    "amount": payout.get("amount") or payout.get("gross_amount"),
                    "ex_date": payout.get("ex_date"),
                    "pay_date": payout.get("pay_date") or payout.get("paid_on"),
                    "date": payout.get("ex_date")
                    or payout.get("pay_date")
                    or payout.get("paid_on"),
                }
            )
        if new_upcoming:
            events["dividend_announced"] = new_upcoming[:cap]

        new_cash: list[dict[str, Any]] = []
        for txn in cash_txns:
            if not isinstance(txn, dict):
                continue
            key = self._activity_key(txn, "date_time", "amount", "description")
            if key in self._seen_cash_tx_ids:
                continue
            self._seen_cash_tx_ids.add(key)
            new_cash.append(
                {
                    "amount": txn.get("amount"),
                    "date": txn.get("date_time") or txn.get("date"),
                    "description": txn.get("description"),
                    "type": txn.get("trade_type") or txn.get("type"),
                }
            )
        if new_cash:
            events["cash_transaction"] = new_cash[:cap]

        # holding_opened / holding_closed — only diff against a non-empty
        # snapshot so a transient empty holdings payload can't fire spurious
        # "closed" events for the entire portfolio.
        if current_symbols:
            opened = current_symbols - self._seen_holding_symbols
            closed = self._seen_holding_symbols - current_symbols
            if opened:
                events["holding_opened"] = [
                    {"symbol": symbol, "value": symbol_to_value.get(symbol)}
                    for symbol in sorted(opened)
                ][:cap]
            if closed:
                events["holding_closed"] = [
                    {"symbol": symbol} for symbol in sorted(closed)
                ][:cap]
            self._seen_holding_symbols = set(current_symbols)

        # daily_close — fires when the one-day report's date advances.
        if (
            daily_close_date
            and self._seen_daily_close_date
            and daily_close_date != self._seen_daily_close_date
        ):
            events["daily_close"] = [
                {
                    "date": daily_close_date,
                    "value": one_day.get("value") if isinstance(one_day, dict) else None,
                    "change": one_day.get("value_change")
                    if isinstance(one_day, dict)
                    else None,
                    "change_percent": one_day.get("total_gain_percent")
                    if isinstance(one_day, dict)
                    else None,
                }
            ]
        self._seen_daily_close_date = daily_close_date

        # news_published (W2) — only when the endpoint returned data this poll.
        # The FIRST poll its key appears (which may be well after poll 1, once
        # the optional-endpoint backoff clears) seeds the baseline silently so a
        # backlog never fires; every later poll diffs by stable article id and
        # stages the genuinely-new items into the SAME events dict, so they ride
        # the single activity_events_seq bump below (never a separate one).
        if news_present:
            if not self._news_seeded:
                for article in news_items:
                    self._seen_news_ids.add(_news_key(article))
                self._news_seeded = True
            else:
                inst_id_to_symbol: dict[str, str] = {}
                user_instruments = combined_dict.get("user_instruments") or {}
                if isinstance(user_instruments, dict):
                    for inst in user_instruments.get("instruments", []) or []:
                        if not isinstance(inst, dict):
                            continue
                        inst_id = inst.get("id")
                        code = inst.get("code")
                        if inst_id is not None and code:
                            inst_id_to_symbol[str(inst_id)] = code
                for holding in holdings_list:
                    if not isinstance(holding, dict):
                        continue
                    instrument = holding.get("instrument") or {}
                    inst_id = instrument.get("id") or holding.get("instrument_id")
                    symbol = analytics.holding_symbol(holding)
                    if inst_id is not None and symbol:
                        inst_id_to_symbol.setdefault(str(inst_id), symbol)

                new_news: list[dict[str, Any]] = []
                new_news_keys: list[str] = []
                for article in news_items:
                    key = _news_key(article)
                    if key in self._seen_news_ids:
                        continue
                    article_instrument = article.get("instrument_id")
                    new_news.append(
                        {
                            # Headline/link/source/timestamp/symbol only — never
                            # the article body or HTML.
                            "title": article.get("title"),
                            "url": article.get("link"),
                            "source": article.get("source"),
                            "published_at": article.get("published_at"),
                            "symbol": inst_id_to_symbol.get(str(article_instrument))
                            if article_instrument is not None
                            else None,
                        }
                    )
                    new_news_keys.append(key)
                if new_news:
                    # Only mark as seen the articles we actually emit this poll;
                    # any beyond the cap stay unseen so a later poll can fire
                    # them, rather than silently dropping them forever.
                    events["news_published"] = new_news[: self._NEWS_EVENT_CAP]
                    for emitted_key in new_news_keys[: self._NEWS_EVENT_CAP]:
                        self._seen_news_ids.add(emitted_key)

        # Stamp a fresh sequence id for this diff.  Cached "keep last good"
        # returns reuse an old combined_dict (and its seq), so the event
        # entity can skip re-firing a batch it has already emitted.
        self._activity_seq += 1
        combined_dict["activity_events"] = events
        combined_dict["activity_events_seq"] = self._activity_seq

    # ------------------------------------------------------------------
    # One-time setup
    # ------------------------------------------------------------------

    async def _async_setup(self) -> None:
        """Seed portfolio detail and financial-year bounds once at setup.

        DataUpdateCoordinator calls this exactly once, inside
        ``async_config_entry_first_refresh`` and before the first
        ``_async_update_data``, which replaces the old per-poll
        ``started_up`` gate.  A transient failure raises ``UpdateFailed`` so the
        base surfaces a retriable ``ConfigEntryNotReady``; a 404 (portfolio
        deleted or access lost) raises ``ConfigEntryAuthFailed`` to trigger a
        reauth/reconfigure.
        """
        try:
            access_token = await self._refresh_token_with_retries()
        except ConfigEntryAuthFailed:
            raise
        except (
            aiohttp.ClientError,
            OSError,
            asyncio.TimeoutError,
            HomeAssistantError,
        ) as token_error:
            raise UpdateFailed(
                f"Error validating Sharesight token during setup: {token_error}"
            ) from token_error

        try:
            local_data = await self._call_endpoint(self.startup_endpoint, access_token)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as startup_error:
            raise UpdateFailed(
                f"Error during Sharesight startup fetch: {startup_error}"
            ) from startup_error

        if not isinstance(local_data, dict) or "error" in local_data:
            status = self._response_status(local_data)
            if status == 404:
                raise ConfigEntryAuthFailed(
                    f"Portfolio {self.portfolio_id} is no longer accessible. "
                    "Please reconfigure the integration."
                )
            raise UpdateFailed(f"Invalid startup response: {local_data}")

        self.start_financial_year, self.end_financial_year = get_financial_year_dates(
            local_data.get("portfolio", {}).get("financial_year_end")
        )
        self._portfolio_detail = local_data.get("portfolio", {}) or {}

    # ------------------------------------------------------------------
    # Main update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest data from Sharesight."""
        if self._in_lockout():
            remaining = int(self._lockout_until - time.monotonic())
            _LOGGER.info(
                "Skipping Sharesight poll — %ss remaining in cooldown", remaining
            )
            if self.data:
                return self.data
            raise UpdateFailed(
                f"Sharesight API is on cooldown for {remaining}s"
            )

        combined_dict: dict[str, Any] = {}

        try:
            access_token = await self._refresh_token_with_retries()
        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, HomeAssistantError) as token_error:
            if self.data:
                _LOGGER.warning(
                    "Token validation failed (%s), keeping last good data", token_error
                )
                return self.data
            raise UpdateFailed(
                f"Error validating Sharesight token: {token_error}"
            ) from token_error

        today = dt_util.now().date()
        self.current_date = today
        self.start_of_week = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        self.end_of_week = (today + timedelta(days=6 - today.weekday())).strftime("%Y-%m-%d")
        self.start_of_month = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        self.start_of_year = f"{today.year}-01-01"

        performance_params: dict[str, Any] = {
            "include_limited": "true",
            "report_combined": "true",
        }

        # Fast windows + the V3 combined report refresh on every poll.
        endpoint_list: list[list[Any]] = [
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": f"{today}", "end_date": f"{today}"},
                "one-day",
            ],
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": self.start_of_week, "end_date": self.end_of_week},
                "one-week",
            ],
            ["v3", "portfolios", None, False],
            [
                "v3",
                f"portfolios/{self.portfolio_id}/performance",
                performance_params,
                False,
            ],
        ]

        # Feature 4 — tiered polling.  The financial-year / one-month / YTD
        # windows move slowly, so only re-fetch them every
        # SLOW_PERIOD_REFRESH_EVERY polls (≈hourly at the 5-min default), on a
        # cold start (their key is absent from self.data), or when the
        # financial-year bounds roll over.  Skipped windows are carried
        # forward from self.data below so their period sensors never flap.
        slow_windows: list[list[Any]] = [
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {
                    "start_date": self.start_financial_year,
                    "end_date": self.end_financial_year,
                },
                "financial-year",
            ],
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": self.start_of_month, "end_date": f"{today}"},
                "one-month",
            ],
            [
                "v2",
                f"portfolios/{self.portfolio_id}/performance",
                {"start_date": self.start_of_year, "end_date": f"{today}"},
                "ytd",
            ],
        ]
        current_fy_bounds = (self.start_financial_year, self.end_financial_year)
        refresh_slow_windows = (
            self._poll_count % SLOW_PERIOD_REFRESH_EVERY == 0
            or self._slow_window_fy_bounds != current_fy_bounds
            or any(endpoint[3] not in self.data for endpoint in slow_windows)
        )
        if refresh_slow_windows:
            endpoint_list.extend(slow_windows)
            self._slow_window_fy_bounds = current_fy_bounds

        optional_endpoint_list: list[list[Any]] = [
            ["v3", f"portfolios/{self.portfolio_id}/holdings", None, "holdings"],
            ["v2", f"portfolios/{self.portfolio_id}/payouts", None, "payouts"],
            # Announced-but-not-yet-paid dividends.  The default payouts call
            # only covers inception→today, so future payouts never show up in
            # it; this second window feeds the next-dividend sensors and the
            # dividend calendar.
            [
                "v2",
                f"portfolios/{self.portfolio_id}/payouts",
                {
                    "start_date": f"{today}",
                    "end_date": f"{today + timedelta(days=365)}",
                },
                "upcoming_payouts",
            ],
            ["v2", f"portfolios/{self.portfolio_id}/diversity", None, "diversity_v2"],
            ["v2", f"portfolios/{self.portfolio_id}/trades", None, "trades"],
            ["v2", "cash_accounts", None, "cash_accounts_v2"],
            ["v3", f"portfolios/{self.portfolio_id}/user_setting", None, "user_setting"],
            ["v2", "user_instruments", None, "user_instruments"],
            # Benchmark performance (only returns data when the user has set a
            # benchmark on the portfolio; otherwise the optional-endpoint
            # backoff quietly parks it).  start_date is required — use the
            # portfolio inception date so the percentages line up with the
            # since-inception V3 performance report.
            [
                "v3",
                f"portfolios/{self.portfolio_id}/benchmark",
                {
                    "start_date": self._portfolio_detail.get("inception_date")
                    or self.start_of_year,
                    "end_date": f"{today}",
                },
                False,
            ],
            # Account/subscription info (near-static; one light V2 request).
            ["v2", "my_user.json", None, "my_user"],
            # Watchlist summary + market trading-hours metadata.  Both are V3
            # "mobile"/"internal"-scoped endpoints; if a standard API token
            # can't reach them they simply park in the backoff tier.
            ["v3", "watchlist.json", None, "watchlist"],
            ["v3", "markets", None, "markets"],
            # All-time totals INCLUDING fully-sold positions.  The V3
            # performance report omits include_sales, so realised gains from
            # exited holdings are missing there; this restores true lifetime
            # P&L.  Light endpoint (path matches no heavy marker); parks via
            # backoff for tokens whose scope can't reach it.
            [
                "v3",
                f"portfolios/{self.portfolio_id}/totals",
                {"include_sales": "true", "consolidated": "false"},
                "totals",
            ],
            # Instrument news (W2) — a light V2 "mobile"-scoped feed of recent
            # articles for the portfolio's instruments.  A standard API token
            # may not reach it, so it parks via the optional-endpoint backoff.
            [
                "v2",
                f"portfolios/{self.portfolio_id}/instrument_news.json",
                None,
                "instrument_news",
            ],
            # 30-day portfolio value series (W6) — a light V3 "mobile"-scoped
            # endpoint feeding the value-trend sensors; parks via backoff for
            # tokens whose scope can't reach it.
            [
                "v3",
                f"portfolios/{self.portfolio_id}/value",
                None,
                "value_series",
            ],
        ]

        # Live FX rates — only worth requesting for multi-currency portfolios.
        # Codes are derived from the previous poll's holdings/instruments (the
        # current poll's holdings aren't built yet), so this comes online on
        # the second poll.  exchange_rates is a V3 "internal" endpoint and may
        # not be reachable by all tokens; it parks via backoff if so.
        fx_codes = analytics.portfolio_currency_codes(self.data)
        if len(fx_codes) >= 2:
            optional_endpoint_list.append(
                [
                    "v3",
                    "exchange_rates",
                    {"codes": ",".join(fx_codes), "date": f"{today}"},
                    "exchange_rates",
                ]
            )

        # Capital gains tax reports are only available for Australian
        # portfolios (the API rejects them otherwise).
        if str(self._portfolio_detail.get("country_code", "")).upper() == "AU":
            optional_endpoint_list.extend(
                [
                    [
                        "v2",
                        f"portfolios/{self.portfolio_id}/capital_gains",
                        {
                            "start_date": self.start_financial_year,
                            "end_date": self.end_financial_year,
                        },
                        "capital_gains",
                    ],
                    [
                        "v2",
                        f"portfolios/{self.portfolio_id}/unrealised_cgt",
                        {"balance_date": f"{today}"},
                        "unrealised_cgt",
                    ],
                ]
            )

        try:
            _LOGGER.debug(
                "Calling %s required endpoints in parallel", len(endpoint_list)
            )
            required_tasks = [
                self._call_endpoint(endpoint, access_token) for endpoint in endpoint_list
            ]
            required_results = await asyncio.gather(*required_tasks, return_exceptions=True)

            required_failures: list[str] = []
            critical_failed = False
            auth_failure_detected = False
            for endpoint, response in zip(endpoint_list, required_results):
                endpoint_path = endpoint[1]
                is_critical = (
                    "performance" in endpoint_path and endpoint[0] == "v3"
                ) or endpoint_path == "portfolios"

                if isinstance(response, Exception):
                    required_failures.append(f"{endpoint_path}: {response}")
                    if is_critical:
                        critical_failed = True
                    continue

                if response is None:
                    required_failures.append(f"{endpoint_path}: returned None")
                    if is_critical:
                        critical_failed = True
                    continue

                if not isinstance(response, dict):
                    required_failures.append(
                        f"{endpoint_path}: unexpected type {type(response)}"
                    )
                    if is_critical:
                        critical_failed = True
                    continue

                if "error" in response:
                    # Detect global cooldown conditions before marking failure
                    if self._is_lockout(response):
                        self._register_lockout(SHARESIGHT_LOCKOUT_COOLDOWN)
                        raise ConfigEntryAuthFailed(
                            "Sharesight API reported a brute-force lockout — "
                            "credentials may have been invalidated."
                        )
                    if self._is_rate_limited(response):
                        # Back off for a minute when we hit the parallel limit.
                        self._register_lockout(timedelta(minutes=1))

                    error_msg = str(response.get("error", "")).lower()
                    status_code = self._response_status(response)
                    required_failures.append(
                        f"{endpoint_path}: {response.get('error')}"
                    )
                    if status_code == 404 and is_critical:
                        raise ConfigEntryAuthFailed(
                            f"Portfolio {self.portfolio_id} is no longer "
                            "accessible. Please reconfigure the integration."
                        )
                    if status_code in (401, 403) or (
                        status_code is None and "invalid_grant" in error_msg
                    ):
                        auth_failure_detected = True
                    if is_critical:
                        critical_failed = True
                    continue

                _LOGGER.debug(
                    "Response for %s: %s",
                    endpoint_path,
                    list(response.keys()) if isinstance(response, dict) else type(response),
                )
                extension = endpoint[3]
                if extension:
                    response = {extension: response}
                combined_dict = merge_dicts(combined_dict, response)

            if auth_failure_detected:
                raise ConfigEntryAuthFailed(
                    "Sharesight API returned an authentication error — "
                    "re-authentication required"
                )

            if required_failures:
                failure_preview = "; ".join(required_failures[:3])
                if critical_failed:
                    if self.data:
                        _LOGGER.warning(
                            "Critical Sharesight endpoint(s) failed (%s total): %s. "
                            "Keeping last good data.",
                            len(required_failures),
                            failure_preview,
                        )
                        return self.data
                    raise UpdateFailed(
                        f"Required Sharesight endpoints failed: {failure_preview}"
                    )
                _LOGGER.warning(
                    "Some Sharesight endpoints failed (%s): %s. "
                    "Continuing with available data.",
                    len(required_failures),
                    failure_preview,
                )

            # Carry forward slow-cadence performance windows that were skipped
            # this poll (Feature 4) or failed to fetch, using the proven
            # diversity carry-forward idiom so period sensors never flap.
            for slow_key in ("financial-year", "one-month", "ytd"):
                if slow_key not in combined_dict and slow_key in self.data:
                    combined_dict[slow_key] = self.data[slow_key]

            # --- Optional endpoints (with per-endpoint cooldown) ----------
            # Cooldowns are keyed on path + extension because the same path
            # can be polled twice with different params (e.g. past vs
            # upcoming payouts) and must back off independently.
            active_optional = [
                endpoint
                for endpoint in optional_endpoint_list
                if not self._endpoint_on_cooldown(f"{endpoint[1]}#{endpoint[3]}")
            ]
            _LOGGER.debug(
                "Calling %s optional endpoints in parallel (%s on cooldown)",
                len(active_optional),
                len(optional_endpoint_list) - len(active_optional),
            )
            optional_tasks = [
                self._call_endpoint(endpoint, access_token) for endpoint in active_optional
            ]
            optional_results = await asyncio.gather(*optional_tasks, return_exceptions=True)

            for endpoint, result in zip(active_optional, optional_results):
                endpoint_path = endpoint[1]
                extension = endpoint[3]
                cooldown_key = f"{endpoint_path}#{extension}"

                if isinstance(result, Exception):
                    _LOGGER.info(
                        "Optional endpoint %s failed: %s, backing off",
                        endpoint_path,
                        result,
                    )
                    self._note_optional_failure(cooldown_key)
                    continue

                response = result
                # The V3 /value series can answer with a bare top-level array.
                # Wrap it under a "data" key so it clears the dict-shape guard
                # below and its normaliser (_value_series_points) can peel it
                # like the nested-list shapes; otherwise a valid list response
                # would be discarded here and the value-trend sensors would
                # never come online.
                if extension == "value_series" and isinstance(response, list):
                    response = {"data": response}
                if response is None or not isinstance(response, dict):
                    _LOGGER.info(
                        "Optional endpoint %s returned %s, backing off",
                        endpoint_path,
                        type(response).__name__,
                    )
                    self._note_optional_failure(cooldown_key)
                    continue
                if "error" in response:
                    if self._is_lockout(response):
                        self._register_lockout(SHARESIGHT_LOCKOUT_COOLDOWN)
                    elif self._is_rate_limited(response):
                        self._register_lockout(timedelta(minutes=1))
                    _LOGGER.info(
                        "Optional endpoint %s returned error %s, backing off",
                        endpoint_path,
                        response.get("error"),
                    )
                    self._note_optional_failure(cooldown_key)
                    continue

                self._note_optional_success(cooldown_key)
                if extension:
                    response = {extension: response}
                combined_dict = merge_dicts(combined_dict, response)

            # --- Per-account cash transactions (optional) ----------------
            cash_accounts_data = combined_dict.get("cash_accounts_v2", {})
            cash_accounts: list[dict[str, Any]] = []
            if isinstance(cash_accounts_data, dict):
                cash_accounts = cash_accounts_data.get("cash_accounts", []) or []

            cash_account_transactions: list[dict[str, Any]] = []
            if cash_accounts:
                tx_work: list[tuple[int, list[Any]]] = []
                for account in cash_accounts:
                    account_id = account.get("id")
                    account_portfolio_id = account.get("portfolio_id")
                    if (
                        account_id is None
                        or str(account_portfolio_id) != str(self.portfolio_id)
                    ):
                        continue
                    if self._cash_tx_on_cooldown(account_id):
                        continue
                    endpoint = [
                        "v2",
                        f"cash_accounts/{account_id}/cash_account_transactions",
                        None,
                        False,
                    ]
                    tx_work.append((account_id, endpoint))

                if tx_work:
                    tx_tasks = [
                        self._call_endpoint(endpoint, access_token)
                        for _, endpoint in tx_work
                    ]
                    tx_results = await asyncio.gather(*tx_tasks, return_exceptions=True)

                    for (account_id, endpoint), tx_result in zip(tx_work, tx_results):
                        tx_endpoint_path = endpoint[1]
                        if isinstance(tx_result, Exception):
                            _LOGGER.info(
                                "Optional cash account transactions endpoint %s failed: %s",
                                tx_endpoint_path,
                                tx_result,
                            )
                            self._note_cash_tx_failure(account_id)
                            continue

                        tx_response = tx_result
                        if not isinstance(tx_response, dict) or "error" in tx_response:
                            self._note_cash_tx_failure(account_id)
                            continue
                        tx_list = tx_response.get("cash_account_transactions", [])
                        if isinstance(tx_list, list):
                            cash_account_transactions.extend(tx_list)
                        self._note_cash_tx_success(account_id)

            combined_dict["cash_account_transactions"] = {
                "cash_account_transactions": cash_account_transactions
            }

            # --- Post-process merged data --------------------------------
            _LOGGER.debug("Data keys available: %s", list(combined_dict.keys()))

            if self._portfolio_detail:
                combined_dict["portfolio_detail"] = self._portfolio_detail

            report_data = combined_dict.get("report", {})
            report_holdings = report_data.get("holdings", [])

            sub_totals = report_data.get("sub_totals", [])
            if sub_totals:
                seen_groups: set[str] = set()
                deduped_sub_totals: list[dict[str, Any]] = []
                for st in sub_totals:
                    gn = st.get("group_name", "")
                    if gn not in seen_groups:
                        seen_groups.add(gn)
                        deduped_sub_totals.append(st)
                if len(deduped_sub_totals) < len(sub_totals):
                    combined_dict["report"]["sub_totals"] = deduped_sub_totals

            report_cash_accounts = report_data.get("cash_accounts", [])
            if report_cash_accounts:
                seen_cash_names: set[str] = set()
                deduped_cash: list[dict[str, Any]] = []
                for ca in report_cash_accounts:
                    cn = ca.get("name", "")
                    if cn not in seen_cash_names:
                        seen_cash_names.add(cn)
                        deduped_cash.append(ca)
                if len(deduped_cash) < len(report_cash_accounts):
                    combined_dict["report"]["cash_accounts"] = deduped_cash

            holdings_from_api = combined_dict.get("holdings", {})
            if report_holdings:
                combined_dict["holdings"] = {
                    "holdings": report_holdings,
                    "value": report_data.get("value", 0),
                }
            elif isinstance(holdings_from_api, dict) and "error" not in holdings_from_api:
                api_holdings_list = holdings_from_api.get("holdings", [])
                if api_holdings_list:
                    total_val = sum(
                        float(h.get("value", 0) or h.get("market_value", 0) or 0)
                        for h in api_holdings_list
                    )
                    combined_dict["holdings"] = {
                        "holdings": api_holdings_list,
                        "value": total_val or report_data.get("value", 0),
                    }
                else:
                    combined_dict["holdings"] = {"holdings": [], "value": 0}
            else:
                combined_dict["holdings"] = {"holdings": [], "value": 0}

            # Build income_report from payouts when available; else fallback.
            payouts_data = combined_dict.get("payouts", {})
            payouts: list[dict[str, Any]] = []
            if isinstance(payouts_data, dict):
                payouts = payouts_data.get("payouts", []) or []

            if payouts:
                combined_dict["income_report"] = {
                    "payouts": payouts,
                    "total_income": sum(
                        float(p.get("amount", 0) or 0)
                        for p in payouts
                        if isinstance(p, dict)
                    ),
                }
            else:
                combined_dict["income_report"] = {
                    "payout_gain": report_data.get("payout_gain"),
                    "payouts": [],
                }

            # Announced-but-unpaid dividends from the forward payouts window.
            upcoming_data = combined_dict.get("upcoming_payouts", {})
            upcoming_list: list[dict[str, Any]] = []
            if isinstance(upcoming_data, dict):
                upcoming_list = upcoming_data.get("payouts", []) or []
            combined_dict["income_report"]["upcoming_payouts"] = [
                p for p in upcoming_list if isinstance(p, dict)
            ]

            # Build diversity breakdown.  Sharesight's diversity_v2 endpoint
            # occasionally returns an empty/partial payload (especially when
            # a poll coincides with a token refresh), which would otherwise
            # collapse the breakdown to [] and flap dependent sensors to
            # "unavailable" for one cycle.  Carry the previous breakdown
            # forward whenever the freshly built one is empty.
            breakdown: list[dict[str, Any]] = []
            diversity_v2 = combined_dict.get("diversity_v2", {})
            if isinstance(diversity_v2, dict) and "groups" in diversity_v2:
                for group_entry in diversity_v2.get("groups", []):
                    if not isinstance(group_entry, dict):
                        continue
                    for group_name, group_payload in group_entry.items():
                        if not isinstance(group_payload, dict):
                            continue
                        breakdown.append(
                            {
                                "group_name": group_name,
                                "percentage": group_payload.get("percentage"),
                                "value": group_payload.get("value"),
                            }
                        )

            if not breakdown:
                sub_totals = report_data.get("sub_totals", [])
                if sub_totals:
                    total_value = float(report_data.get("value", 1) or 1)
                    for st in sub_totals:
                        st_value = float(st.get("value", 0) or 0)
                        pct = (st_value / total_value * 100) if total_value else 0
                        breakdown.append(
                            {
                                "group_name": st.get("group_name", ""),
                                "percentage": round(pct, 2),
                                "value": st_value,
                            }
                        )

            if not breakdown:
                previous_diversity = self.data.get("diversity") if self.data else None
                if (
                    isinstance(previous_diversity, dict)
                    and previous_diversity.get("breakdown")
                ):
                    _LOGGER.debug(
                        "Diversity breakdown empty this poll — preserving "
                        "previous breakdown (%s entries) to avoid sensor flap",
                        len(previous_diversity["breakdown"]),
                    )
                    combined_dict["diversity"] = previous_diversity
                else:
                    combined_dict["diversity"] = {"breakdown": []}
            else:
                combined_dict["diversity"] = {"breakdown": breakdown}

            trades_data = combined_dict.get("trades", {})
            if not (
                trades_data
                and isinstance(trades_data, dict)
                and "error" not in trades_data
            ):
                combined_dict["trades"] = {"trades": []}

            # --- Activity events (Feature 2, no extra API calls) ---------
            # Diff this poll's records against the previous poll and stage HA
            # events for the event platform to emit.  A diff error must never
            # sink the poll, so guard it defensively.
            try:
                self._build_activity_events(combined_dict, today)
            except (ValueError, TypeError, KeyError, AttributeError) as activity_err:
                _LOGGER.debug("Activity event diff failed: %s", activity_err)

            # --- Derived analytics (no extra API calls) ------------------
            # These mine data already fetched this poll into per-holding and
            # portfolio-level maps that many sensors consume.  Failures here
            # must never sink the whole poll, so guard defensively.
            try:
                holdings_list = combined_dict.get("holdings", {}).get("holdings", [])

                instrument_lookup = analytics.build_instrument_lookup(
                    combined_dict.get("user_instruments", {})
                )
                combined_dict["instrument_lookup"] = instrument_lookup

                combined_dict["holding_income"] = analytics.build_holding_income(
                    combined_dict.get("income_report", {}).get("payouts", []),
                    holdings_list,
                    today,
                )
                combined_dict["holding_trades"] = analytics.build_holding_trades(
                    combined_dict.get("trades", {}).get("trades", []),
                    holdings_list,
                )
                combined_dict["sector_allocation"] = analytics.build_sector_allocation(
                    holdings_list, instrument_lookup, axis="sector"
                )
                combined_dict["industry_allocation"] = analytics.build_sector_allocation(
                    holdings_list, instrument_lookup, axis="industry"
                )
                combined_dict["portfolio_analytics"] = analytics.build_portfolio_analytics(
                    holdings_list,
                    instrument_lookup,
                    combined_dict.get("report", {}),
                    today,
                )
                # 30-day value trend (W6).  Gate on the optional endpoint's key
                # (like totals) so a parked token that never fetches the series
                # sees no value_trend rather than an empty/misleading one.
                if "value_series" in combined_dict:
                    combined_dict["value_trend"] = analytics.build_value_trend(
                        combined_dict.get("value_series")
                    )
                # Label allocation (W7).  Only assigned when at least one holding
                # actually carries a label, so portfolios without labels grow no
                # key (and Stage 3 creates no device/entities).
                label_allocation = analytics.build_label_allocation(holdings_list)
                if label_allocation:
                    combined_dict["label_allocation"] = label_allocation
                # Merge the forward-income forecast into income_report without
                # clobbering the payouts/totals already assembled above.
                forecast = analytics.build_income_forecast(
                    combined_dict.get("income_report", {}).get("upcoming_payouts", []),
                    combined_dict.get("holding_income", {}),
                    combined_dict.get("report", {}).get("value"),
                    today,
                )
                income_report = combined_dict.setdefault("income_report", {})
                for forecast_key, forecast_value in forecast.items():
                    income_report.setdefault(forecast_key, forecast_value)
            except (ValueError, TypeError, KeyError, AttributeError) as analytics_err:
                _LOGGER.debug("Derived analytics failed: %s", analytics_err)

            # Refresh the financial year bounds if the portfolio list has it.
            portfolios_list = combined_dict.get("portfolios", [])
            if isinstance(portfolios_list, list) and portfolios_list:
                fy_end = (portfolios_list[0] or {}).get("financial_year_end")
                sofy_date, eofy_date = get_financial_year_dates(fy_end)
                if self.end_financial_year != eofy_date:
                    self.end_financial_year = eofy_date
                    self.start_financial_year = sofy_date

            # Count only genuinely successful polls so the slow-window cadence
            # (Feature 4) advances once per fetch, never on kept-last-good paths.
            self._poll_count += 1
            self.data = combined_dict
            return self.data

        except ConfigEntryAuthFailed:
            raise
        except (
            aiohttp.ClientError,
            OSError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            KeyError,
        ) as err:
            if self.data:
                _LOGGER.warning(
                    "Error in coordinator update (%s), keeping last good data",
                    err,
                    exc_info=True,
                )
                return self.data
            _LOGGER.error("Error in coordinator update: %s", err, exc_info=True)
            raise UpdateFailed(f"Error fetching Sharesight data: {err}") from err
