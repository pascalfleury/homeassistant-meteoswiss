"""Support for the MeteoSwiss service."""

from __future__ import annotations

import datetime
import logging
from typing import Any, cast

from hamsclientfork.client import CurrentCondition, DayForecast, HourlyForecast
from homeassistant.components.weather import (
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_NATIVE_PRECIPITATION,
    ATTR_FORECAST_NATIVE_TEMP,
    ATTR_FORECAST_NATIVE_TEMP_LOW,
    ATTR_FORECAST_TIME,
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.meteoswiss import (
    MeteoSwissClientResult,
    MeteoSwissDataUpdateCoordinator,
)
from custom_components.meteoswiss.const import (
    CODE_TO_CONDITION_MAP,
    CONF_FORECAST_NAME,
    CONF_POSTCODE,
    CONF_STATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up weather entity."""
    _LOGGER.debug("Add a MeteoSwiss weather entity from a config_entry")
    c: MeteoSwissDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MeteoSwissWeather(entry.entry_id, c)], True)


def condition_name_to_value(
    condition: None | list[CurrentCondition], name: str
) -> float | None:
    if not condition:
        # Real-time weather station provides no data.
        return None
    try:
        row = condition[0]
    except Exception:
        _LOGGER.exception("Current condition has no rows: %s", condition)
        return None
    try:
        value = row[name]  # type:ignore[literal-required]
    except Exception:
        _LOGGER.exception("Current condition has no value for %s", name)
        return None
    if value is None or value == "-":
        _LOGGER.debug(
            "Value %s of current condition is %s, so not available", name, value
        )
        return None
    try:
        return float(value)
    except Exception:
        _LOGGER.exception("Error converting %s to float", value)
        return None


class MeteoSwissWeather(
    CoordinatorEntity[MeteoSwissDataUpdateCoordinator],
    WeatherEntity,
):
    _attr_has_entity_name = True
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(
        self,
        integration_id: str,
        coordinator: MeteoSwissDataUpdateCoordinator,
    ):
        super().__init__(coordinator)
        self._attr_unique_id = "weather.%s" % integration_id
        self._attr_station = coordinator.data[CONF_STATION]
        self._attr_post_code = coordinator.data[CONF_POSTCODE]
        self.__set_data(coordinator.data)

    def __set_data(self, data: MeteoSwissClientResult) -> None:
        self._displayName = data[CONF_FORECAST_NAME]
        self._forecastData = data["forecast"]
        self._condition = data["condition"]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle data update."""
        data = self.coordinator.data
        self.__set_data(data)
        self.async_write_ha_state()

    @property
    def name(self) -> Any:
        return self._displayName

    @property
    def native_temperature(self) -> float | None:
        return condition_name_to_value(self._condition, "tre200s0")

    @property
    def native_pressure(self) -> float | None:
        return condition_name_to_value(self._condition, "prestas0")

    @property
    def pressure_qff(self) -> float | None:
        return condition_name_to_value(self._condition, "pp0qffs0")

    @property
    def pressure_qnh(self) -> float | None:
        return condition_name_to_value(self._condition, "pp0qnhs0")

    @property
    def humidity(self) -> float | None:
        return condition_name_to_value(self._condition, "ure200s0")

    @property
    def native_wind_speed(self) -> float | None:
        return condition_name_to_value(self._condition, "fu3010z0")

    @property
    def wind_bearing(self) -> float | None:
        return condition_name_to_value(self._condition, "dkl010z0")

    @property
    def condition(self) -> str | None:
        symbolId = self._forecastData["currentWeather"]["icon"]
        try:
            cond: str | None = CODE_TO_CONDITION_MAP.get(symbolId, (None, None))[0]
            if cond is None:
                _LOGGER.error(
                    "Expected a known int for the forecast icon, not None",
                    symbolId,
                )
                return STATE_UNAVAILABLE
            _LOGGER.debug(
                "Current symbol is %s, condition is: %s",
                symbolId,
                cond,
            )
            return cond
        except TypeError as exc:
            _LOGGER.error(
                "Expected an int, not %r, to decide on the forecast icon: %s",
                symbolId,
                exc,
            )
            _LOGGER.error("Forecast data: %r", self._forecastData)
            return STATE_UNAVAILABLE

    @property
    def attribution(self) -> str:
        a = "Data provided by MeteoSwiss."
        a += "  Forecasts from postal code %s." % (self._attr_post_code,)
        if self._attr_station:
            a += "  Real-time weather data from weather station %s." % (
                self._attr_station,
            )
            url = "https://rudd-o.com/meteostations"
            a += "  Stations available at %s ." % (url,)
        return a

    def _daily_forecast(self) -> list[Forecast] | None:
        fcdata_out = []
        # Skip the first element - it's the forecast for the current day
        for untyped_forecast in self._forecastData["regionForecast"]:
            try:
                forecast = cast(DayForecast, untyped_forecast)
                data_out: Forecast = {
                    ATTR_FORECAST_TIME: forecast["dayDate"],
                    ATTR_FORECAST_NATIVE_TEMP_LOW: forecast["temperatureMin"],
                    ATTR_FORECAST_NATIVE_TEMP: forecast["temperatureMax"],
                    ATTR_FORECAST_CONDITION: CODE_TO_CONDITION_MAP.get(
                        forecast["iconDay"], (None, None)
                    )[0],
                    ATTR_FORECAST_NATIVE_PRECIPITATION: forecast["precipitation"],
                }
                _LOGGER.debug("Appending daily forecast: %s", data_out)
                fcdata_out.append(data_out)
            except Exception as e:
                _LOGGER.error("Error while computing forecast: %s", e)
        return fcdata_out

    def _hourly_forecast(self) -> list[Forecast] | None:
        fcdata_out: list[Forecast] = []
        # Skip the first element - it's the forecast for the current day
        now = datetime.datetime.now(datetime.timezone.utc)
        forecast_data = cast(
            list[HourlyForecast], self._forecastData["regionHourlyForecast"]
        )
        biggers = [f["time"] > now for f in forecast_data]
        try:
            idx = biggers.index(True)
        except IndexError:
            return fcdata_out
        for forecast in forecast_data[idx - 1 :]:
            data_out: Forecast = {
                ATTR_FORECAST_TIME: forecast["time"].isoformat("T").partition("+")[0]
                + "Z",
                ATTR_FORECAST_NATIVE_TEMP_LOW: forecast["temperatureMin"],
                ATTR_FORECAST_NATIVE_TEMP: forecast["temperatureMax"],
                ATTR_FORECAST_NATIVE_PRECIPITATION: forecast["precipitationMax"],
            }
            _LOGGER.debug("Appending hourly forecast: %s", data_out)
            fcdata_out.append(data_out)
        return fcdata_out

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(self._daily_forecast())

    async def async_forecast_daily(self) -> list[Forecast]:
        """Return the daily forecast in native units."""
        return self._daily_forecast() or []

    async def async_forecast_hourly(self) -> list[Forecast]:
        """Return the hourly forecast in native units."""
        return self._hourly_forecast() or []
