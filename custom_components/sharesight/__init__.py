"""The Sharesight integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from SharesightAPI.SharesightAPI import SharesightAPI

from .const import (
    API_URL_BASE,
    CONF_PORTFOLIO_ID,
    CONF_USE_EDGE,
    DOMAIN,
    EDGE_API_URL_BASE,
    EDGE_TOKEN_URL,
    PLATFORMS,
    TOKEN_URL,
)
from .coordinator import SharesightCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sharesight from a config entry."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    await oauth_session.async_ensure_token_valid()

    portfolio_id = entry.data[CONF_PORTFOLIO_ID]
    use_edge = entry.data.get(CONF_USE_EDGE, False)

    api_url = EDGE_API_URL_BASE if use_edge else API_URL_BASE
    token_url = EDGE_TOKEN_URL if use_edge else TOKEN_URL

    # Reuse HA's shared aiohttp client session so we inherit its lifecycle,
    # connection pooling, and SSL context.  Per-request timeouts are applied
    # inside the coordinator.
    api_session = async_get_clientsession(hass)

    client = SharesightAPI(
        client_id="",
        client_secret="",
        authorization_code="",
        redirect_uri="",
        token_url=token_url,
        api_url_base=api_url,
        use_token_file=False,
        session=api_session,
    )

    local_coordinator = SharesightCoordinator(
        hass, entry, portfolio_id, client=client, oauth_session=oauth_session
    )
    await local_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": local_coordinator,
        "portfolio_id": portfolio_id,
        "edge": use_edge,
        "sharesight_client": client,
        # Per-entry sensor tracking — used by sensor platform setup to avoid
        # creating duplicate market/cash/holding entities.
        "market_sensors": [],
        "cash_sensors": [],
        "holding_sensors": [],
        # Snapshot of options used to detect real options changes vs. token
        # refreshes inside the update listener.
        "last_options": dict(entry.options),
    }

    entry.async_on_unload(entry.add_update_listener(update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_clear_recorder_repair_issues(hass, entry)
    # Recorder compiles long-term stats roughly every 5 min and may (re)raise
    # state_class / unit issues after our initial clear. Re-clear a few times
    # over the first ~30 min so the user doesn't have to act on transient
    # repairs caused by upgrading this integration.
    for delay in (360, 900, 1800):
        entry.async_on_unload(
            async_call_later(
                hass,
                delay,
                lambda _now, h=hass, e=entry: _async_clear_recorder_repair_issues(h, e),
            )
        )
    return True


def _async_clear_recorder_repair_issues(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Unblock long-term statistics for entities owned by this entry.

    HA's sensor recorder raises persistent issues like ``state_class_removed_<eid>``
    and ``units_changed_<eid>`` whenever an entity's reported state_class / unit
    differs from what was previously recorded in ``statistics_meta``. While the
    issue exists, the recorder *silently skips that entity on every LTS compile
    cycle* — no min/max/mean datapoints are ever written. Dismissing the repair
    card via ``ir.async_delete_issue`` alone is not enough: the underlying
    ``statistics_meta`` row stays stale, the recorder re-raises the same issue
    on the next compile, and LTS remains broken forever.

    This integration has shipped monetary sensors with three different
    state_class / device_class combinations over its lifetime (MEASUREMENT+
    MONETARY → TOTAL+MONETARY → MEASUREMENT+None), so almost every existing
    install has stale ``statistics_meta`` rows for entities like
    ``sensor.portfolio_value_<portfolio_id>`` blocking LTS.

    Clear the stale recorder statistics for those entities so the recorder
    treats them as brand-new on the next compile and starts recording LTS
    again. Then dismiss the repair cards.
    """
    try:
        registry = er.async_get(hass)
    except Exception:  # noqa: BLE001 — never let cleanup break setup
        return

    entity_ids = [
        ent.entity_id
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id)
    ]
    if not entity_ids:
        return

    # Detect which of our entities currently have a recorder repair issue
    # against them. We only wipe stats for those — leaving healthy entities
    # untouched preserves their historical LTS.
    stuck_entity_ids: list[str] = []
    try:
        issue_registry = ir.async_get(hass)
    except Exception:  # noqa: BLE001
        issue_registry = None

    if issue_registry is not None:
        for entity_id in entity_ids:
            for issue_id in (
                f"state_class_removed_{entity_id}",
                f"units_changed_{entity_id}",
            ):
                if issue_registry.async_get_issue("sensor", issue_id) is not None:
                    stuck_entity_ids.append(entity_id)
                    break
    else:
        # Fall back to clearing all of our entities if we couldn't read the
        # issue registry — safer than leaving LTS broken indefinitely.
        stuck_entity_ids = list(entity_ids)

    if stuck_entity_ids:
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import clear_statistics

            recorder_instance = get_instance(hass)
            recorder_instance.async_add_executor_job(
                clear_statistics, recorder_instance, stuck_entity_ids
            )
            _LOGGER.info(
                "Cleared stale recorder statistics for %d Sharesight entit%s "
                "so long-term statistics can begin recording again: %s",
                len(stuck_entity_ids),
                "y" if len(stuck_entity_ids) == 1 else "ies",
                ", ".join(stuck_entity_ids),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not clear stale recorder statistics (recorder may be "
                "unavailable): %s", err
            )

    for entity_id in entity_ids:
        for issue_id in (
            f"state_class_removed_{entity_id}",
            f"units_changed_{entity_id}",
        ):
            try:
                ir.async_delete_issue(hass, "sensor", issue_id)
            except Exception:  # noqa: BLE001
                continue


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

        # Cancel any coordinator listeners registered by the sensor platform.
        unsub = domain_data.get("update_sensors_unsub")
        if unsub:
            unsub()

        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _LOGGER.debug("Unloaded platforms for entry %s", entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of a config entry."""
    _LOGGER.info("Removing Sharesight integration: %s", entry.entry_id)
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)
    if not domain_data:
        hass.data.pop(DOMAIN, None)
    _LOGGER.info("Successfully removed Sharesight integration: %s", entry.entry_id)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle entry updates — reload only when user-facing options change.

    Home Assistant fires update listeners for *any* `async_update_entry` call,
    including the OAuth2 token-refresh that periodically writes a new token
    into `entry.data`. Reloading on every token refresh tears down all
    sensors (briefly marking them unavailable) every ~30 minutes. Compare
    the entry's options snapshot and only reload when it actually changes.
    """
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if domain_data is None:
        return

    new_options = dict(entry.options)
    last_options = domain_data.get("last_options")
    if last_options == new_options:
        # Token refresh or other non-options data update — nothing to do.
        return

    domain_data["last_options"] = new_options
    await hass.config_entries.async_reload(entry.entry_id)
