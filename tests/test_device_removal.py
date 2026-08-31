"""Regression tests for user-initiated Sharesight device removal."""

from __future__ import annotations

from types import SimpleNamespace

import custom_components.sharesight as init_module
from custom_components.sharesight import (
    _enable_integration_disabled_entities,
    _is_retired_legacy_device,
)
from custom_components.sharesight.const import DOMAIN


def test_retired_market_hours_device_is_explicitly_removable() -> None:
    device = SimpleNamespace(identifiers={(DOMAIN, "1020131_market_hours")})

    assert _is_retired_legacy_device(device, "1020131_")


def test_active_or_foreign_device_is_not_treated_as_retired() -> None:
    active_device = SimpleNamespace(identifiers={(DOMAIN, "1020131_portfolio")})
    foreign_device = SimpleNamespace(
        identifiers={(DOMAIN, "1020131_market_hours"), ("other", "device")}
    )

    assert not _is_retired_legacy_device(active_device, "1020131_")
    assert not _is_retired_legacy_device(foreign_device, "1020131_")


def test_previously_default_disabled_entities_are_reenabled(monkeypatch) -> None:
    """Only the integration's old default, never a user preference, is reset."""
    integration_disabled = SimpleNamespace(
        entity_id="sensor.sharesight_niche",
        disabled_by=init_module.er.RegistryEntryDisabler.INTEGRATION,
    )
    user_disabled = SimpleNamespace(
        entity_id="sensor.sharesight_user_choice",
        disabled_by=init_module.er.RegistryEntryDisabler.USER,
    )
    updates: list[tuple[str, object]] = []

    registry = SimpleNamespace(
        async_update_entity=lambda entity_id, **kwargs: updates.append(
            (entity_id, kwargs["disabled_by"])
        )
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        init_module.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: [integration_disabled, user_disabled],
    )

    _enable_integration_disabled_entities(
        object(), SimpleNamespace(entry_id="entry", pref_disable_new_entities=False)
    )

    assert updates == [("sensor.sharesight_niche", None)]
