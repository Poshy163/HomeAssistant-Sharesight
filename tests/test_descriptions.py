"""Structural checks over every sensor description.

With ~390 descriptions spread across twenty lists, the failure mode is not a
subtle logic bug but a typo: a translation key with no string, a duplicate
unique-id recipe, a state class Home Assistant will reject.  These assertions
are cheap and catch all of it at commit time.

They replace the old ``scripts/check_stats.py``, which had to be remembered
and run by hand.
"""

from __future__ import annotations

import json
import pathlib

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
import pytest

from custom_components.sharesight import enum

COMPONENT = pathlib.Path(__file__).parent.parent / "custom_components" / "sharesight"

#: Every description list the platform actually instantiates.
DESCRIPTION_LISTS = {
    name: value
    for name, value in vars(enum).items()
    if name.endswith("DESCRIPTIONS") and isinstance(value, list)
}

#: Descriptions where a unitless MEASUREMENT is correct: these are genuine
#: dimensionless ratios, not counts of something.
UNITLESS_RATIOS = frozenset(
    {
        "concentration_hhi",
        "weighted_p_e",
        "holding_pe_ratio",
        "benchmark_return_over_drawdown",
    }
)

#: Device groups the sensor platform knows how to build a device for.
KNOWN_DEVICE_GROUPS = frozenset(
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
        "analytics",
        "totals",
        "labels",
        "market",
        "cash",
        "holding",
        "extended",
    }
)

EXTENDED_PERIOD_KEYS = frozenset(
    {"three-month", "six-month", "one-year", "three-year", "five-year"}
)
EXTENDED_METRIC_KEYS = frozenset(
    {
        "total_gain",
        "total_gain_percent",
        "capital_gain",
        "capital_gain_percent",
        "payout_gain",
        "payout_gain_percent",
    }
)
EXTENDED_TRANSLATION_KEYS = frozenset(f"extended_{metric}" for metric in EXTENDED_METRIC_KEYS)


def all_descriptions():
    seen = set()
    for name, descriptions in DESCRIPTION_LISTS.items():
        for description in descriptions:
            # ALL_HOLDING_DESCRIPTIONS is a concatenation of the others.
            if id(description) in seen:
                continue
            seen.add(id(description))
            yield name, description


@pytest.fixture(name="strings", scope="module")
def strings_fixture() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))


@pytest.fixture(name="icons", scope="module")
def icons_fixture() -> dict:
    return json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))


def test_every_description_has_a_translation(strings) -> None:
    entity_strings = strings["entity"]["sensor"]
    missing = sorted(
        {
            description.translation_key
            for _, description in all_descriptions()
            if description.translation_key not in entity_strings
        }
    )
    assert missing == []


def test_every_description_has_an_icon(icons) -> None:
    entity_icons = icons["entity"]["sensor"]
    missing = sorted(
        {
            description.translation_key
            for _, description in all_descriptions()
            if description.translation_key not in entity_icons
        }
    )
    assert missing == []


def test_no_orphan_translations(strings) -> None:
    """A string with no description behind it is dead weight."""
    used = {description.translation_key for _, description in all_descriptions()}
    defined = set(strings["entity"]["sensor"])
    assert defined - used == set()


def test_translations_and_english_agree() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
    assert strings == english


def test_translation_key_reuse_is_limited_to_extended_templates(strings) -> None:
    """Five periods may share each metric's ``{period}`` name template."""
    descriptions_by_key = {}
    for _, description in all_descriptions():
        descriptions_by_key.setdefault(description.translation_key, []).append(description)

    repeated = {
        key: descriptions
        for key, descriptions in descriptions_by_key.items()
        if len(descriptions) > 1
    }
    assert set(repeated) == EXTENDED_TRANSLATION_KEYS

    entity_strings = strings["entity"]["sensor"]
    for translation_key, descriptions in repeated.items():
        metric = translation_key.removeprefix("extended_")
        assert {description.device_group for description in descriptions} == {"extended"}
        assert {description.key for description in descriptions} == {metric}
        assert {description.sub_key for description in descriptions} == EXTENDED_PERIOD_KEYS
        assert "{period}" in entity_strings[translation_key]["name"]


def test_extended_descriptions_cover_every_period_and_metric() -> None:
    recipes = {
        (description.sub_key, description.key) for description in enum.EXTENDED_SENSOR_DESCRIPTIONS
    }
    assert recipes == {
        (period, metric) for period in EXTENDED_PERIOD_KEYS for metric in EXTENDED_METRIC_KEYS
    }


def test_unique_id_recipes_do_not_collide() -> None:
    """(device_group, key, sub_key) is what the unique_id is built from."""
    recipes = [
        (description.device_group, description.key, description.sub_key)
        for _, description in all_descriptions()
    ]
    duplicates = sorted({str(recipe) for recipe in recipes if recipes.count(recipe) > 1})
    assert duplicates == []


def test_monetary_measurement_is_normalised() -> None:
    """HA rejects device_class=monetary with state_class=measurement."""
    for _, description in all_descriptions():
        if description.state_class == SensorStateClass.MEASUREMENT:
            assert description.device_class != SensorDeviceClass.MONETARY, (
                description.translation_key
            )


def test_measurement_sensors_carry_a_unit() -> None:
    """Except the handful that are genuine dimensionless ratios."""
    offenders = sorted(
        {
            description.translation_key
            for _, description in all_descriptions()
            if description.state_class == SensorStateClass.MEASUREMENT
            and description.native_unit_of_measurement is None
            and description.translation_key not in UNITLESS_RATIOS
        }
    )
    assert offenders == []


def test_text_sensors_have_no_state_class() -> None:
    """A state class on a string sensor makes the recorder complain forever."""
    for _, description in all_descriptions():
        if description.device_class in (
            SensorDeviceClass.DATE,
            SensorDeviceClass.TIMESTAMP,
        ):
            assert description.state_class is None, description.translation_key


def test_device_groups_are_known() -> None:
    unknown = sorted(
        {
            description.device_group
            for _, description in all_descriptions()
            if description.device_group not in KNOWN_DEVICE_GROUPS
        }
    )
    assert unknown == []


def test_percentage_sensors_are_measurements() -> None:
    for _, description in all_descriptions():
        if description.native_unit_of_measurement == "%":
            assert description.state_class in (
                SensorStateClass.MEASUREMENT,
                None,
            ), description.translation_key


def test_sliding_window_totals_are_measurements() -> None:
    """A figure that falls as its window slides is not an accumulator."""
    sliding = {
        "dividends_last_30_days",
        "dividends_last_12_months",
        "income_next_30_days",
        "income_next_90_days",
        "forward_annual_income",
        "announced_income_unpaid",
        "holding_ttm_income",
        "all_time_return_incl_sold",
    }
    for _, description in all_descriptions():
        if description.translation_key in sliding:
            assert description.state_class == SensorStateClass.MEASUREMENT, (
                description.translation_key
            )
