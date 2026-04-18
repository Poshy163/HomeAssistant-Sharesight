"""Data update coordinator for the Sharesight integration."""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
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


class SharesightCoordinator(DataUpdateCoordinator):
    """Coordinate polling of the Sharesight API for a single portfolio."""

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
            update_interval=_get_scan_interval(entry),
        )
        self.entry = entry
        self.sharesight = client
        self.oauth_session = oauth_session
        self.update_method = self._async_update_data
        self.data: dict = {}
        self.portfolio_id = portfolio_id
        self.startup_endpoint = ["v3", f"portfolios/{self.portfolio_id}", None, False]
        self.started_up = False

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

        if not self.started_up:
            try:
                local_data = await self._call_endpoint(self.startup_endpoint, access_token)
                if not isinstance(local_data, dict) or "error" in local_data:
                    status = self._response_status(local_data)
                    if status == 404:
                        # The portfolio has been deleted or the user lost
                        # access — ask for a full reauth/reconfigure.
                        raise ConfigEntryAuthFailed(
                            f"Portfolio {self.portfolio_id} is no longer "
                            "accessible. Please reconfigure the integration."
                        )
                    raise ValueError(f"Invalid startup response: {local_data}")
                self.start_financial_year, self.end_financial_year = get_financial_year_dates(
                    local_data.get("portfolio", {}).get("financial_year_end")
                )
                self._portfolio_detail = local_data.get("portfolio", {}) or {}
                self.started_up = True
            except ConfigEntryAuthFailed:
                raise
            except (
                aiohttp.ClientError,
                OSError,
                asyncio.TimeoutError,
                ValueError,
            ) as startup_error:
                if self.data:
                    _LOGGER.warning(
                        "Startup request failed (%s), keeping last good data",
                        startup_error,
                    )
                    return self.data
                raise UpdateFailed(
                    f"Error during Sharesight startup fetch: {startup_error}"
                ) from startup_error

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
            ["v3", "portfolios", None, False],
            [
                "v3",
                f"portfolios/{self.portfolio_id}/performance",
                performance_params,
                False,
            ],
        ]

        optional_endpoint_list: list[list[Any]] = [
            ["v3", f"portfolios/{self.portfolio_id}/holdings", None, "holdings"],
            ["v2", f"portfolios/{self.portfolio_id}/payouts", None, "payouts"],
            ["v2", f"portfolios/{self.portfolio_id}/diversity", None, "diversity_v2"],
            ["v2", f"portfolios/{self.portfolio_id}/trades", None, "trades"],
            ["v2", "cash_accounts", None, "cash_accounts_v2"],
            ["v3", f"portfolios/{self.portfolio_id}/user_setting", None, "user_setting"],
            ["v2", "user_instruments", None, "user_instruments"],
        ]

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

            # --- Optional endpoints (with per-endpoint cooldown) ----------
            active_optional = [
                endpoint
                for endpoint in optional_endpoint_list
                if not self._endpoint_on_cooldown(endpoint[1])
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

                if isinstance(result, Exception):
                    _LOGGER.info(
                        "Optional endpoint %s failed: %s, backing off",
                        endpoint_path,
                        result,
                    )
                    self._note_optional_failure(endpoint_path)
                    continue

                response = result
                if response is None or not isinstance(response, dict):
                    _LOGGER.info(
                        "Optional endpoint %s returned %s, backing off",
                        endpoint_path,
                        type(response).__name__,
                    )
                    self._note_optional_failure(endpoint_path)
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
                    self._note_optional_failure(endpoint_path)
                    continue

                self._note_optional_success(endpoint_path)
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

            # Refresh the financial year bounds if the portfolio list has it.
            portfolios_list = combined_dict.get("portfolios", [])
            if isinstance(portfolios_list, list) and portfolios_list:
                fy_end = (portfolios_list[0] or {}).get("financial_year_end")
                sofy_date, eofy_date = get_financial_year_dates(fy_end)
                if self.end_financial_year != eofy_date:
                    self.end_financial_year = eofy_date
                    self.start_financial_year = sofy_date

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
