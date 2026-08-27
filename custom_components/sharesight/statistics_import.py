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
- Only imports days strictly BEFORE today and only hours that do not already
  exist, so it never replaces recorder's time-weighted live statistics.
- Idempotent: existing metadata is checked for an exact contract match and
  existing hourly rows are skipped on every run.
- Defensive: the ``portfolio_value_data.json`` endpoint is V3 "mobile"-scoped
  and may 403 for standard API tokens; on any failure this simply logs and
  returns, leaving the rest of the integration untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorEntityCapabilityAttribute,
    SensorStateClass,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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

_COUNT_UNIT_METADATA_MIGRATIONS = {
    "effective_number_of_holdings": "holdings",
    "stale_price_count": "instruments",
}


async def async_migrate_statistics_metadata(hass: HomeAssistant, entry: Any) -> None:
    """Migrate known-safe recorder metadata without deleting history.

    ``holding_franking_ttm`` used to be declared TOTAL even though it is a
    moving 365-day measurement. Numeric entities migrate automatically on the
    next recorder cycle, but an Unknown entity with legacy sum metadata would
    otherwise retain a permanent ``mean_type_changed`` repair. Importing no
    rows while supplying the new metadata updates that exact series in place;
    existing rows and statistic IDs are preserved.

    Two measurement counts also gained descriptive units after initially
    recording as unitless. HA treats their old ``None`` unit as a convertible
    ratio before it consults integration-provided unit equivalence, so these
    exact metadata labels are migrated here without changing numeric rows.
    """
    if "recorder" not in hass.config.components:
        return

    try:
        from homeassistant.components.recorder.models import (
            StatisticMeanType,
        )
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
            async_list_statistic_ids,
        )
    except ImportError:
        return

    registry = er.async_get(hass)
    candidates: dict[str, tuple[str, str]] = {}
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not registry_entry.entity_id.startswith("sensor."):
            continue
        translation_key = registry_entry.translation_key
        is_franking = translation_key == "holding_franking_ttm"
        expected_count_unit = _COUNT_UNIT_METADATA_MIGRATIONS.get(translation_key)
        if not is_franking and expected_count_unit is None:
            continue
        state = hass.states.get(registry_entry.entity_id)
        if (
            state is None
            or state.attributes.get(SensorEntityCapabilityAttribute.STATE_CLASS)
            != SensorStateClass.MEASUREMENT
        ):
            continue
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if not isinstance(unit, str):
            continue
        if expected_count_unit is not None and unit != expected_count_unit:
            continue
        candidates[registry_entry.entity_id] = (translation_key, unit)

    if not candidates:
        return

    try:
        existing_rows = await async_list_statistic_ids(hass, set(candidates))
    except (HomeAssistantError, RuntimeError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Cannot inspect legacy franking statistics metadata; leaving it unchanged: %s",
            err,
        )
        return
    existing_by_id = {row.get("statistic_id"): row for row in existing_rows}

    for entity_id, (translation_key, unit) in candidates.items():
        existing = existing_by_id.get(entity_id)
        if existing is None:
            # No history exists; recorder will create correct metadata with
            # the first numeric state.
            continue
        existing_mean_type = existing.get("mean_type")
        already_current = (
            existing.get("source") == "recorder"
            and existing.get("statistics_unit_of_measurement") == unit
            and existing.get("unit_class") is None
            and existing_mean_type == StatisticMeanType.ARITHMETIC
            and existing.get("has_sum") is False
        )
        if already_current:
            continue
        if translation_key == "holding_franking_ttm":
            safe_legacy = (
                existing.get("source") == "recorder"
                and existing.get("statistics_unit_of_measurement") == unit
                and existing.get("unit_class") is None
                and existing_mean_type in (None, StatisticMeanType.NONE)
                and existing.get("has_sum") is True
            )
            legacy_contract = "franking TOTAL"
        else:
            safe_legacy = (
                existing.get("source") == "recorder"
                and existing.get("statistics_unit_of_measurement") is None
                and existing.get("unit_class") in (None, "unitless")
                and existing_mean_type == StatisticMeanType.ARITHMETIC
                and existing.get("has_sum") is False
            )
            legacy_contract = "unitless count"
        if not safe_legacy:
            _LOGGER.warning(
                "Not migrating statistic %s because its existing "
                "metadata (%r, mean=%r, sum=%r) is not the audited legacy "
                "%s contract; history was left unchanged",
                entity_id,
                existing.get("statistics_unit_of_measurement"),
                existing_mean_type,
                existing.get("has_sum"),
                legacy_contract,
            )
            continue
        try:
            async_import_statistics(
                hass,
                {
                    "has_sum": False,
                    "mean_type": StatisticMeanType.ARITHMETIC,
                    "name": None,
                    "source": "recorder",
                    "statistic_id": entity_id,
                    "unit_class": None,
                    "unit_of_measurement": unit,
                },
                (),
            )
        except (HomeAssistantError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Recorder rejected the metadata migration for %s; its "
                "history was left unchanged: %s",
                entity_id,
                err,
            )


