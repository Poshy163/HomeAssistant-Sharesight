"""The Sharesight integration."""

from __future__ import annotations

from functools import partial
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from SharesightAPI.SharesightAPI import SharesightAPI

from .api import SharesightRequestGate
from .application_credentials import account_type_context
from .const import (
    ACCOUNT_DEVELOPER,
    ACCOUNT_STANDARD,
    API_URL_BASE,
    CONF_ACCOUNT_TYPE,
    CONF_AUTO_REMOVE_STALE_DEVICES,
    CONF_ENABLE_LTS_BACKFILL,
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_AUTO_REMOVE_STALE_DEVICES,
    DEFAULT_ENABLE_LTS_BACKFILL,
    DOMAIN,
    PLATFORMS,
    STALE_DEVICE_POLL_CONFIRMATIONS,
    TOKEN_URL,
    portfolio_resource_id,
)
from .coordinator import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    SharesightCoordinator,
    oauth_response_requires_reauth,
)
from .data import SharesightConfigEntry, SharesightRuntimeData
from .icons import async_load_entity_icons
from .services import async_setup_services
from .statistics_import import (
    async_backfill_value_statistics,
    async_migrate_statistics_metadata,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# entry_id -> the options snapshot the running setup was built from.  Kept
# module-level rather than on runtime_data because Home Assistant clears
# runtime_data during unload, and the update listener has to be able to
# compare against the previous options even mid-reload.
_LAST_OPTIONS: dict[str, dict[str, Any]] = {}


def _migrate_developer_registry_identity(
    hass: HomeAssistant, entry: ConfigEntry, portfolio_id: Any
) -> None:
    """Namespace pre-fix developer registry rows without losing history."""
    old_prefix = f"{portfolio_id}_"
    resource_id = portfolio_resource_id(portfolio_id, ACCOUNT_DEVELOPER)
    new_prefix = f"{resource_id}_"

    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not registry_entry.unique_id.startswith(old_prefix):
            continue
        try:
            entity_registry.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=new_prefix + registry_entry.unique_id[len(old_prefix) :],
            )
        except ValueError:
            _LOGGER.warning(
                "Could not namespace legacy developer entity %s because the target ID already exists",
                registry_entry.entity_id,
            )

    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        # If a historical collision already attached both standard and
        # developer entries to one device, leave it for the entity platform to
        # split; changing the shared identifier would steal the standard device.
        if set(device.config_entries) != {entry.entry_id}:
            continue
        changed = False
        identifiers: set[tuple[str, str]] = set()
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith(old_prefix):
                identifier = new_prefix + identifier[len(old_prefix) :]
                changed = True
            identifiers.add((domain, identifier))
        if changed:
            device_registry.async_update_device(device.id, new_identifiers=identifiers)


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


