"""The Sharesight integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
        "market_sensors": [],
        "cash_sensors": [],
        "holding_sensors": [],
        "last_options": dict(entry.options),
    }

    entry.async_on_unload(entry.add_update_listener(update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        unsub = domain_data.get("update_sensors_unsub")
        if unsub:
            unsub()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of a config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)
    if not domain_data:
        hass.data.pop(DOMAIN, None)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when user-facing options actually change.

    Why: HA fires update listeners for every async_update_entry call, including
    OAuth2 token refreshes that periodically write a new token into entry.data.
    Reloading on every token refresh would tear down all sensors every ~30 min.
    """
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if domain_data is None:
        return

    new_options = dict(entry.options)
    if domain_data.get("last_options") == new_options:
        return

    domain_data["last_options"] = new_options
    await hass.config_entries.async_reload(entry.entry_id)
