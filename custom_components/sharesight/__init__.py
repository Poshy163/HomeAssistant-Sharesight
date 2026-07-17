"""The Sharesight integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from SharesightAPI.SharesightAPI import SharesightAPI

from .application_credentials import account_type_context
from .const import (
    ACCOUNT_DEVELOPER,
    ACCOUNT_STANDARD,
    API_URL_BASE,
    CONF_ACCOUNT_TYPE,
    CONF_ENABLE_LTS_BACKFILL,
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_ENABLE_LTS_BACKFILL,
    DOMAIN,
    PLATFORMS,
    TOKEN_URL,
)
from .coordinator import SharesightCoordinator
from .data import SharesightConfigEntry, SharesightRuntimeData
from .services import async_setup_services
from .statistics_import import async_backfill_value_statistics

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Sharesight response services for the process lifetime.

    action-setup (Bronze): the response services are registered once at
    integration load rather than per config entry.  ``async_setup_services`` is
    idempotent (guards on ``has_service``), and the handlers raise a clear
    ``ServiceValidationError`` when no entry is loaded, so a call made while
    unconfigured validates correctly instead of hitting an "unknown service".
    """
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    v2 -> v3: the boolean ``use_edge_url`` becomes ``account_type``.  The old
    flag only ever switched the API host — never the OAuth endpoints — so an
    entry with it set to True was authenticating as a standard account
    regardless and could never actually reach the developer deployment.
    Mapping such an entry to ACCOUNT_DEVELOPER would therefore be wrong: its
    token came from the standard host.  They are migrated to standard, which is
    what they were really using, and the user can add a fresh developer entry
    with a developer credential.
    """
    if entry.version >= 3:
        return True

    data = dict(entry.data)
    was_edge = data.pop(CONF_USE_EDGE, False)
    data[CONF_ACCOUNT_TYPE] = ACCOUNT_STANDARD

    if was_edge:
        _LOGGER.warning(
            "Sharesight entry '%s' had the old edge flag set. That flag never "
            "switched the OAuth endpoints, so the entry was authenticating as a "
            "standard account and could not have worked against the developer "
            "sandbox. It has been migrated to a standard account. For sandbox "
            "access, add a developer application credential and set up a new "
            "entry",
            entry.title,
        )

    hass.config_entries.async_update_entry(entry, data=data, version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SharesightConfigEntry) -> bool:
    """Set up Sharesight from a config entry."""
    account_type = entry.data.get(CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE)

    # Implementations are rebuilt on every async_get_implementations() call
    # rather than cached, so the account type has to be republished here or the
    # platform would hand back standard endpoints for a developer entry.  The
    # URLs are frozen into the implementation at construction, so
    # OAuth2Session's later token refreshes work fine outside the context.
    with account_type_context(account_type):
        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    hass, entry
                )
            )
        except ValueError as err:
            raise ConfigEntryNotReady(
                f"Sharesight {account_type} account credential is unavailable"
            ) from err

    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    await oauth_session.async_ensure_token_valid()

    portfolio_id = entry.data[CONF_PORTFOLIO_ID]

    api_session = async_get_clientsession(hass)

    client = SharesightAPI(
        client_id="",
        client_secret="",
        authorization_code="",
        redirect_uri="",
        token_url=TOKEN_URL[account_type],
        api_url_base=API_URL_BASE[account_type],
        use_token_file=False,
        session=api_session,
    )

    local_coordinator = SharesightCoordinator(
        hass, entry, portfolio_id, client=client, oauth_session=oauth_session
    )
    await local_coordinator.async_config_entry_first_refresh()

    # runtime-data (Bronze): store per-entry state on the entry itself.
    # Assigned BEFORE async_forward_entry_setups so every platform's
    # async_setup_entry can read entry.runtime_data.
    entry.runtime_data = SharesightRuntimeData(
        coordinator=local_coordinator,
        client=client,
        portfolio_id=portfolio_id,
        # Kept as a bool: the entity platforms only use it for naming and the
        # configuration_url host prefix.
        edge=account_type == ACCOUNT_DEVELOPER,
        market_sensors=[],
        cash_sensors=[],
        holding_sensors=[],
        last_options=dict(entry.options),
    )

    entry.async_on_unload(entry.add_update_listener(update_listener))

    # Register the portfolio hub device up front so the nested device tree is
    # deterministic. Sub-devices nest under it via DeviceInfo.via_device, but HA
    # resolves via_device only when the referencing device is created and never
    # backfills a link to a hub registered later. Platforms are set up
    # concurrently, so a sub-device (Account, Market Hours, ...) can otherwise
    # register before the sensor platform builds the hub — leaving it unnested
    # and logging a spurious "non existing via_device" warning. Name/model/URL
    # mirror the "portfolio" device the sensor/button/event/binary_sensor
    # platforms build, so async_get_or_create updates that same device rather
    # than creating a second one.
    edge = account_type == ACCOUNT_DEVELOPER
    edge_infix = " Edge " if edge else " "
    edge_host = "edge-" if edge else ""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{portfolio_id}_portfolio")},
        entry_type=dr.DeviceEntryType.SERVICE,
        name=f"Sharesight{edge_infix}Portfolio {portfolio_id}",
        model=f"Sharesight{edge_infix}API - Portfolio",
        configuration_url=(
            f"https://{edge_host}portfolio.sharesight.com/portfolios/{portfolio_id}"
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Backfill the portfolio-value long-term statistics from inception once at
    # startup (opt-out via options).  Runs in the background so it never blocks
    # setup, and is idempotent so re-running on restart is safe.
    if entry.options.get(CONF_ENABLE_LTS_BACKFILL, DEFAULT_ENABLE_LTS_BACKFILL):
        portfolios = (local_coordinator.data or {}).get("portfolios", [])
        currency = "USD"
        if portfolios and isinstance(portfolios[0], dict):
            currency = portfolios[0].get("currency_code", "USD")
        entry.async_create_background_task(
            hass,
            async_backfill_value_statistics(
                hass, local_coordinator, portfolio_id, currency
            ),
            "sharesight_lts_backfill",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SharesightConfigEntry) -> bool:
    """Unload a config entry.

    The coordinator's refresh loop is torn down for free: passing
    ``config_entry=`` to ``DataUpdateCoordinator`` registers ``async_shutdown``
    via ``entry.async_on_unload``.  Per-entry state lives on
    ``entry.runtime_data`` (cleared automatically by Home Assistant), and the
    response services are process-global (see ``async_setup``), so nothing else
    needs unwinding here.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: SharesightConfigEntry) -> None:
    """Reload only when user-facing options actually change.

    Why: HA fires update listeners for every async_update_entry call, including
    OAuth2 token refreshes that periodically write a new token into entry.data.
    Reloading on every token refresh would tear down all sensors every ~30 min.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return

    new_options = dict(entry.options)
    if runtime_data.last_options == new_options:
        return

    runtime_data.last_options = new_options
    await hass.config_entries.async_reload(entry.entry_id)


# Fixed device groups: the portfolio hub, the portfolio-wide report devices, and
# the single container devices that hold the dynamic per-item families. These
# exist for the life of a configured portfolio, so async_remove_config_entry_device
# always refuses them — only the per-item market/cash/holding devices are ever
# prunable. Mirrors sensor.py's device_group_config keys; keep in sync if a new
# fixed group is added there.
_STATIC_DEVICE_GROUPS = frozenset(
    {
        "portfolio", "daily", "weekly", "financial_year", "holdings", "income",
        "diversity", "trades", "contributions", "monthly", "ytd", "tax",
        "benchmark", "sector", "account", "watchlist", "fx", "market_hours",
        "analytics", "totals", "labels",
    }
)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: SharesightConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow the user to delete a stale Sharesight device from the UI.

    Returning True lets Home Assistant remove the device; False refuses it.

    Only the per-item market / cash / holding devices are ever prunable, and
    only when the item they represent is absent from the CURRENT coordinator
    data (a holding sold, a market exited, a cash account closed). The portfolio
    hub and every fixed report/container device (including the single Watchlist,
    Labels, Exchange Rates and Market Hours devices, whose per-item entities are
    grouped inside one device) are always refused.

    Conservative by design: this supports *user-initiated* deletion only. The
    integration never auto-prunes on data absence, because a transient API gap
    routinely omits items for a poll or two; auto-deleting on that would orphan
    live devices. When the coordinator has no data (entry unloaded / mid-outage)
    staleness cannot be proven, so every device is refused — a deliberately safe
    default the user can retry once data is back.
    """
    # Deferred import: reuse sensor.py's exact symbol resolution so a
    # reconstructed holding identifier matches byte-for-byte, without a
    # module-load import cycle (sensor imports from this package at import time).
    from .sensor import _get_holding_symbol

    runtime_data = getattr(config_entry, "runtime_data", None)
    portfolio_id = config_entry.data.get(CONF_PORTFOLIO_ID)
    coordinator = runtime_data.coordinator if runtime_data is not None else None
    data = coordinator.data if coordinator is not None else None

    if portfolio_id is None or not isinstance(data, dict):
        return False

    prefix = f"{portfolio_id}_"

    report = data.get("report")
    if not isinstance(report, dict):
        report = {}
    live_markets = {
        market.get("group_name", "Unknown Market")
        for market in report.get("sub_totals", [])
        if isinstance(market, dict)
    }
    live_cash = {
        cash.get("name", "Unknown Cash Account")
        for cash in report.get("cash_accounts", [])
        if isinstance(cash, dict)
    }
    holdings = data.get("holdings")
    holdings_list = holdings.get("holdings", []) if isinstance(holdings, dict) else []
    live_holdings = {
        symbol
        for holding in holdings_list
        if isinstance(holding, dict) and (symbol := _get_holding_symbol(holding))
    }

    # A device may carry several identifiers; refuse if ANY of ours is a fixed or
    # still-live device. Remove only when every Sharesight identifier on the
    # device resolves to a stale per-item device.
    saw_removable = False
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if not identifier.startswith(prefix):
            # Foreign / malformed identifier under our domain — keep it.
            return False
        suffix = identifier[len(prefix):]

        # Fixed devices, checked first: "market_hours" would otherwise be
        # misread as a per-market device named "hours".
        if suffix in _STATIC_DEVICE_GROUPS:
            return False

        if suffix.startswith("market_"):
            stale = suffix[len("market_"):] not in live_markets
        elif suffix.startswith("cash_"):
            stale = suffix[len("cash_"):] not in live_cash
        elif suffix.startswith("holding_"):
            stale = suffix[len("holding_"):] not in live_holdings
        else:
            # Unrecognised per-portfolio device — keep it to be safe.
            return False

        if not stale:
            return False
        saw_removable = True

    return saw_removable