# Keys a pre-OAuth (VERSION 1) entry carried.  They are meaningless now — the
# integration authenticates through Application Credentials — and leaving them
# behind would let a stale client_secret sit in .storage forever.
_LEGACY_CREDENTIAL_KEYS = (
    "client_id",
    "client_secret",
    "authorization_code",
    "redirect_uri",
)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    v1 -> v3: entries created before 1.8.0 stored a raw client id/secret and
    let the SharesightAPI library manage its own token file.  They have no
    ``auth_implementation`` and no ``token``, so simply stamping them v3 left
    ``async_setup_entry`` to die on a KeyError.  The stale credentials are
    dropped, the portfolio id is kept, and setup raises ``ConfigEntryAuthFailed``
    so the user gets a reauth card instead of a traceback — the reauth flow
    merges the new token into the existing entry, preserving every entity.

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
        # HA never calls this for a newer entry than the handler supports, but
        # be explicit rather than silently rewriting a future schema.
        return True

    data = dict(entry.data)
    was_edge = data.pop(CONF_USE_EDGE, False)
    data[CONF_ACCOUNT_TYPE] = ACCOUNT_STANDARD

    if "auth_implementation" not in data or "token" not in data:
        for key in _LEGACY_CREDENTIAL_KEYS:
            data.pop(key, None)
        _LOGGER.warning(
            "Sharesight entry '%s' predates OAuth support. Its stored "
            "credentials have been discarded; Home Assistant will ask you to "
            "re-authenticate, which keeps all existing entities and history",
            entry.title,
        )
    elif was_edge:
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
    # Checked BEFORE resolving the implementation.  A migrated pre-OAuth entry
    # has neither key, and async_get_config_entry_implementation indexes
    # entry.data["auth_implementation"] directly - so reaching it first raised
    # a bare KeyError, which is neither ConfigEntryAuthFailed nor
    # ConfigEntryNotReady and therefore produced a dead entry with a traceback
    # and no reauth prompt.
    if "auth_implementation" not in entry.data or "token" not in entry.data:
        raise ConfigEntryAuthFailed(
            "Sharesight now authenticates with OAuth. Please re-authenticate this portfolio."
        )

    with account_type_context(account_type):
        try:
            implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
        except (ValueError, KeyError) as err:
            # The credential the entry was created against has been deleted.
            # Retriable rather than fatal: the user may be re-adding it.
            raise ConfigEntryNotReady(
                f"Sharesight {account_type} account credential is unavailable"
            ) from err

    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    try:
        await oauth_session.async_ensure_token_valid()
    except OAuth2TokenRequestReauthError as err:
        # A 4xx from the token endpoint: the grant is gone for good, so prompt
        # for reauth instead of retrying forever.
        raise ConfigEntryAuthFailed(f"Sharesight rejected the stored credentials: {err}") from err
    except (OAuth2TokenRequestError, aiohttp.ClientResponseError) as err:
        if oauth_response_requires_reauth(err):
            raise ConfigEntryAuthFailed(
                f"Sharesight rejected the stored credentials: {err}"
            ) from err
        raise ConfigEntryNotReady(f"Could not reach the Sharesight token endpoint: {err}") from err
    except (
        aiohttp.ClientError,
        OSError,
        TimeoutError,
    ) as err:
        # Anything transient — Sharesight 5xx, DNS not up yet at boot — has to
        # be retriable.  Left unguarded this raised straight out of setup and
        # parked the entry in SETUP_ERROR with no retry and no reauth prompt.
        raise ConfigEntryNotReady(f"Could not reach the Sharesight token endpoint: {err}") from err

    portfolio_id = entry.data[CONF_PORTFOLIO_ID]
    edge = account_type == ACCOUNT_DEVELOPER
    resource_id = portfolio_resource_id(portfolio_id, account_type)
    if edge:
        _migrate_developer_registry_identity(hass, entry, portfolio_id)

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
        # Without this the library swallows the HTTP status and hands back the
        # error body verbatim — and Sharesight's error envelope carries no
        # status at all, which left every status-gated defence in the
        # coordinator (rate limiting, lockout, 404, 401/403) unreachable.
        raise_for_status=True,
        # The coordinator owns retry/cooldown policy. Library sleeps (especially
        # Retry-After >= 60s) would otherwise be misreported as endpoint timeout.
        max_retries=0,
    )

    domain_data = hass.data.setdefault(DOMAIN, {})
    request_gates: dict[tuple[str, str], SharesightRequestGate] = domain_data.setdefault(
        "request_gates", {}
    )
    gate_key = (account_type, str(entry.data.get("auth_implementation", "default")))
    request_gate = request_gates.setdefault(gate_key, SharesightRequestGate())

    local_coordinator = SharesightCoordinator(
        hass,
        entry,
        portfolio_id,
        client=client,
        oauth_session=oauth_session,
        request_gate=request_gate,
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
    )

    _LAST_OPTIONS[entry.entry_id] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # Register the portfolio hub device up front so the nested device tree is
    # deterministic. Sub-devices nest under it via DeviceInfo.via_device, but HA
    # resolves via_device only when the referencing device is created and never
    # backfills a link to a hub registered later. Platforms are set up
    # concurrently, so a sub-device (Account, Watchlist, ...) can otherwise
    # register before the sensor platform builds the hub — leaving it unnested
    # and logging a spurious "non existing via_device" warning. Name/model/URL
    # mirror the "portfolio" device the sensor/button/event/binary_sensor
    # platforms build, so async_get_or_create updates that same device rather
    # than creating a second one.
    edge_infix = " Edge " if edge else " "
    edge_host = "edge-" if edge else ""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{resource_id}_portfolio")},
        entry_type=dr.DeviceEntryType.SERVICE,
        name=f"Sharesight{edge_infix}Portfolio {portfolio_id}",
        model=f"Sharesight{edge_infix}API - Portfolio",
        configuration_url=(
            f"https://{edge_host}portfolio.sharesight.com/portfolios/{portfolio_id}"
        ),
    )

    # Resolve icons.json into {platform: {translation_key: icon}} before the
    # platforms are forwarded, so every entity can mirror its icon into
    # attributes.icon from its very first state write (see icons.py).
    local_coordinator.entity_icons = await async_load_entity_icons(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Update recorder metadata for statistics contracts that changed without
    # deleting their history. This is idempotent and queues no statistic rows.
    await async_migrate_statistics_metadata(hass, entry)

    # Auto-remove devices for sold holdings / exited markets / closed cash
    # accounts (opt-in).  Registered after the platforms so the first prune
    # scan sees the devices this load created, and detached on unload.
    if entry.options.get(CONF_AUTO_REMOVE_STALE_DEVICES, DEFAULT_AUTO_REMOVE_STALE_DEVICES):
        entry.async_on_unload(
            local_coordinator.async_add_listener(partial(_async_prune_stale_devices, hass, entry))
        )
        # Count this load's data as the first confirmation instead of idling
        # until the next poll.  Setup only gets this far after a successful
        # first refresh, so it is evidence of the same quality as a poll's —
        # and it means enabling the option (which reloads the entry) starts
        # the count immediately rather than a full interval later.
        _async_prune_stale_devices(hass, entry)

    # Backfill the portfolio-value long-term statistics from inception once at
    # startup (opt-out via options).  Runs in the background so it never blocks
    # setup, and is idempotent so re-running on restart is safe.
    if entry.options.get(CONF_ENABLE_LTS_BACKFILL, DEFAULT_ENABLE_LTS_BACKFILL):
        entry.async_create_background_task(
            hass,
            async_backfill_value_statistics(hass, entry, local_coordinator),
            "sharesight_lts_backfill",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SharesightConfigEntry) -> bool:
    """Unload a config entry.

    The coordinator's refresh loop is torn down for free: passing
    ``config_entry=`` to ``DataUpdateCoordinator`` registers ``async_shutdown``
    via ``entry.async_on_unload``.  Per-entry state lives on
    ``entry.runtime_data`` (cleared automatically by Home Assistant), and the
    response services are process-global (see ``async_setup``). The options
    snapshot is integration-owned, so release it after a successful unload
    instead of retaining removed entry ids forever.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        _LAST_OPTIONS.pop(entry.entry_id, None)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: SharesightConfigEntry) -> None:
    """Reload only when user-facing options actually change.

    Why: HA fires update listeners for every async_update_entry call, including
    OAuth2 token refreshes that periodically write a new token into entry.data.
    Reloading on every token refresh would tear down all sensors every ~30 min.

    The comparison is against a snapshot rather than the runtime data, because
    HA deletes ``runtime_data`` while the entry is unloading — reading it there
    used to drop an options change that landed mid-reload.
    """
    snapshot = _LAST_OPTIONS.get(entry.entry_id)
    new_options = dict(entry.options)
    if snapshot == new_options:
        return

    _LAST_OPTIONS[entry.entry_id] = new_options
    if snapshot is None:
        # First call for this entry (the snapshot is seeded at setup), so there
        # is nothing to reload for.
        return
    await hass.config_entries.async_reload(entry.entry_id)


# Fixed device groups: the portfolio hub, portfolio-wide report devices, and
# single container devices that hold dynamic per-item families. The retired
# ``fx`` and ``market_hours`` suffixes remain here solely to protect legacy
# registry devices from stale-item pruning; ``market_hours`` would otherwise be
# parsed as the per-market device ``market_hours``. Only per-item
# market/cash/holding devices are ever prunable.
_STATIC_DEVICE_GROUPS = frozenset(
    {
        "portfolio",
        "daily",
        "weekly",
        "financial_year",
        "holdings",
        "income",
        "diversity",
        "trades",
        "contributions",
        "monthly",
        "ytd",
        "tax",
        "benchmark",
        "sector",
        "account",
        "watchlist",
        "fx",
        "market_hours",
        "extended",
        "analytics",
        "totals",
        "labels",
    }
)


def _live_item_names(data: Any) -> dict[str, set[str]] | None:
    """Per-item device families currently present in the portfolio.

    Keyed by the device-identifier prefix that introduces each family, so a
    device suffix can be matched against the right set.  None when the payload
    can't support any judgement at all (no data / entry unloaded).
    """
    if not isinstance(data, dict):
        return None

    # Deferred import: reuse sensor.py's exact symbol resolution so a
    # reconstructed holding identifier matches byte-for-byte, without a
    # module-load import cycle (sensor imports from this package at import time).
    from .sensor import _get_holding_symbol

    report = data.get("report")
    if not isinstance(report, dict):
        report = {}
    holdings = data.get("holdings")
    holdings_list = holdings.get("holdings", []) if isinstance(holdings, dict) else []
    return {
        "market_": {
            market.get("group_name", "Unknown Market")
            for market in report.get("sub_totals", [])
            if isinstance(market, dict)
        },
        "cash_": {
            cash.get("name", "Unknown Cash Account")
            for cash in report.get("cash_accounts", [])
            if isinstance(cash, dict)
        },
        "holding_": {
            symbol
            for holding in holdings_list
            if isinstance(holding, dict) and (symbol := _get_holding_symbol(holding))
        },
    }


def _stale_item(
    device_entry: dr.DeviceEntry,
    prefix: str,
    live: dict[str, set[str]],
    *,
    require_evidence: bool,
) -> tuple[str, str] | None:
    """The (family, item name) this device represents, if it is stale.

    None means "keep": a fixed device, an unrecognised identifier, an item
    that is still live, or — when ``require_evidence`` is set — a family whose
    list came back empty, which is a short payload rather than proof that
    every item in it is gone.  A device carrying several identifiers is only
    stale when every one of them is.
    """
    stale_item: tuple[str, str] | None = None
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if not identifier.startswith(prefix):
            # Foreign / malformed identifier under our domain — keep it.
            return None
        suffix = identifier[len(prefix) :]

        # Fixed devices, checked first: "market_hours" would otherwise be
        # misread as a per-market device named "hours".
        if suffix in _STATIC_DEVICE_GROUPS:
            return None

        for family, names in live.items():
            if suffix.startswith(family):
                item = suffix[len(family) :]
                if item in names or (require_evidence and not names):
                    return None
                stale_item = (family, item)
                break
        else:
            # Unrecognised per-portfolio device — keep it to be safe.
            return None

    return stale_item


@callback
def _async_prune_stale_devices(hass: HomeAssistant, entry: SharesightConfigEntry) -> None:
    """Delete per-item devices whose item has left the portfolio (opt-in).

    Runs after every coordinator refresh when the user has enabled
    ``CONF_AUTO_REMOVE_STALE_DEVICES``.  A device is only removed after its
    item has been missing from ``STALE_DEVICE_POLL_CONFIRMATIONS`` consecutive
    *successful* polls, and never on the strength of an empty list — Sharesight
    occasionally returns a short payload without erroring, and a sold holding
    is not worth deleting a live one over.  Removing the device removes its
    entities (and their history) with it, which is why this is opt-in.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return
    coordinator = runtime_data.coordinator
    if not coordinator.last_update_success:
        return
    live = _live_item_names(coordinator.data)
    if live is None:
        return

    portfolio_id = entry.data.get(CONF_PORTFOLIO_ID)
    if portfolio_id is None:
        return
    prefix = f"{portfolio_resource_id(portfolio_id, entry.data.get(CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE))}_"

    strikes = runtime_data.stale_device_strikes
    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        stale = _stale_item(device_entry, prefix, live, require_evidence=True)
        if stale is None:
            strikes.pop(device_entry.id, None)
            continue

        polls = strikes.get(device_entry.id, 0) + 1
        if polls < STALE_DEVICE_POLL_CONFIRMATIONS:
            strikes[device_entry.id] = polls
            _LOGGER.debug(
                "Sharesight device '%s' looks stale (%s of %s confirmations)",
                device_entry.name_by_user or device_entry.name,
                polls,
                STALE_DEVICE_POLL_CONFIRMATIONS,
            )
            continue

        strikes.pop(device_entry.id, None)
        _family, item = stale
        _LOGGER.info(
            "Removing Sharesight device '%s': %s has been absent from portfolio "
            "%s for %s consecutive updates",
            device_entry.name_by_user or device_entry.name,
            item,
            portfolio_id,
            polls,
        )
        # Drop the item's entity names from the platform's "already created"
        # guards, so buying back in recreates its entities on the next poll
        # instead of waiting for a restart.
        for created in (
            runtime_data.holding_sensors,
            runtime_data.market_sensors,
            runtime_data.cash_sensors,
        ):
            dropped = [name for name in created if str(name).startswith(f"{item} ")]
            created[:] = [name for name in created if name not in dropped]
            runtime_data.created_unique_ids.difference_update(dropped)
        device_registry.async_update_device(device_entry.id, remove_config_entry_id=entry.entry_id)


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
    hub and every fixed report/container device are always refused. This also
    protects legacy Exchange Rates and Market Hours registry devices, even
    though those unsupported entity families are no longer created.

    Conservative by design. Deletion here is *user-initiated*, so an item
    missing from a single poll is enough — the user is asserting the device is
    finished with. The unattended path (``_async_prune_stale_devices``, opt-in
    via CONF_AUTO_REMOVE_STALE_DEVICES) holds itself to a stricter standard:
    several consecutive confirmations and never on an empty list. When the
    coordinator has no data (entry unloaded / mid-outage) staleness cannot be
    proven either way, so every device is refused — a safe default the user can
    retry once data is back.
    """
    runtime_data = getattr(config_entry, "runtime_data", None)
    portfolio_id = config_entry.data.get(CONF_PORTFOLIO_ID)
    coordinator = runtime_data.coordinator if runtime_data is not None else None
    live = _live_item_names(coordinator.data if coordinator is not None else None)

    if portfolio_id is None or live is None:
        return False

    return (
        _stale_item(
            device_entry,
            f"{portfolio_resource_id(portfolio_id, config_entry.data.get(CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE))}_",
            live,
            require_evidence=False,
        )
        is not None
    )
