"""Resolve the bundled ``icons.json`` so entities can publish their own icon.

Home Assistant resolves icon translations in the *frontend*, not the backend.
``Entity._async_calculate_state`` only ever writes ``attributes.icon`` from the
entity-registry override or the entity's own ``icon`` property::

    if (icon := (entry and entry.icon) or self.icon) is not None:
        attr[EntityStateAttribute.ICON] = icon

An icon declared in ``icons.json`` therefore never reaches the state machine —
the HA dashboard fetches it separately over the ``frontend/get_icons``
websocket command.  Anything else that consumes entity states (Stream Deck
plugins, REST/MQTT bridges, custom dashboards) only sees ``attributes.icon``,
finds it missing, and falls back to its own generic per-domain default.  That
is why hand-setting an icon on an entity in the HA UI "fixes" those clients:
the registry override *does* land in ``attributes.icon``.

Loading ``icons.json`` once at setup and handing the result to the entities
(see ``SharesightBaseEntity.icon``) puts the same icon into ``attributes.icon``
for every consumer, while keeping ``icons.json`` the single source of truth so
the frontend and the quality scale's icon-translations rule are unaffected.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.icon import async_get_icons

from .const import DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__package__)

# icons.json entries that vary the icon by state (``state``, ``range``) or that
# describe an attribute rather than the entity itself (``state_attributes``)
# cannot be collapsed into the single value ``attributes.icon`` has room for.
# Mirroring their ``default`` would pin the frontend to that default and
# silently kill the per-state icons, so those entries are skipped and keep
# relying on the frontend's own resolution.  Nothing in our icons.json uses
# them today; this only guards future additions.
_STATE_DEPENDENT_KEYS = frozenset({"state", "range", "state_attributes"})


async def async_load_entity_icons(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Return ``{platform: {translation_key: icon}}`` from our ``icons.json``.

    ``platform`` is the entity domain (``sensor``, ``binary_sensor``, ...), so
    a translation key only ever resolves against the platform that declared it.
    Results come from HA's own icon cache, so calling this once per config
    entry costs a single dict lookup after the first load.
    """
    icons: dict[str, Any] = await async_get_icons(
        hass, "entity", integrations=[DOMAIN]
    )
    entity_icons = icons.get(DOMAIN) or {}

    resolved: dict[str, dict[str, str]] = {}
    for platform, translation_keys in entity_icons.items():
        if not isinstance(translation_keys, dict):
            continue
        for translation_key, entry in translation_keys.items():
            if not isinstance(entry, dict) or _STATE_DEPENDENT_KEYS & entry.keys():
                continue
            if isinstance(icon := entry.get("default"), str):
                resolved.setdefault(platform, {})[translation_key] = icon

    if not resolved:
        # Not fatal: entities simply keep the pre-existing behaviour of leaving
        # attributes.icon unset and letting the frontend resolve the icon.
        _LOGGER.debug("No entity icons resolved from icons.json")
    return resolved
