"""Long-term statistics backfill for the Sharesight portfolio value sensor.

On startup this fetches the full inception-to-today daily portfolio value
series from Sharesight and imports it into the ``Portfolio value`` sensor's
own long-term statistics, so HA history/statistics cards show years of data
instead of only the days since the integration was installed.

Design notes / safety:
- Imports into the sensor's OWN statistic_id, resolved from the entity
  registry by unique_id, via ``async_import_statistics`` so the history
  lands on the real entity even if the user has renamed it - not on a
  separate external statistic.
- Only imports days strictly BEFORE today, so it never overwrites the
  statistics the recorder compiles forward from the live sensor's state.
- Idempotent: ``async_import_statistics`` upserts by (statistic_id, start), so
  re-running on every HA restart is safe.
- Defensive: the ``portfolio_value_data.json`` endpoint is V3 "mobile"-scoped
  and may 403 for standard API tokens; on any failure this simply logs and
  returns, leaving the rest of the integration untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import math
from typing import Any

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    APP_VERSION,
    CONF_ACCOUNT_TYPE,
    CONF_PORTFOLIO_ID,
    DEFAULT_ACCOUNT_TYPE,
    DOMAIN,
    portfolio_resource_id,
)

_LOGGER = logging.getLogger(__name__)


def _extract_points(response: Any) -> list[tuple[datetime, float]]:
    """Pull (aware-datetime, value) points from the value-data response.

    Tolerates the documented shape (``chart.data[]``) and the wrapper/example
    variants, plus ``date`` vs ``timestamp`` key naming.
    """
    if not isinstance(response, dict) or "error" in response:
        return []

    data: Any = None
    for container in (
        response.get("chart"),
        response.get("portfolio_value_data"),
        (response.get("portfolio_value_data") or {}).get("chart")
        if isinstance(response.get("portfolio_value_data"), dict)
        else None,
        response,
    ):
        if isinstance(container, dict) and isinstance(container.get("data"), list):
            data = container["data"]
            break
    if data is None and isinstance(response.get("data"), list):
        data = response["data"]
    if not isinstance(data, list):
        return []

    points: list[tuple[datetime, float]] = []
    for point in data:
        if not isinstance(point, dict):
            continue
        raw = point.get("date") or point.get("timestamp") or point.get("on")
        value = point.get("value")
        if value is None:
            value = point.get("close")
        if raw is None or value is None:
            continue

        parsed = dt_util.parse_datetime(str(raw))
        if parsed is None:
            day = dt_util.parse_date(str(raw)[:10])
            if day is None:
                continue
            # Anchor a date-only point to LOCAL midnight, not UTC midnight.
            # The recorder buckets statistics by hour in local time, so
            # pinning a Sydney or New York day to 00:00 UTC shifted the whole
            # series onto the wrong calendar day for anyone not on UTC.
            parsed = dt_util.start_of_local_day(day)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = dt_util.as_utc(parsed)

        try:
            fval = float(value)
        except TypeError, ValueError:
            continue
        if not math.isfinite(fval):
            continue
        points.append((parsed, fval))
    return points


def _value_sensor_entity_id(hass: HomeAssistant, entry: Any, portfolio_id: Any) -> str | None:
    """The live entity_id of this portfolio's value sensor.

    Resolved from the entity registry by unique_id rather than reconstructed
    from the display name.  The recorder renames a statistic_id along with its
    entity, so a user who renamed the sensor (or a second portfolio whose
    entity_id got a ``_2`` suffix) had every backfill land on a statistic_id
    that belonged to nothing.
    """
    resource_id = portfolio_resource_id(
        portfolio_id,
        entry.data.get(CONF_ACCOUNT_TYPE, DEFAULT_ACCOUNT_TYPE),
    )
    unique_id = f"{resource_id}_value_{APP_VERSION}"
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, unique_id)
    if entity_id is None:
        _LOGGER.debug(
            "Portfolio value sensor (unique_id=%s) is not registered yet; "
            "skipping the statistics backfill this time",
            unique_id,
        )
    return entity_id


async def async_backfill_value_statistics(
    hass: HomeAssistant,
    entry: Any,
    coordinator: Any,
) -> None:
    """Fetch the value history and import it as long-term statistics."""
    if "recorder" not in hass.config.components:
        _LOGGER.debug("Recorder not loaded; skipping value-history backfill")
        return

    portfolio_id = entry.data.get(CONF_PORTFOLIO_ID)
    statistic_id = _value_sensor_entity_id(hass, entry, portfolio_id)
    if statistic_id is None:
        return
    currency = coordinator.portfolio_currency

    try:
        from homeassistant.components.recorder.models import StatisticData
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
        )
    except ImportError:
        _LOGGER.debug("Recorder statistics API unavailable; skipping backfill")
        return

    # HA 2025.x replaced the metadata's ``has_mean`` bool with a ``mean_type``
    # enum; ``has_mean`` is deprecated and removed in 2026.11.  Prefer
    # ``mean_type`` where available and fall back for older cores.
    try:
        from homeassistant.components.recorder.models import StatisticMeanType
    except ImportError:
        StatisticMeanType = None

    try:
        response = await coordinator.async_get_value_history()
    except Exception as err:
        _LOGGER.debug("Portfolio value history fetch failed, skipping backfill: %s", err)
        return

    if isinstance(response, dict) and "error" in response:
        _LOGGER.debug(
            "Portfolio value history endpoint unavailable (%s); skipping "
            "long-term statistics backfill",
            response.get("error"),
        )
        return

    points = _extract_points(response)
    if not points:
        _LOGGER.debug("No portfolio value history points returned; nothing to backfill")
        return

    today = dt_util.utcnow().date()
    statistics: list[StatisticData] = []
    for when, value in sorted(points):
        if when.date() >= today:
            # Leave today/forward to the recorder's live compilation.
            continue
        hour = when.replace(minute=0, second=0, microsecond=0)
        statistics.append(StatisticData(start=hour, mean=value, min=value, max=value))

    if not statistics:
        _LOGGER.debug("Value history had no pre-today points to backfill")
        return

    metadata: dict[str, Any] = {
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": currency,
        # Currency is not a convertible physical unit, so it has no unit class.
        # None matches what the recorder derives for the live sensor and
        # satisfies HA 2025.x+ which requires the key (removed-if-absent 2026.11).
        "unit_class": None,
    }
    if StatisticMeanType is not None:
        metadata["mean_type"] = StatisticMeanType.ARITHMETIC
    else:
        metadata["has_mean"] = True
    async_import_statistics(hass, metadata, statistics)
    _LOGGER.info(
        "Backfilled %s long-term statistics points into %s",
        len(statistics),
        statistic_id,
    )