def _extract_points(
    response: Any,
    start_of_day: Callable[[date], datetime] | None = None,
) -> list[tuple[datetime, float]]:
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

    date_start = start_of_day or dt_util.start_of_local_day
    points: list[tuple[datetime, float]] = []
    for point in data:
        if not isinstance(point, dict):
            continue
        raw = point.get("date") or point.get("timestamp") or point.get("on")
        value = point.get("value")
        if value is None:
            value = point.get("close")
        if raw is None or value is None or isinstance(value, bool):
            continue

        raw_text = str(raw).strip()
        # HA's parse_datetime accepts a bare YYYY-MM-DD as a naive midnight,
        # so test the exact date form first. Sharesight's documented daily
        # value series belongs to the portfolio calendar, not UTC or HA's
        # configured timezone.
        day = dt_util.parse_date(raw_text)
        date_only = day is not None
        if date_only:
            parsed = date_start(day)
        else:
            parsed = dt_util.parse_datetime(raw_text)
            if parsed is None:
                continue
        if parsed.tzinfo is None:
            # Treat an offset-less source timestamp in the same portfolio
            # timezone as date-only source values.
            parsed = parsed.replace(tzinfo=date_start(parsed.date()).tzinfo)
        parsed = dt_util.as_utc(parsed)
        if date_only and (parsed.minute or parsed.second or parsed.microsecond):
            # Recorder accepts hourly rows on UTC hour boundaries. In a
            # half-hour/quarter-hour portfolio timezone, use the first UTC
            # hour that still falls on the source calendar day (00:30/00:45
            # portfolio time), rather than flooring into the previous day.
            parsed = (parsed + timedelta(hours=1)).replace(
                minute=0,
                second=0,
                microsecond=0,
            )

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
    try:
        currency = coordinator.portfolio_currency
    except (AttributeError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Cannot backfill %s because its report currency is unavailable: %s",
            statistic_id,
            err,
        )
        return

    live_state = hass.states.get(statistic_id)
    live_unit = (
        live_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if live_state is not None else None
    )
    if live_unit != currency:
        _LOGGER.warning(
            "Not backfilling %s: live unit %r does not match report currency %r",
            statistic_id,
            live_unit,
            currency,
        )
        return

    try:
        from homeassistant.components.recorder.models import StatisticData
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
            async_list_statistic_ids,
            statistics_during_period,
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

    intended_mean_type = StatisticMeanType.ARITHMETIC if StatisticMeanType is not None else None
    try:
        existing_metadata = await async_list_statistic_ids(hass, {statistic_id})
    except (HomeAssistantError, RuntimeError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Cannot inspect existing recorder metadata for %s; skipping "
            "backfill without changing history: %s",
            statistic_id,
            err,
        )
        return
    if existing_metadata:
        existing = next(
            (item for item in existing_metadata if item.get("statistic_id") == statistic_id),
            None,
        )
        if existing is not None:
            existing_mean_type = existing.get("mean_type")
            if existing_mean_type is None and existing.get("has_mean"):
                existing_mean_type = intended_mean_type
            metadata_matches = (
                existing.get("source") == "recorder"
                and existing.get("statistics_unit_of_measurement") == currency
                and existing.get("unit_class") is None
                and existing.get("has_sum") is False
                and (intended_mean_type is None or existing_mean_type == intended_mean_type)
            )
            if not metadata_matches:
                _LOGGER.warning(
                    "Not backfilling %s because existing recorder metadata "
                    "(%r, mean=%r, sum=%r) does not match the live contract "
                    "(%r, mean=%r, sum=False); historical values were not relabelled",
                    statistic_id,
                    existing.get("statistics_unit_of_measurement"),
                    existing_mean_type,
                    existing.get("has_sum"),
                    currency,
                    intended_mean_type,
                )
                return

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

    points = _extract_points(response, coordinator.portfolio_start_of_day)
    if not points:
        _LOGGER.debug("No portfolio value history points returned; nothing to backfill")
        return

    # Compare in the portfolio timezone used to anchor date-only source data.
    # HA's timezone can differ from the portfolio's reporting timezone.
    today = coordinator.current_date
    portfolio_timezone = coordinator.portfolio_start_of_day(today).tzinfo
    statistics_by_hour: dict[datetime, StatisticData] = {}
    for when, value in sorted(points, key=lambda item: item[0]):
        if when.astimezone(portfolio_timezone).date() >= today:
            # Leave today/forward to the recorder's live compilation.
            continue
        hour = when.replace(minute=0, second=0, microsecond=0)
        # Some response variants repeat a day/hour.  Recorder's import API
        # upserts one row per hour; collapse duplicates deterministically so a
        # single queued job never contains competing values for the same key.
        statistics_by_hour[hour] = StatisticData(start=hour, mean=value, min=value, max=value)

    if not statistics_by_hour:
        _LOGGER.debug("Value history had no pre-today points to backfill")
        return

    first_hour = min(statistics_by_hour)
    last_hour = max(statistics_by_hour)
    try:
        existing_rows = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            first_hour,
            last_hour + timedelta(hours=1),
            {statistic_id},
            "hour",
            None,
            {"mean", "min", "max"},
        )
    except (HomeAssistantError, RuntimeError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Cannot inspect existing rows for %s; skipping backfill without "
            "overwriting history: %s",
            statistic_id,
            err,
        )
        return
    existing_hours = {
        datetime.fromtimestamp(float(row["start"]), tz=UTC)
        for row in existing_rows.get(statistic_id, [])
        if row.get("start") is not None
    }
    statistics = [
        statistic for hour, statistic in statistics_by_hour.items() if hour not in existing_hours
    ]

    if not statistics:
        _LOGGER.debug(
            "Value history had no missing pre-today points to backfill into %s",
            statistic_id,
        )
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
    try:
        async_import_statistics(hass, metadata, statistics)
    except (HomeAssistantError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Recorder rejected the Sharesight value backfill for %s: %s",
            statistic_id,
            err,
        )
        return
    _LOGGER.info(
        "Backfilled %s long-term statistics points into %s",
        len(statistics),
        statistic_id,
    )
