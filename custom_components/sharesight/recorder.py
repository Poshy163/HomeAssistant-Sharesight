"""Recorder compatibility helpers for Sharesight statistics.

Home Assistant deliberately refuses to continue a statistic when a custom
unit changes and it cannot prove the old and new units are equivalent.  Two
Sharesight migrations are safe to declare as equivalent because the numeric
values never changed:

* Foreign instrument prices were historically labelled with the portfolio
  currency even though the state was already the instrument-currency value.
* Two count sensors originally had no unit and later gained descriptive
  ``holdings`` / ``instruments`` units.

Declaring only those exact, entity-scoped transitions lets recorder relabel
the existing series on its next statistics run without deleting history or
globally claiming that different currencies are interchangeable.
"""

from __future__ import annotations

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_INSTRUMENT_CURRENCY_TRANSLATIONS = frozenset(
    {
        "holding_eps",
        "holding_instrument_price",
        "holding_nta",
        "holding_vwap_buy_price",
    }
)

_COUNT_UNIT_MIGRATIONS = {
    "effective_number_of_holdings": "holdings",
    "stale_price_count": "instruments",
}


@callback
def async_custom_equivalent_units(
    hass: HomeAssistant,
) -> dict[str, dict[str | None, str]]:
    """Return narrowly-scoped unit migrations for recorder statistics."""
    registry = er.async_get(hass)
    equivalent: dict[str, dict[str | None, str]] = {}

    for config_entry in hass.config_entries.async_entries(DOMAIN):
        runtime_data = getattr(config_entry, "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        try:
            portfolio_currency = getattr(coordinator, "portfolio_currency", None)
        except (AttributeError, TypeError, ValueError):
            # A not-yet-loaded or degraded entry must not prevent recorder
            # collecting safe migrations from every other Sharesight entry.
            portfolio_currency = None

        for registry_entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            if not registry_entry.entity_id.startswith("sensor."):
                continue
            state = hass.states.get(registry_entry.entity_id)
            if state is None:
                continue
            state_unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            translation_key = registry_entry.translation_key

            if translation_key in _INSTRUMENT_CURRENCY_TRANSLATIONS:
                if (
                    isinstance(portfolio_currency, str)
                    and isinstance(state_unit, str)
                    and state_unit != portfolio_currency
                ):
                    equivalent[registry_entry.entity_id] = {
                        portfolio_currency: state_unit,
                        state_unit: state_unit,
                    }
                continue

            expected_unit = _COUNT_UNIT_MIGRATIONS.get(translation_key)
            if expected_unit is not None and state_unit == expected_unit:
                equivalent[registry_entry.entity_id] = {
                    None: expected_unit,
                    expected_unit: expected_unit,
                }

    return equivalent
