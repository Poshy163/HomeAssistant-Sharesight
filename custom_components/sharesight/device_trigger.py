"""Device triggers for the Sharesight portfolio device.

Two families of triggers are offered on the Portfolio device:

- Activity-event triggers (one per activity event type) — attach to the
  portfolio's ``event`` entity via a state-change listener filtered on the
  fired ``event_type`` attribute, so a specific activity kind can be
  automated against directly from the device automation UI.
- Numeric-threshold triggers ("Portfolio value" / "Daily change") — delegate
  to the core ``numeric_state`` trigger against the resolved sensor entity, so
  users get the standard above/below fields with all their edge handling.

The numeric target sensors are resolved from the entity registry by their
stable unique_id rather than a stored entity_id, so a user renaming the entity
never breaks the trigger.
"""

from __future__ import annotations

from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import (
    numeric_state as numeric_state_trigger,
)
from homeassistant.const import (
    CONF_ABOVE,
    CONF_BELOW,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import APP_VERSION, DOMAIN
from .event import EVENT_TYPES

# EventEntity surfaces the last fired type under this state attribute; kept as
# a literal so this module has no import-order dependency on the event
# component being loaded first.
ATTR_EVENT_TYPE = "event_type"

# Activity-event trigger types, taken straight from the event platform so the
# two cannot drift.
TRIGGER_TYPES_EVENT = tuple(EVENT_TYPES)
# Numeric-threshold trigger types.
TRIGGER_TYPE_PORTFOLIO_VALUE = "portfolio_value"
TRIGGER_TYPE_DAILY_CHANGE = "daily_change"
TRIGGER_TYPES_NUMERIC = (TRIGGER_TYPE_PORTFOLIO_VALUE, TRIGGER_TYPE_DAILY_CHANGE)
TRIGGER_TYPES = (*TRIGGER_TYPES_EVENT, *TRIGGER_TYPES_NUMERIC)


def _require_bound_for_numeric(config: ConfigType) -> ConfigType:
    """Reject a numeric trigger that carries neither above nor below.

    Core ``numeric_state`` enforces has_at_least_one_key(above, below); without
    this guard such a config saves fine but raises a raw ``vol.Invalid`` at
    attach time, leaving the whole automation unavailable.  Failing here gives
    a clear message and blocks the bad config up front.
    """
    if config[CONF_TYPE] in TRIGGER_TYPES_NUMERIC and (
        CONF_ABOVE not in config and CONF_BELOW not in config
    ):
        raise vol.Invalid(
            f"Sharesight '{config[CONF_TYPE]}' trigger requires at least one of "
            f"'{CONF_ABOVE}' or '{CONF_BELOW}'"
        )
    return config


TRIGGER_SCHEMA = vol.All(
    DEVICE_TRIGGER_BASE_SCHEMA.extend(
        {
            vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
            vol.Optional(CONF_ABOVE): vol.Coerce(float),
            vol.Optional(CONF_BELOW): vol.Coerce(float),
        }
    ),
    _require_bound_for_numeric,
)


def _portfolio_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Extract the portfolio id from a Sharesight portfolio device.

    Returns None when the device is not the portfolio device, so only that
    device advertises these triggers.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.endswith("_portfolio"):
            return identifier[: -len("_portfolio")]
    return None


def _numeric_target_unique_id(portfolio_id: str, trigger_type: str) -> str:
    """unique_id of the sensor a numeric trigger watches."""
    if trigger_type == TRIGGER_TYPE_PORTFOLIO_VALUE:
        # SharesightSensor mints report/value as "{id}_value_{APP_VERSION}".
        return f"{portfolio_id}_value_{APP_VERSION}"
    # Daily Change Amount is an "Extension" sensor: "{id}_one-day_total_gain".
    return f"{portfolio_id}_one-day_total_gain_{APP_VERSION}"


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    """List device triggers for a Sharesight portfolio device."""
    portfolio_id = _portfolio_id_for_device(hass, device_id)
    if portfolio_id is None:
        return []

    ent_reg = er.async_get(hass)
    triggers: list[dict[str, str]] = []

    def _base(trigger_type: str) -> dict[str, str]:
        return {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }

    # Activity-event triggers only when the event entity exists.
    event_uid = f"{portfolio_id}_activity_events_{APP_VERSION}"
    if ent_reg.async_get_entity_id("event", DOMAIN, event_uid):
        triggers.extend(_base(trigger_type) for trigger_type in TRIGGER_TYPES_EVENT)

    # Numeric triggers only when their target sensor exists.
    for trigger_type in TRIGGER_TYPES_NUMERIC:
        target_uid = _numeric_target_unique_id(portfolio_id, trigger_type)
        if ent_reg.async_get_entity_id("sensor", DOMAIN, target_uid):
            triggers.append(_base(trigger_type))

    return triggers


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Offer above/below fields for the numeric-threshold triggers only."""
    if config[CONF_TYPE] not in TRIGGER_TYPES_NUMERIC:
        return {}
    return {
        "extra_fields": vol.Schema(
            {
                vol.Optional(CONF_ABOVE): vol.Coerce(float),
                vol.Optional(CONF_BELOW): vol.Coerce(float),
            }
        )
    }


async def _attach_event_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire when the portfolio event entity reports a matching event_type."""
    trigger_type = config[CONF_TYPE]
    device_id = config[CONF_DEVICE_ID]
    portfolio_id = _portfolio_id_for_device(hass, device_id)
    if portfolio_id is None:
        raise InvalidDeviceAutomationConfig(
            f"Device {device_id} is not a Sharesight portfolio",
            translation_domain=DOMAIN,
            translation_key="device_not_portfolio",
            translation_placeholders={"device_id": str(device_id)},
        )
    event_uid = f"{portfolio_id}_activity_events_{APP_VERSION}"
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, event_uid)
    if entity_id is None:
        raise InvalidDeviceAutomationConfig(
            f"No Sharesight activity event entity for portfolio {portfolio_id}",
            translation_domain=DOMAIN,
            translation_key="no_event_entity",
            translation_placeholders={"portfolio_id": str(portfolio_id)},
        )

    job = HassJob(action)
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle_state_change(event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if new_state.attributes.get(ATTR_EVENT_TYPE) != trigger_type:
            return
        hass.async_run_hass_job(
            job,
            {
                "trigger": {
                    **trigger_data,
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_ENTITY_ID: entity_id,
                    CONF_TYPE: trigger_type,
                    "event_type": trigger_type,
                    "description": f"Sharesight event '{trigger_type}'",
                }
            },
            new_state.context,
        )

    return async_track_state_change_event(hass, [entity_id], _handle_state_change)


async def _attach_numeric_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Delegate to the core numeric_state trigger against the target sensor."""
    trigger_type = config[CONF_TYPE]
    device_id = config[CONF_DEVICE_ID]
    portfolio_id = _portfolio_id_for_device(hass, device_id)
    if portfolio_id is None:
        raise InvalidDeviceAutomationConfig(
            f"Device {device_id} is not a Sharesight portfolio",
            translation_domain=DOMAIN,
            translation_key="device_not_portfolio",
            translation_placeholders={"device_id": str(device_id)},
        )
    target_uid = _numeric_target_unique_id(portfolio_id, trigger_type)
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, target_uid)
    if entity_id is None:
        raise InvalidDeviceAutomationConfig(
            f"No target sensor for Sharesight trigger '{trigger_type}'",
            translation_domain=DOMAIN,
            translation_key="no_target_sensor",
            translation_placeholders={"trigger_type": str(trigger_type)},
        )

    if CONF_ABOVE not in config and CONF_BELOW not in config:
        raise InvalidDeviceAutomationConfig(
            f"Sharesight '{trigger_type}' trigger requires at least one of "
            f"'{CONF_ABOVE}' or '{CONF_BELOW}'",
            translation_domain=DOMAIN,
            translation_key="numeric_trigger_needs_bound",
            translation_placeholders={
                "trigger_type": str(trigger_type),
                "above": CONF_ABOVE,
                "below": CONF_BELOW,
            },
        )

    numeric_state_config = {
        CONF_PLATFORM: "numeric_state",
        CONF_ENTITY_ID: entity_id,
    }
    if CONF_ABOVE in config:
        numeric_state_config[CONF_ABOVE] = config[CONF_ABOVE]
    if CONF_BELOW in config:
        numeric_state_config[CONF_BELOW] = config[CONF_BELOW]

    numeric_state_config = await numeric_state_trigger.async_validate_trigger_config(
        hass, numeric_state_config
    )
    return await numeric_state_trigger.async_attach_trigger(
        hass, numeric_state_config, action, trigger_info, platform_type="device"
    )


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a Sharesight device trigger."""
    if config[CONF_TYPE] in TRIGGER_TYPES_EVENT:
        return await _attach_event_trigger(hass, config, action, trigger_info)
    return await _attach_numeric_trigger(hass, config, action, trigger_info)
