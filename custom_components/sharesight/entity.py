"""Shared base entity for the Sharesight integration.

Centralises the two things every Sharesight platform (sensor, binary_sensor,
button, event, calendar) previously duplicated inline:

* the coordinator hand-off (``CoordinatorEntity.__init__``), and
* the identical ``DeviceInfo`` boilerplate.

All Sharesight entities are grouped into HA "service" devices whose identifier
is ``{portfolio_id}_{group}``.  Display names and models carry an " Edge "
infix for developer (edge) portfolios and " " otherwise, and the configuration
URL points at the standard or edge web host accordingly.

Subclasses keep full ownership of their ``unique_id``, ``entity_id`` and name;
this base only removes duplication and must not change any of those values.
"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SharesightCoordinator


class SharesightBaseEntity(CoordinatorEntity[SharesightCoordinator]):
    """Base class wiring the coordinator and building the device grouping."""

    # Modern HA naming: the entity's display name is the device name plus the
    # per-entity (translated) name.  Subclasses supply a ``translation_key``
    # (sensors via their description, the other platforms via a class attr);
    # they keep hand-assigning ``entity_id`` and ``unique_id`` unchanged, so no
    # existing entity is renamed or orphaned by this switch.
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SharesightCoordinator, portfolio_id, edge: bool
    ) -> None:
        super().__init__(coordinator)
        self._portfolio_id = portfolio_id
        self._edge = edge
        # " Edge " for developer/edge portfolios, " " otherwise — the exact
        # infix the platforms have always used inside display names/models.
        self._edge_name = " Edge " if edge else " "
        # "edge-" host prefix for the portfolio's configuration URL.
        self._edge_url = "edge-" if edge else ""

    @property
    def _configuration_url(self) -> str:
        """Deep link to this portfolio on the (edge or standard) web app."""
        return (
            f"https://{self._edge_url}portfolio.sharesight.com"
            f"/portfolios/{self._portfolio_id}"
        )

    def _make_device_info(
        self, *, identifier: str, name: str, model: str
    ) -> DeviceInfo:
        """Wrap a fully-resolved (identifier, name, model) into a DeviceInfo.

        Used where the caller has already resolved the device's display name and
        model (e.g. sensor.py's ``device_group_config`` and its dynamic
        market/cash/holding groups).  Centralises the constant service
        entry-type, the ``DOMAIN`` identifier namespacing and the configuration
        URL, leaving the name/model strings untouched.

        Every device except the portfolio hub is nested under it via
        ``via_device`` so the fleet forms a tree on the HA device page.  The hub
        (``{portfolio_id}_portfolio``) is the tree root and must not reference
        itself, so it is the one device that gets no ``via_device``.
        """
        hub_identifier = f"{self._portfolio_id}_portfolio"
        device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, identifier)},
            configuration_url=self._configuration_url,
            model=model,
            name=name,
        )
        # HA resolves ``via_device`` only when the referencing device is created
        # and never backfills a link to a hub registered later, so
        # __init__.async_setup_entry ensures the hub device exists before any
        # platform adds entities (guaranteeing this reference always resolves).
        if identifier != hub_identifier:
            device_info["via_device"] = (DOMAIN, hub_identifier)
        return device_info

    def _service_device_info(
        self, group: str, label: str, model_suffix: str
    ) -> DeviceInfo:
        """Build the standard ``Sharesight[ Edge ]<label>`` service device.

        ``group`` is appended to the portfolio id to form the device identifier
        (``{portfolio_id}_{group}``); ``label`` is the display-name suffix and
        ``model_suffix`` the model suffix, both carrying the edge infix.  This
        reproduces, byte-for-byte, the DeviceInfo the platforms built inline.
        """
        return self._make_device_info(
            identifier=f"{self._portfolio_id}_{group}",
            name=f"Sharesight{self._edge_name}{label}",
            model=f"Sharesight{self._edge_name}API - {model_suffix}",
        )
